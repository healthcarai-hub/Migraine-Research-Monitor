"""Summaries through any OpenAI-compatible chat endpoint (Groq in the earlier newsletter agent). The model is untrusted: every answer is checked
against the abstract, and anything that fails falls back to an extract of the abstract itself."""
from __future__ import annotations

import json
import logging
import re
import time

from ..domain.summary import SummaryRequest, SummaryResult, build_messages, check_summary
from .http import HttpClient, HttpError, redact

log = logging.getLogger(__name__)


_DUR = re.compile(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?!s))?(?:(\d+(?:\.\d+)?)s)?(?:(\d+(?:\.\d+)?)ms)?$")


def parse_duration(text: str | None) -> float | None:
    """'22.8s' -> 22.8, '1m2.5s' -> 62.5, '15m50.4s', '2h1m', '300ms'. None when it is not a duration."""
    m = _DUR.match((text or "").strip())
    if not m or not any(m.groups()):
        return None
    h, mi, sec, ms = (float(x) if x else 0.0 for x in m.groups())
    return h * 3600 + mi * 60 + sec + ms / 1000


class _Truncated(Exception):
    pass


def extract_summary(raw: str) -> str:
    """The summary text from a model answer: strict JSON, JSON wrapped in other text or a code fence, or (last resort) the plain text itself.
    Whatever comes out still has to pass the same checks against the abstract."""
    text = raw.strip()
    for candidate in (text, *re.findall(r"\{.*\}", text, re.S)):
        try:
            value = json.loads(candidate)
            if isinstance(value, dict) and "summary" in value:
                return str(value["summary"]).strip()
        except ValueError:
            continue
    if "{" in text or "}" in text:
        raise ValueError("no summary field in the answer")
    return text.strip().strip('"')


class ChatSummariser:
    name = "llm"

    def __init__(self, http: HttpClient, *, base_url: str, api_key: str, model: str, field_name: str, max_words: int = 60,
                 min_interval: float = 2.1, token_reserve: int = 1500, max_tokens: int = 400, reasoning_effort: str | None = None, sleep=time.sleep):
        self.http, self.base_url, self.api_key, self.model = http, base_url.rstrip("/"), api_key, model
        self.token_reserve, self.max_tokens, self._sleep, self.reasoning_effort = token_reserve, max_tokens, sleep, reasoning_effort
        self.field_name, self.max_words, self.min_interval = field_name, max_words, min_interval
        self._json_mode = True   # some models reject response_format; switch it off once and remember

    def _complete(self, messages: list[dict]) -> str:
        payload = {"model": self.model, "temperature": 0.1, "max_tokens": self.max_tokens, "messages": messages}
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort   # for reasoning models (e.g. low / medium / high); omitted when not set
        if self._json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            resp = self.http.post_json(f"{self.base_url}/chat/completions", payload, host="llm", min_interval=self.min_interval,
                                       headers={"Authorization": f"Bearer {self.api_key}"})
        except HttpError as exc:
            if exc.status == 400 and self._json_mode:
                self._json_mode = False
                return self._complete(messages)
            raise
        self._respect_token_limit()
        choice = resp.json()["choices"][0]
        content = choice["message"].get("content") or ""
        if not content.strip() and choice.get("finish_reason") == "length":
            raise _Truncated()   # a reasoning model spent the whole budget thinking
        return content

    def _respect_token_limit(self) -> None:
        """Groq reports the tokens left in the current minute. Wait for the reset when too few remain for the next request, instead of sending it and
        being refused (a free key can be limited to a few thousand tokens a minute, far below the published table)."""
        h = {k.lower(): v for k, v in self.http.last_headers.get("llm", {}).items()}
        try:
            remaining = int(h.get("x-ratelimit-remaining-tokens", ""))
        except ValueError:
            return
        if remaining < self.token_reserve:
            wait = parse_duration(h.get("x-ratelimit-reset-tokens"))
            if wait:
                log.info("llm token budget low (%s left): waiting %.1fs for the reset", remaining, wait)
                self._sleep(min(wait + 0.5, 65))

    def summarise(self, req: SummaryRequest) -> SummaryResult:
        """One summary, or a result with source 'failed' and the reason. Never raises for model or network problems."""
        messages = build_messages(req.title, req.abstract, field_name=self.field_name, max_words=self.max_words)
        reason = "no attempt"
        for _ in range(3):   # a retry when the answer fails the checks, and one more when the token budget had to be raised
            try:
                text = extract_summary(self._complete(messages))
            except _Truncated:
                self.max_tokens = min(self.max_tokens * 4, 3200)   # remembered, so the next paper starts with enough room
                reason = "answer cut off: raised the token budget"
                continue
            except HttpError as exc:
                return SummaryResult("", "failed", self.model, redact(str(exc))[:200])
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                reason = f"unreadable answer: {type(exc).__name__}"
                continue
            reason = check_summary(text, req.abstract, req.title, max_words=self.max_words) or ""
            if not reason:
                return SummaryResult(text, "llm", self.model)
        return SummaryResult("", "failed", self.model, reason)
