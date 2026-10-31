"""Shared HTTP client: per-host pacing, retries with backoff, request counting, and secret redaction."""
from __future__ import annotations

import re
import threading
import time
from collections import Counter
from typing import Any, Callable

import httpx

RETRY_STATUSES = {429, 500, 502, 503, 504}
_SECRET = re.compile(r"(?i)\b(api[_-]?key|apikey|key|token)=([^&\s'\"]+)")


def redact(text: str) -> str:
    """Remove key values from anything that may end up in a log, an exception or the database."""
    return _SECRET.sub(lambda m: f"{m.group(1)}=***", text)


class HttpError(RuntimeError):
    def __init__(self, status: int | None, message: str):
        super().__init__(redact(message))
        self.status = status


class RateLimiter:
    def __init__(self, min_interval: float, *, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.min_interval = min_interval
        self._clock, self._sleep = clock, sleep
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            delay = self._next - now
            self._next = max(now, self._next) + self.min_interval
        if delay > 0:
            self._sleep(delay)


class HttpClient:
    def __init__(self, user_agent: str, *, transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] = time.sleep,
                 timeout: float = 60.0, max_tries: int = 4):
        self._client = httpx.Client(headers={"User-Agent": user_agent}, transport=transport, timeout=timeout, follow_redirects=True)
        self._sleep = sleep
        self._max_tries = max_tries
        self._limiters: dict[str, RateLimiter] = {}
        self._lock = threading.Lock()
        self._secrets: set[str] = set()
        self.requests: Counter[str] = Counter()
        self.last_headers: dict[str, dict[str, str]] = {}

    def _learn(self, headers: dict[str, str] | None, params: dict[str, Any] | None) -> None:
        """Remember every credential this client sends, so the exact value can be scrubbed from any error text a server echoes back."""
        for k, v in (headers or {}).items():
            if v and any(w in k.lower() for w in ("authorization", "key", "token")):
                self._secrets.add(v)
                self._secrets.add(v.removeprefix("Bearer ").strip())
        for k, v in (params or {}).items():
            if v and any(w in k.lower() for w in ("key", "token")):
                self._secrets.add(str(v))
        self._secrets.discard("")

    def _scrub(self, text: str) -> str:
        for secret in sorted(self._secrets, key=len, reverse=True):
            if len(secret) >= 6:
                text = text.replace(secret, "***")
        return redact(text)

    def limiter(self, name: str, min_interval: float) -> RateLimiter:
        with self._lock:
            if name not in self._limiters:
                self._limiters[name] = RateLimiter(min_interval, sleep=self._sleep)
            return self._limiters[name]

    def get(self, url: str, params: dict[str, Any] | None = None, *, host: str, min_interval: float = 0.2,
            headers: dict[str, str] | None = None) -> httpx.Response:
        """GET with pacing and retries. `headers` (credentials) are sent only to the original host: a redirect to another host drops them."""
        self._learn(headers, params)
        return self._with_retries(lambda: self._follow(url, params, headers), host, min_interval)

    def post_json(self, url: str, payload: dict[str, Any], *, host: str, min_interval: float = 0.2, headers: dict[str, str] | None = None) -> httpx.Response:
        """POST a JSON body with the same pacing, retry and Retry-After handling as `get`. Redirects are not followed (credentials stay on one host)."""
        self._learn(headers, None)
        return self._with_retries(lambda: self._client.post(url, json=payload, headers=headers or {}, follow_redirects=False), host, min_interval)

    def _with_retries(self, send, host: str, min_interval: float) -> httpx.Response:
        limiter = self.limiter(host, min_interval)
        last_err = "no attempt"
        for attempt in range(self._max_tries):
            limiter.wait()
            self.requests[host] += 1
            try:
                resp = send()
            except httpx.TransportError as exc:
                last_err = self._scrub(f"{type(exc).__name__}: {exc}")
                resp = None
            if resp is not None:
                self.last_headers[host] = dict(resp.headers)
                if resp.status_code < 400:
                    return resp
                last_err = self._scrub(f"HTTP {resp.status_code}: {resp.text[:200]}")
                if resp.status_code not in RETRY_STATUSES or _plan_limited(resp):
                    raise HttpError(resp.status_code, f"{host} {last_err}")
                retry_after = _retry_after(resp)
            else:
                retry_after = None
            if attempt < self._max_tries - 1:
                self._sleep(min(retry_after if retry_after is not None else 2 ** (attempt + 1), 60))
        raise HttpError(None, f"{host} failed after {self._max_tries} tries: {last_err}")

    def _follow(self, url: str, params: dict[str, Any] | None, headers: dict[str, str] | None, max_redirects: int = 5) -> httpx.Response:
        origin = httpx.URL(url).host
        current, current_params, current_headers = url, params, dict(headers or {})
        for _ in range(max_redirects + 1):
            resp = self._client.get(current, params=current_params, headers=current_headers, follow_redirects=False)
            if resp.status_code not in (301, 302, 303, 307, 308) or "location" not in resp.headers:
                return resp
            nxt = resp.url.join(resp.headers["location"])
            if nxt.host != origin:
                current_headers = {}  # never forward credentials to another host
            current, current_params = str(nxt), None
        raise httpx.TooManyRedirects("too many redirects", request=resp.request)

    def get_bytes(self, url: str, params: dict[str, Any] | None = None, **kw) -> bytes:
        return self.get(url, params, **kw).content

    def get_json(self, url: str, params: dict[str, Any] | None = None, **kw) -> Any:
        return self.get(url, params, **kw).json()

    def get_text(self, url: str, params: dict[str, Any] | None = None, **kw) -> str:
        return self.get(url, params, **kw).text

    def close(self) -> None:
        self._client.close()


def _plan_limited(resp: httpx.Response) -> bool:
    """A 429 that says the plan lacks the feature will not clear by waiting, so do not retry it."""
    return resp.status_code == 429 and any(w in resp.text.lower() for w in ("plan upgrade", "upgrade required", "upgrade your plan"))


def _retry_after(resp: httpx.Response) -> float | None:
    v = resp.headers.get("Retry-After")
    try:
        return float(v) if v else None
    except ValueError:
        return None
