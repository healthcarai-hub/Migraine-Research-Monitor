"""A small in-process request limiter for the API: at most `limit` events per client in any `window` seconds.

It lives in the memory of one server process, which is right for the single-process deployment this API is built for. Running several workers
would give each its own count, so put a shared limiter (a reverse proxy or gateway) in front instead. A limit of 0 switches it off.
"""
from __future__ import annotations

import time
from collections import deque


class SlidingWindow:
    def __init__(self, limit: int, window: float = 60.0, clock=time.monotonic, max_keys: int = 10_000):
        self.limit, self.window, self.clock, self.max_keys = limit, window, clock, max_keys
        self._hits: dict[str, deque[float]] = {}

    def _recent(self, key: str, now: float) -> deque[float]:
        q = self._hits.setdefault(key, deque())
        while q and q[0] <= now - self.window:
            q.popleft()
        return q

    def _bound_memory(self, now: float) -> None:
        if len(self._hits) <= self.max_keys:
            return
        for k in [k for k in self._hits if not self._recent(k, now)]:
            del self._hits[k]
        if len(self._hits) > self.max_keys:     # still too many distinct clients: start over rather than grow without limit
            self._hits.clear()

    def wait(self, key: str) -> float:
        """Seconds until `key` may act again, 0.0 if it may now. Records nothing."""
        if self.limit <= 0:
            return 0.0
        now = self.clock()
        q = self._recent(key, now)
        return 0.0 if len(q) < self.limit else max(q[0] + self.window - now, 0.001)

    def record(self, key: str) -> None:
        if self.limit > 0:
            now = self.clock()
            self._bound_memory(now)
            self._recent(key, now).append(now)

    def check(self, key: str) -> float:
        """Count one event for `key` unless it is over the limit. Returns 0.0 if allowed, else the seconds to wait."""
        w = self.wait(key)
        if not w:
            self.record(key)
        return w
