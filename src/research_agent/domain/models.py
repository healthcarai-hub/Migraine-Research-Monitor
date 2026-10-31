"""Plain data types shared by every layer. No I/O and nothing field-specific here."""
from __future__ import annotations

import hashlib
import html
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .text_repair import repair_abstract


@dataclass(frozen=True)
class DateWindow:
    """Inclusive calendar window. `rolling(today, 3)` = today and the 3 days before it (4 calendar days)."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError("window start is after end")

    @classmethod
    def rolling(cls, end: date, overlap_days: int) -> "DateWindow":
        return cls(end - timedelta(days=overlap_days), end)

    def widen_back(self, days: int) -> "DateWindow":
        return DateWindow(self.start - timedelta(days=days), self.end)

    def contains(self, d: date | None) -> bool:
        return d is not None and self.start <= d <= self.end

    def __str__(self) -> str:
        return f"{self.start.isoformat()}..{self.end.isoformat()}"


@dataclass
class Author:
    family: str = ""
    given: str = ""
    initials: str = ""
    orcid: str | None = None

    def display(self) -> str:
        return " ".join(p for p in (self.given or self.initials, self.family) if p)


@dataclass
class Link:
    kind: str  # doi | article | pdf | pmc | europepmc | openalex | preprint | trial
    url: str
    source: str
    is_free: bool | None = None
    licence: str | None = None


@dataclass
class PaperRecord:
    """One source's view of one paper, normalised. The pipeline merges records into `papers` rows."""

    source: str  # pubmed | europepmc | crossref | openalex | ctgov
    source_id: str
    title: str = ""
    abstract: str = ""
    doi: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    nct_id: str | None = None
    journal: str | None = None
    issns: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)  # ISO 639-2/1 as the source reports; [] = unknown
    pub_types: list[str] = field(default_factory=list)
    authors: list[Author] = field(default_factory=list)
    published_date: date | None = None  # best known publication date
    entry_date: date | None = None  # date this source first indexed it (the filter field)
    url: str | None = None
    is_preprint: bool = False
    is_trial: bool = False
    retracted: bool = False
    retraction_notice: bool = False
    retraction_target_doi: str | None = None  # set on notices
    retraction_target_pmid: str | None = None
    licences: list[str] = field(default_factory=list)
    oa_status: str | None = None
    links: list[Link] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.journal:
            self.journal = html.unescape(self.journal)         # some sources send 'Research &amp; Yoga'; store the real ampersand
        self.abstract = repair_abstract(self.abstract)     # every source passes through here, so glued words ('ObjectiveTo', 'not.The') are repaired once, before the gate sees the text


@dataclass(frozen=True)
class SearchSpec:
    """One concrete query against one source. Identity (`key`) ignores the date window so history is comparable."""

    path: str  # topic | journal | researcher | trial | preprint | bridge
    source: str
    label: str
    params: dict[str, Any]
    gated: bool = True  # the query itself already requires the field's gate term
    min_expected: int | None = None  # a broad query returning fewer than this raises the silent-zero alarm
    confidence: str = "query"  # how sure we are a hit is about the intended entity: id | name | name_ambiguous | query
    meta: dict[str, Any] = field(default_factory=dict, compare=False)  # entity_type / entity_key / track; not part of identity

    @property
    def key(self) -> str:
        blob = json.dumps({"p": self.path, "s": self.source, "q": self.params}, sort_keys=True, default=str)
        return hashlib.sha1(blob.encode()).hexdigest()[:20]


@dataclass
class SourceResult:
    spec: SearchSpec
    records: list[PaperRecord] = field(default_factory=list)
    total_reported: int | None = None  # the source's own hit count, to detect truncation
    requests: int = 0
    error: str | None = None
    skipped_reason: str | None = None
    notes: list[str] = field(default_factory=list)  # non-fatal events worth keeping in the run log (a premium feature degraded, for example)

    @property
    def status(self) -> str:
        if self.error:
            return "error"
        if self.skipped_reason:
            return "skipped"
        if self.spec.min_expected is not None and len(self.records) < self.spec.min_expected:
            return "suspect_zero"
        if self.total_reported is not None and self.total_reported > len(self.records):
            return "truncated"
        return "ok"
