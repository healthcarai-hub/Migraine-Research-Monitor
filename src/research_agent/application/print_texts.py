"""Texts for the printed edition: written by the model from each paper's abstract, checked against it, cached, and never invented.

The cache is the same table as the newsletter summaries (`paper_summaries`), told apart by the prompt version (`print-high-v1`, `print-other-v1`), so
rebuilding an edition never calls the model again. A paper that fails the checks, or any paper when no model is configured, is simply absent from the
result: the edition builder then uses an excerpt of the abstract instead, and says so in the stored text's `source`.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..domain.print_edition import PRINT_PROMPT_VERSIONS, PrintRequest, PrintText
from ..domain.summary import abstract_hash
from ..infrastructure.db import models as m
from ..infrastructure.db.session import session_scope

log = logging.getLogger(__name__)


class PrintTextService:
    """`llm` is optional (an object with `write(request)` and `synthesise(papers)`, see infrastructure/llm.py). Without it nothing is generated."""

    def __init__(self, factory: sessionmaker[Session], llm=None, *, max_per_run: int = 300):
        self.factory, self.llm, self.max_per_run = factory, llm, max_per_run

    @property
    def model(self) -> str | None:
        return getattr(self.llm, "model", None)

    def ensure(self, requests: list[PrintRequest]) -> dict[int, PrintText]:
        """A printed text per paper that has an abstract and for which the model produced an acceptable one (cached ones included)."""
        out: dict[int, PrintText] = {}
        if self.llm is None:
            return out
        wanted = [r for r in requests if r.abstract.strip()]
        if not wanted:
            return out
        with session_scope(self.factory) as s:
            cache = {(r.paper_id, r.prompt_version, r.abstract_hash): r.text for r in s.scalars(
                select(m.PaperSummary).where(m.PaperSummary.model == self.model, m.PaperSummary.prompt_version.in_(set(PRINT_PROMPT_VERSIONS.values())),
                                             m.PaperSummary.paper_id.in_([r.paper_id for r in wanted])))}
        budget, cached, written, failed = self.max_per_run, 0, 0, 0
        for req in wanted:
            version, h = PRINT_PROMPT_VERSIONS[req.tier], abstract_hash(req.title, req.abstract)
            hit = cache.get((req.paper_id, version, h))
            if hit:
                try:
                    out[req.paper_id] = PrintText.from_dict(json.loads(hit))
                    cached += 1
                    continue
                except ValueError:
                    pass                      # an unreadable cache row is simply written again below
            if budget <= 0:
                failed += 1
                continue
            budget -= 1
            text, reason = self.llm.write(req)
            if text is None:
                failed += 1
                log.warning("print text for paper %s not used: %s", req.paper_id, reason)
                continue
            text.model = self.model
            with session_scope(self.factory) as s:    # saved as each one is produced, so a failed run keeps its work
                s.add(m.PaperSummary(paper_id=req.paper_id, model=self.model or "", prompt_version=version, abstract_hash=h, text=json.dumps(text.to_dict())))
            out[req.paper_id] = text
            written += 1
        log.info("print texts: %d written, %d from cache, %d not used (an excerpt of the abstract is printed instead)", written, cached, failed)
        return out

    def synthesise(self, papers: list[dict]) -> dict | None:
        """The cover theme and the closing significance and assessment, from the High papers' checked texts; None if there is no model or it failed the checks."""
        if self.llm is None or not papers:
            return None
        result, reason = self.llm.synthesise(papers)
        if result is None:
            log.warning("edition notes not written: %s", reason)
        return result
