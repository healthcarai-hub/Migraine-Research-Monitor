"""Summaries for the newsletter: cached model-written ones where possible, abstract extracts otherwise."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..domain.newsletter import summarise as extract
from ..domain.summary import PROMPT_VERSION, SummaryRequest, SummaryResult, abstract_hash
from ..infrastructure.db import models as m
from ..infrastructure.db.session import session_scope

log = logging.getLogger(__name__)


@dataclass
class SummaryReport:
    cached: int = 0
    generated: int = 0
    failed: int = 0
    skipped_over_cap: int = 0
    reasons: list[str] = field(default_factory=list)


class SummaryService:
    """`llm` is optional. Without it every summary is an extract of the abstract and nothing is cached."""

    def __init__(self, factory: sessionmaker[Session], llm=None, *, max_per_run: int = 300):
        self.factory, self.llm, self.max_per_run = factory, llm, max_per_run

    @property
    def model(self) -> str | None:
        return getattr(self.llm, "model", None)

    def ensure(self, requests: list[SummaryRequest]) -> tuple[dict[int, SummaryResult], SummaryReport]:
        """Return a summary-or-None per paper id. Model-written summaries are cached in the database as they are produced, so a failed run keeps its work."""
        report, out = SummaryReport(), {}
        if self.llm is None:
            return out, report
        budget = self.max_per_run
        with session_scope(self.factory) as s:
            cache = {(r.paper_id, r.abstract_hash): r.text for r in s.scalars(
                select(m.PaperSummary).where(m.PaperSummary.model == self.model, m.PaperSummary.prompt_version == PROMPT_VERSION,
                                             m.PaperSummary.paper_id.in_([r.paper_id for r in requests] or [0])))}
        for req in requests:
            if not req.abstract.strip():
                continue
            h = abstract_hash(req.title, req.abstract)
            if (req.paper_id, h) in cache:
                out[req.paper_id] = SummaryResult(cache[(req.paper_id, h)], "llm", self.model, "cached")
                report.cached += 1
                continue
            if budget <= 0:
                report.skipped_over_cap += 1
                continue
            budget -= 1
            res = self.llm.summarise(req)
            if res.source != "llm":
                report.failed += 1
                if len(report.reasons) < 5:
                    report.reasons.append(f"paper {req.paper_id}: {res.detail}")
                continue
            with session_scope(self.factory) as s:
                s.add(m.PaperSummary(paper_id=req.paper_id, model=self.model, prompt_version=PROMPT_VERSION, abstract_hash=h, text=res.text))
            out[req.paper_id] = res
            report.generated += 1
        return out, report


def final_summary(result: SummaryResult | None, abstract: str, *, sentences: int, max_chars: int) -> SummaryResult:
    """The model's summary if there is one, else an extract of the abstract."""
    if result is not None and result.source == "llm":
        return result
    return SummaryResult(extract(abstract, max_sentences=sentences, max_chars=max_chars), "extractive")
