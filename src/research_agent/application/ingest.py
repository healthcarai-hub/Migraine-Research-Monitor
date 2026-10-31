"""The daily collection run: plan -> discover (sources in parallel, isolated) -> gate -> dedupe/merge -> match -> enrich -> flag -> log."""
from __future__ import annotations

import logging
import statistics
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ..domain.field_config import FieldConfig, ProfileConfig, selected_subjects
from ..domain.gate import FieldMatcher
from ..domain.models import DateWindow, PaperRecord, SearchSpec, SourceResult
from ..domain.retraction import apply_title_signals
from ..infrastructure.db import models as m
from ..infrastructure.db.repository import Repository
from ..infrastructure.db.session import session_scope
from ..ports import DiscoverySource, RecordEnricher
from .matching import ProfileMatcher
from .planner import build_specs

log = logging.getLogger(__name__)


@dataclass
class RunSummary:
    run_id: int
    status: str
    window: DateWindow
    specs: int = 0
    records: int = 0
    rejected_by_gate: int = 0
    new_papers: int = 0
    new_matches: int = 0
    passed_matches: int = 0
    errors: list[str] = field(default_factory=list)
    suspect: list[str] = field(default_factory=list)
    by_source: dict[str, dict] = field(default_factory=dict)
    enrichment: dict[str, int] = field(default_factory=dict)
    duration_s: float = 0.0


