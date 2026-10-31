"""Full-text retrieval: for each paper still without text, try the active providers in routing order until one supplies it.

Routing is data-driven: providers are active only when their credentials exist, so a newly added premium key is used automatically, and
papers that earlier providers could not supply are retried by the new provider alone (every attempt is recorded).
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..domain.capabilities import Rights, Tier
from ..domain.fulltext import ERROR, NOT_AVAILABLE, NOT_PERMITTED, OK, FullTextPolicy, FullTextResult
from ..domain.field_config import FieldConfig, ProfileConfig, selected_subjects
from ..domain.fulltext import extract_abstract, text_matches_paper
from ..domain.gate import FieldMatcher
from ..infrastructure.db import models as m
from ..infrastructure.db.repository import Repository
from ..infrastructure.db.session import session_scope
from ..ports import FullTextProvider
from .matching import ProfileMatcher

log = logging.getLogger(__name__)


@dataclass
class FullTextSummary:
    considered: int = 0
    obtained: int = 0
    without_text: int = 0
    skipped_recently_tried: int = 0
    refreshed: int = 0            # papers re-looked-up for a PMC ID or a free copy
    pmcid_found: int = 0
    free_links_found: int = 0
    refresh_errors: list[str] = field(default_factory=list)
    abstracts_recovered: int = 0   # papers that had no abstract and got one from their own text
    by_provider: dict[str, Counter] = field(default_factory=dict)
    print_allowed: int = 0
    provider_order: list[str] = field(default_factory=list)


def policy_for(profile: ProfileConfig) -> FullTextPolicy:
    c = profile.fulltext
    return FullTextPolicy(c.print_text_regardless_of_licence, tuple(c.print_licences), c.allow_entitled_in_print, c.store_entitled_text)


class FullTextService:
    def __init__(self, factory: sessionmaker[Session], providers: list[FullTextProvider], enrichers: dict | None = None, *,
                 now=lambda: datetime.now(timezone.utc)):
        self.factory, self.providers, self.enrichers, self.now = factory, [p for p in providers if p.active()], enrichers or {}, now

    def routes(self, profile: ProfileConfig, *, limit: int = 20) -> list[tuple[str, list[str]]]:
        """Dry run, no network: which providers would be tried for each candidate paper, in order."""
        with session_scope(self.factory) as s:
            repo = Repository(s)
            papers = repo.fulltext_candidates(profile.id, exclude_types=profile.digest.exclude_content_types, limit=limit)
            return [(p.title[:70], [pr.name for pr in self.providers if pr.supports(repo.request_for(p))]) for p in papers]

    def run(self, profile: ProfileConfig, *, limit: int = 50, max_per_provider: dict[str, int] | None = None, refresh: bool = True,
            trigger: str = "manual", field_cfg: FieldConfig | None = None) -> FullTextSummary:
        policy, retry = policy_for(profile), timedelta(days=profile.fulltext.retry_days)
        summary = FullTextSummary(provider_order=[p.name for p in self.providers])
        quota = dict(max_per_provider or {})
        with session_scope(self.factory) as s:
            repo = Repository(s)
            today = self.now().date()
            run = repo.start_run(profile.id, today, today, trigger)
            run.kind = "fulltext"
            if refresh:
                self._refresh(repo, profile, retry, summary)
                s.commit()
            papers = repo.fulltext_candidates(profile.id, exclude_types=profile.digest.exclude_content_types, limit=limit)
            recovered: list[m.Paper] = []
            for paper in papers:
                summary.considered += 1
                got = self._one(repo, paper, policy, retry, quota, summary, recovered)
                summary.obtained += got
                summary.without_text += not got
                s.commit()  # keep what was fetched even if a later paper fails
            summary.abstracts_recovered = len(recovered)
            if recovered and field_cfg is not None:
                self._rematch(repo, profile, field_cfg, recovered)
            repo.finish_run(run, "partial" if summary.refresh_errors else "ok", {
                "considered": summary.considered, "obtained": summary.obtained, "without_text": summary.without_text, "refreshed": summary.refreshed,
                "pmcid_found": summary.pmcid_found, "free_links_found": summary.free_links_found, "abstracts_recovered": summary.abstracts_recovered, "print_allowed": summary.print_allowed,
                "providers": summary.provider_order, "by_provider": {k: dict(v) for k, v in summary.by_provider.items()}, "errors": summary.refresh_errors})
        return summary

    # ------------------------------------------------------------------
    def _refresh(self, repo: Repository, profile: ProfileConfig, retry: timedelta, summary: FullTextSummary) -> None:
        """Papers that had no PMC ID when first stored may have one now (PMC lags publication by weeks; embargoes lift). Look them up again,
        at most once per retry window and only while the paper is recent enough to still change, and let the providers have another go."""
        entered = (self.now() - timedelta(days=profile.fulltext.recheck_days)).date()
        papers = repo.refresh_candidates(profile.id, exclude_types=profile.digest.exclude_content_types, not_refreshed_since=self.now() - retry, entered_since=entered)
        if not papers:
            return
        for name in ("europepmc", "openalex"):   # Europe PMC supplies the PMC ID; OpenAlex supplies free-copy links
            enricher = self.enrichers.get(name)
            if enricher is None:
                continue
            recs, err = enricher.lookup([p.doi for p in papers])
            if err:
                summary.refresh_errors.append(f"{name}: {err[:120]}")
            by_doi = {r.doi: r for r in recs if r.doi}
            for paper in papers:
                rec = by_doi.get(paper.doi)
                if rec is None:
                    continue
                had_pmc, had_free = bool(paper.pmcid), {l.url for l in paper.links if l.is_free}
                repo.merge_enrichment(paper, rec, self.now())
                repo.s.flush()
                repo.s.refresh(paper)
                summary.pmcid_found += bool(paper.pmcid) and not had_pmc
                summary.free_links_found += len({l.url for l in paper.links if l.is_free} - had_free) > 0
        for paper in papers:
            repo.save_fulltext(paper.id, refreshed_at=self.now())
        summary.refreshed = len(papers)

    def _one(self, repo: Repository, paper: m.Paper, policy: FullTextPolicy, retry: timedelta, quota: dict[str, int], summary: FullTextSummary,
             recovered: list[m.Paper]) -> bool:
        req = repo.request_for(paper)
        attempts = repo.latest_attempts(paper.id)
        streaks = repo.error_streaks(paper.id)
        tried = 0
        for prov in self.providers:
            if not prov.supports(req):
                continue
            last = attempts.get(prov.name)
            if last and last.outcome in (NOT_AVAILABLE, NOT_PERMITTED) and self._age(last.attempted_at) < retry:
                continue  # asked recently and it could not help; a provider added since then has no such record and is asked
            if last and last.outcome == ERROR and self._age(last.attempted_at) < min(timedelta(days=2 ** (streaks.get(prov.name, 1) - 1)), retry):
                continue  # repeated errors: wait 1, 2, 4 ... days (never longer than the retry window) instead of hammering a failing endpoint daily
            if prov.name in quota and quota[prov.name] <= 0:
                continue
            tried += 1
            res = self._fetch(prov, req)
            if prov.name in quota:
                quota[prov.name] -= 1
            summary.by_provider.setdefault(prov.name, Counter())[res.outcome] += 1
            repo.add_attempt(paper.id, prov.name, res.outcome, res.detail, self.now())
            if res.outcome != OK:
                continue
            if res.rights == Rights.ENTITLED and not policy.store_entitled_text:
                repo.add_attempt(paper.id, prov.name, NOT_PERMITTED, "policy: entitled text is not stored", self.now())
                continue
            allowed = policy.print_allowed(res)
            repo.save_fulltext(paper.id, status="full_text", format=res.format, provider=prov.name, tier=prov.tier.value, rights=res.rights.value,
                               licence=res.licence_url, licence_key=res.licence, print_allowed=allowed, words=res.words, source_url=res.source_url,
                               text=res.text, fetched_at=self.now(), checked_at=self.now())
            summary.print_allowed += allowed
            if res.abstract and not (paper.abstract or "").strip():   # the paper had no abstract anywhere else: use the one found in its own text
                paper.abstract, paper.abstract_source = res.abstract, res.abstract_kind or "pdf_abstract"
                recovered.append(paper)
            return True
        if not tried:
            summary.skipped_recently_tried += 1
        row = repo.s.get(m.PaperFulltext, paper.id)
        if row is None or row.status != "full_text":
            repo.save_fulltext(paper.id, status="abstract_and_links_only", checked_at=self.now())
        return False

    @staticmethod
    def _fetch(prov: FullTextProvider, req) -> FullTextResult:
        try:
            return prov.fetch(req)
        except Exception as exc:  # a provider bug must not stop the batch
            log.warning("full-text provider %s crashed: %s", prov.name, exc)
            return FullTextResult(ERROR, prov.name, detail=f"provider crashed: {type(exc).__name__}: {str(exc)[:150]}")

    def _age(self, when: datetime) -> timedelta:
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return self.now() - when

    # ------------------------------------------------------------------ abstracts found in the paper's own text
    def _rematch(self, repo: Repository, profile: ProfileConfig, field_cfg: FieldConfig, papers: list[m.Paper]) -> None:
        """A paper that just gained an abstract can now be judged: relevance gate, language, subjects and track are recomputed."""
        pmatch = ProfileMatcher(profile, field_cfg, FieldMatcher(field_cfg, selected_subjects(profile, field_cfg)))
        for paper in papers:
            repo.upsert_match(profile.id, paper.id, **pmatch.evaluate(paper, repo.hit_meta(paper.id)))

    def recover_abstracts(self, profile: ProfileConfig, field_cfg: FieldConfig) -> int:
        """No network: for papers with no abstract whose PDF or plain text is already stored, find the abstract in that text. Returns how many were recovered."""
        with session_scope(self.factory) as s:
            repo = Repository(s)
            done: list[m.Paper] = []
            for paper, ft in repo.papers_missing_abstract_with_text(profile.id):
                found = extract_abstract(ft.text or "")
                if found:
                    paper.abstract, paper.abstract_source = found
                    done.append(paper)
            if done:
                self._rematch(repo, profile, field_cfg, done)
            return len(done)

    def verify_stored(self, profile: ProfileConfig) -> list[tuple[int, str]]:
        """No network: re-check that each stored PDF or plain text really is its paper (DOI or title in the first pages). Texts that are not are removed, the paper goes
        back to 'abstract and links only', and the attempt is recorded so the same wrong link is not tried again. Returns (paper id, title) of what was removed."""
        removed = []
        with session_scope(self.factory) as s:
            repo = Repository(s)
            for ft, paper in s.execute(select(m.PaperFulltext, m.Paper).join(m.Paper, m.Paper.id == m.PaperFulltext.paper_id)
                                       .where(m.PaperFulltext.status == "full_text", m.PaperFulltext.format.in_(["pdf_text", "plain_text"]))).all():
                if text_matches_paper(ft.text or "", paper.title, paper.doi):
                    continue
                repo.add_attempt(paper.id, ft.provider or "unknown", NOT_AVAILABLE, "stored text is not this paper (wrong link); removed", self.now())
                ft.status, ft.text, ft.words, ft.print_allowed, ft.format, ft.source_url = "abstract_and_links_only", None, 0, False, None, None
                removed.append((paper.id, paper.title[:70]))
        return removed
