"""Ports: what the application layer needs from the outside world."""
from __future__ import annotations

from typing import Protocol

from dataclasses import dataclass

from .domain.capabilities import ProviderInfo, Tier
from .domain.fulltext import FullTextRequest, FullTextResult
from .domain.models import DateWindow, PaperRecord, SearchSpec, SourceResult


class DiscoverySource(Protocol):
    """One external index. `run` must never raise for source failures: it returns a SourceResult with `error` set."""

    name: str

    def run(self, spec: SearchSpec, window: DateWindow) -> SourceResult: ...


class RecordEnricher(Protocol):
    """Looks papers up by identifier and returns records carrying extra facts (retraction, licence, links, abstract)."""

    name: str

    def lookup(self, dois: list[str]) -> tuple[list[PaperRecord], str | None]: ...


class FullTextProvider(Protocol):
    """One route to a paper's text. The router tries active providers in order until one returns text.

    A provider never raises for ordinary failures: it returns a FullTextResult whose `outcome` says what happened.
    """

    name: str
    tier: "Tier"
    priority: int  # lower is tried first

    def info(self) -> "ProviderInfo": ...

    def active(self) -> bool: ...

    def supports(self, req: "FullTextRequest") -> bool: ...  # cheap, no network: can this provider possibly serve this paper?

    def fetch(self, req: "FullTextRequest") -> "FullTextResult": ...


@dataclass
class RenderedIssue:
    subject: str
    html: str
    text: str


class NewsletterRenderer(Protocol):
    def render(self, **context) -> RenderedIssue: ...


class DraftPublisher(Protocol):
    """Puts a built issue where a person can review and send it. Never sends mail itself."""

    name: str

    def publish(self, subject: str, html: str, text: str, issue_key: str, recipients: list[str], existing_ref: str | None = None) -> str: ...