class IngestPipeline:
    def __init__(self, session_factory: sessionmaker[Session], sources: dict[str, DiscoverySource], enrichers: dict[str, RecordEnricher] | None = None,
                 *, clock: Callable[[], float] = time.monotonic):
        self.factory, self.sources, self.enrichers, self.clock = session_factory, sources, enrichers or {}, clock

    # ---------------------------------------------------------------- public
    def run(self, profile: ProfileConfig, field_cfg: FieldConfig, window: DateWindow, *, trigger: str = "manual",
            only_sources: set[str] | None = None, only_paths: set[str] | None = None, enrich: bool = True) -> RunSummary:
        t0 = self.clock()
        specs = [s for s in build_specs(profile, field_cfg, sources=only_sources, paths=only_paths) if s.source in self.sources]
        with session_scope(self.factory) as s:
            repo = Repository(s)
            repo.sync_config(profile, field_cfg)
            repo.close_stale_runs()
            if not only_sources and not only_paths:
                repo.retire_specs_not_in(profile.id, {sp.key for sp in build_specs(profile, field_cfg)})
            rows = {sp.key: repo.upsert_spec(profile.id, sp) for sp in specs}
            run = repo.start_run(profile.id, window.start, window.end, trigger)
            run_id = run.id
            spec_ids = {k: r.id for k, r in rows.items()}
        summary = RunSummary(run_id=run_id, status="running", window=window, specs=len(specs))

        results = self._discover(specs, window)  # network only; no database access in worker threads

        with session_scope(self.factory) as s:
            repo = Repository(s)
            run = s.get(m.Run, run_id)
            self._record_results(repo, run, profile, field_cfg, results, spec_ids, summary)
            if enrich:
                self._enrich(repo, run, profile, field_cfg, summary)
            repo.resolve_retraction_targets()
            summary.passed_matches = self._count_passed(repo, run_id, profile.id)
            summary.duration_s = round(self.clock() - t0, 2)
            summary.status = self._status(summary, results)
            repo.finish_run(run, summary.status, self._stats(summary))
        return summary

    @staticmethod
    def _count_passed(repo: Repository, run_id: int, profile_id: str) -> int:
        ids = select(m.PaperHit.paper_id).where(m.PaperHit.run_id == run_id).distinct()
        return repo.s.scalar(select(func.count()).select_from(m.ProfileMatch).where(
            m.ProfileMatch.profile_id == profile_id, m.ProfileMatch.paper_id.in_(ids), m.ProfileMatch.gate_status == "passed")) or 0

    @staticmethod
    def _status(summary: RunSummary, results: list[tuple[SearchSpec, SourceResult, int]]) -> str:
        """failed: nothing worked; partial: something needs attention (errors, suspect zeros, truncation); ok otherwise."""
        real = [r for _, r, _ in results if not r.skipped_reason]
        if not real or all(r.error for r in real):
            return "failed"
        if summary.errors or summary.suspect or any(r.status == "truncated" for r in real):
            return "partial"
        return "ok"

    @staticmethod
    def _stats(summary: RunSummary) -> dict:
        return {"specs": summary.specs, "records": summary.records, "rejected_by_gate": summary.rejected_by_gate, "new_papers": summary.new_papers,
                "new_matches": summary.new_matches, "passed_matches": summary.passed_matches, "errors": summary.errors[:50], "suspect": summary.suspect,
                "by_source": summary.by_source, "enrichment": summary.enrichment, "duration_s": summary.duration_s}

    # ---------------------------------------------------------------- discovery
    def _discover(self, specs: list[SearchSpec], window: DateWindow) -> list[tuple[SearchSpec, SourceResult, int]]:
        by_source: dict[str, list[SearchSpec]] = defaultdict(list)
        for sp in specs:
            by_source[sp.source].append(sp)

        def work(source_name: str, group: list[SearchSpec]) -> list[tuple[SearchSpec, SourceResult, int]]:
            out = []
            for sp in group:
                t = self.clock()
                try:
                    res = self.sources[source_name].run(sp, window)
                except Exception as exc:  # an adapter bug must not take the other sources down
                    res = SourceResult(sp, error=f"adapter crashed: {type(exc).__name__}: {str(exc)[:200]}")
                out.append((sp, res, int((self.clock() - t) * 1000)))
            return out

        if not by_source:
            return []
        with ThreadPoolExecutor(max_workers=len(by_source)) as pool:
            futures = [pool.submit(work, name, group) for name, group in by_source.items()]
            return [item for f in futures for item in f.result()]

    # ---------------------------------------------------------------- merge + match
    def _record_results(self, repo: Repository, run: m.Run, profile: ProfileConfig, field_cfg: FieldConfig,
                        results: list[tuple[SearchSpec, SourceResult, int]], spec_ids: dict[str, int], summary: RunSummary) -> None:
        matcher = FieldMatcher(field_cfg, selected_subjects(profile, field_cfg))
        pmatch = ProfileMatcher(profile, field_cfg, matcher)
        touched: dict[int, m.Paper] = {}
        per_source: dict[str, Counter] = defaultdict(Counter)
        for spec, res, ms in results:
            spec_row = repo.s.get(m.SearchSpecRow, spec_ids[spec.key])
            n_new = rejected = 0
            for rec in res.records:
                apply_title_signals(rec)
                verdict = matcher.evaluate_gate(rec, source_gated=spec.gated)
                if verdict.status == "failed":
                    rejected += 1
                    continue
                outcome = repo.merge_record(rec)
                repo.add_hit(outcome.paper, run, spec_row, spec, rec, fresh=outcome.created)
                touched[outcome.paper.id] = outcome.paper
                n_new += outcome.created
            status = self._spec_status(repo, spec_row, res, run.id)
            note = f"{rejected} rejected by gate" if rejected else None
            repo.log_source(run, spec_row, res, status, n_new, ms, note)
            summary.records += len(res.records)
            summary.rejected_by_gate += rejected
            summary.new_papers += n_new
            c = per_source[spec.source]
            c["specs"] += 1; c["records"] += len(res.records); c["new"] += n_new; c["requests"] += res.requests
            c[status] += 1
            if res.error:
                summary.errors.append(f"{spec.source}/{spec.path}/{spec.label[:40]}: {res.error[:160]}")
            if status == "suspect_zero":
                summary.suspect.append(f"{spec.source}/{spec.path}/{spec.label[:40]}")
            repo.s.commit()      # progress is kept even if the job is stopped part-way; later steps redo matching for these papers
        summary.by_source = {k: dict(v) for k, v in per_source.items()}
        repo.s.flush()
        self._match_all(repo, pmatch, profile.id, touched.values(), summary)

    def _match_all(self, repo: Repository, pmatch: ProfileMatcher, profile_id: str, papers, summary: RunSummary) -> None:
        papers = list(papers)
        ids = [p.id for p in papers]
        metas, existing = repo.hit_meta_many(ids), repo.matches_for(profile_id, ids)     # two queries for all papers, not two per paper
        for paper in papers:
            vals = pmatch.evaluate(paper, metas.get(paper.id, []))
            row, created = repo.apply_match(existing.get(paper.id), profile_id, paper.id, **vals)
            existing[paper.id] = row
            summary.new_matches += created
        repo.s.flush()
        repo.s.commit()

    def _spec_status(self, repo: Repository, spec_row: m.SearchSpecRow, res: SourceResult, run_id: int) -> str:
        status = res.status
        if status == "ok" and not res.records:
            history = repo.recent_counts(spec_row.id, limit=7, exclude_run=run_id)
            if len(history) >= 3 and statistics.median(history) >= 5:
                return "suspect_zero"  # a search that normally returns results returned none: filter or API drift
        return status

    # ---------------------------------------------------------------- enrichment
    def _enrich(self, repo: Repository, run: m.Run, profile: ProfileConfig, field_cfg: FieldConfig, summary: RunSummary) -> None:
        matcher = FieldMatcher(field_cfg, selected_subjects(profile, field_cfg))
        pmatch = ProfileMatcher(profile, field_cfg, matcher)
        stats: Counter = Counter()
        run_paper_ids = [pid for (pid,) in repo.s.execute(select(m.PaperHit.paper_id).where(m.PaperHit.run_id == run.id).distinct())]
        by_id = repo.papers_by_id(run_paper_ids)
        papers = [by_id[pid] for pid in run_paper_ids if pid in by_id]
        by_doi = {p.doi: p for p in papers if p.doi}
        matches = repo.matches_for(profile.id, run_paper_ids)                 # everything below reads these, instead of querying once per paper and step
        status_of = lambda p: matches[p.id].gate_status if p.id in matches else None
        # 1. papers whose gate could not be decided (no abstract): ask other sources for the abstract
        unknown = [p for p in papers if p.doi and status_of(p) == "unknown"]
        if unknown:
            self._apply_lookups(repo, ["openalex", "europepmc"], [p.doi for p in unknown], stats, "abstract", by_doi, only_missing_abstract=True)
        # 2. retraction state + licence + free links for papers not yet checked
        checked = repo.paper_ids_with_checks(run_paper_ids)
        unchecked = [p for p in papers if p.doi and p.id not in checked and status_of(p) not in (None, "failed")]
        if unchecked:
            self._apply_lookups(repo, ["crossref", "openalex"], [p.doi for p in unchecked], stats, "retraction", by_doi)
        # 3. identifiers and free-copy links for gate-passed papers: a PubMed Central ID is what lets the full-text stage use the cleanest route
        no_ids = [p for p in papers if p.doi and not p.pmcid and p.kind != "trial" and status_of(p) == "passed"]
        if no_ids:
            self._apply_lookups(repo, ["europepmc"], [p.doi for p in no_ids], stats, "ids", by_doi)
        repo.s.flush()
        self._match_all(repo, pmatch, profile.id, papers, RunSummary(run.id, "x", summary.window))  # re-evaluate with enriched data
        summary.enrichment = dict(stats)
        # papers that stayed gate-unknown after enrichment are left for review (not in digests)
        summary.enrichment["still_unknown_gate"] = sum(1 for p in papers if status_of(p) == "unknown")

    @staticmethod
    def _matches(repo: Repository, profile_id: str, paper_id: int) -> list[m.ProfileMatch]:
        return list(repo.s.scalars(select(m.ProfileMatch).where(m.ProfileMatch.profile_id == profile_id, m.ProfileMatch.paper_id == paper_id)))

    def _apply_lookups(self, repo: Repository, names: list[str], dois: list[str], stats: Counter, purpose: str, papers_by_doi: dict[str, m.Paper], *,
                       only_missing_abstract: bool = False) -> None:
        for name in names:
            enricher = self.enrichers.get(name)
            if enricher is None:
                continue
            recs, err = enricher.lookup(list(dois))
            if err:
                stats[f"{name}_{purpose}_error"] += 1
                log.warning("%s lookup (%s) failed: %s", name, purpose, err)
            by_doi = {r.doi: r for r in recs if r.doi}
            for doi in dois:
                rec = by_doi.get(doi)
                paper = papers_by_doi.get(doi)
                if rec is None or paper is None:
                    continue
                if only_missing_abstract and paper.abstract:
                    continue
                repo.merge_enrichment(paper, rec)
                if purpose == "retraction":
                    repo.add_retraction_check(paper.id, name, "retracted" if rec.retracted else "clean")
                    stats[f"{name}_checked"] += 1
                    stats["retracted_found"] += bool(rec.retracted)
                elif purpose == "ids":
                    stats["pmcid_found"] += bool(rec.pmcid)
                else:
                    stats[f"{name}_abstract_added"] += bool(rec.abstract)
