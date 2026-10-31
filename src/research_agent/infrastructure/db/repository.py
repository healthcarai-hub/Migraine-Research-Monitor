"""All database reads and writes used by the pipeline. Keeps SQLAlchemy out of the application layer's logic."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ...domain import content_type as ct
from ...domain.field_config import FieldConfig, ProfileConfig
from ...domain.identity import normalise_doi, title_fingerprint
from ...domain.models import PaperRecord, SearchSpec, SourceResult
from ...domain.selection import Selection
from . import models as m


def _union(a: list | None, b: list | None) -> list:
    out = list(a or [])
    for x in b or []:
        if x not in out:
            out.append(x)
    return out


def _author_dicts(rec: PaperRecord) -> list[dict]:
    return [{"family": a.family, "given": a.given, "initials": a.initials, "orcid": a.orcid} for a in rec.authors]


@dataclass
class MergeOutcome:
    paper: m.Paper
    created: bool


class Repository:
    def __init__(self, session: Session):
        self.s = session

    # ------------------------------------------------------------ configuration
    def sync_config(self, profile: ProfileConfig, field: FieldConfig) -> None:
        """Idempotent: load the YAML configuration into the configuration tables."""
        s = self.s
        blob = json.dumps(field.model_dump(), sort_keys=True)
        row = s.get(m.Field, field.id)
        if row is None:
            s.add(m.Field(id=field.id, name=field.name, config=field.model_dump(), config_hash=hashlib.sha1(blob.encode()).hexdigest()))
        else:
            row.name, row.config, row.config_hash = field.name, field.model_dump(), hashlib.sha1(blob.encode()).hexdigest()
        prow = s.get(m.Profile, profile.id)
        if prow is None:
            s.add(m.Profile(id=profile.id, field_id=field.id, name=profile.name, config=profile.model_dump()))
        else:
            prow.name, prow.config, prow.field_id = profile.name, profile.model_dump(), field.id
        s.flush()

        subj_rows: dict[str, m.Subject] = {x.code: x for x in s.scalars(select(m.Subject).where(m.Subject.field_id == field.id))}
        for sub in field.subjects:
            r = subj_rows.get(sub.code) or m.Subject(field_id=field.id, code=sub.code)
            r.name, r.track, r.terms, r.generic_terms = sub.name, sub.track, sub.terms, sub.generic_terms
            s.add(r)
            subj_rows[sub.code] = r
        for term in field.bridge.terms:
            if not s.scalar(select(m.BridgeTerm).where(m.BridgeTerm.field_id == field.id, m.BridgeTerm.term == term)):
                s.add(m.BridgeTerm(field_id=field.id, term=term, approved=field.bridge.approved))
        jrows = {x.key: x for x in s.scalars(select(m.Journal).where(m.Journal.field_id == field.id))}
        for j in profile.journals:
            r = jrows.get(j.key) or m.Journal(field_id=field.id, key=j.key)
            r.name, r.track, r.tier, r.issns, r.aliases = j.name, j.track, j.tier, j.issns, j.aliases
            s.add(r)
            jrows[j.key] = r
        rrows = {x.key: x for x in s.scalars(select(m.Researcher).where(m.Researcher.field_id == field.id))}
        for r_ in profile.researchers:
            r = rrows.get(r_.key) or m.Researcher(field_id=field.id, key=r_.key)
            r.name, r.track, r.pubmed_names, r.pubmed_name_ambiguous = r_.name, r_.track, r_.pubmed_names, r_.pubmed_name_ambiguous
            r.openalex_ids, r.orcid, r.aliases = r_.openalex_ids, r_.orcid, r_.aliases
            s.add(r)
            rrows[r_.key] = r
        s.flush()

        chosen_subjects = {x.code for x in field.subjects} if profile.subjects == "all" else set(profile.subjects)
        self._set_membership(m.ProfileSubject, "subject_id", profile.id, {subj_rows[c].id for c in chosen_subjects})
        self._set_membership(m.ProfileJournal, "journal_id", profile.id, {jrows[j.key].id for j in profile.journals})
        self._set_membership(m.ProfileResearcher, "researcher_id", profile.id, {rrows[r.key].id for r in profile.researchers})
        s.flush()

    def _set_membership(self, model, col: str, profile_id: str, wanted: set[int]) -> None:
        have = {getattr(x, col): x for x in self.s.scalars(select(model).where(model.profile_id == profile_id))}
        for i, row in have.items():
            if i not in wanted:
                self.s.delete(row)
        for i in wanted - set(have):
            self.s.add(model(profile_id=profile_id, **{col: i}))

    def upsert_spec(self, profile_id: str, spec: SearchSpec) -> m.SearchSpecRow:
        row = self.s.scalar(select(m.SearchSpecRow).where(m.SearchSpecRow.spec_key == spec.key))
        if row is None:
            row = m.SearchSpecRow(spec_key=spec.key, path=spec.path, source=spec.source, label=spec.label, params=spec.params,
                                  meta={**spec.meta, "confidence": spec.confidence}, gated=spec.gated)
            self.s.add(row)
            self.s.flush()
        else:
            row.label, row.meta, row.active = spec.label, {**spec.meta, "confidence": spec.confidence}, True
        if not self.s.get(m.ProfileSearchSpec, (profile_id, row.id)):
            self.s.add(m.ProfileSearchSpec(profile_id=profile_id, spec_id=row.id))
        return row

    def retire_specs_not_in(self, profile_id: str, keep_keys: set[str]) -> int:
        links = self.s.scalars(select(m.ProfileSearchSpec).where(m.ProfileSearchSpec.profile_id == profile_id)).all()
        n = 0
        for link in links:
            spec = self.s.get(m.SearchSpecRow, link.spec_id)
            if spec and spec.spec_key not in keep_keys:
                self.s.delete(link)
                n += 1
                if not self.s.scalar(select(func.count()).select_from(m.ProfileSearchSpec).where(m.ProfileSearchSpec.spec_id == spec.id, m.ProfileSearchSpec.profile_id != profile_id)):
                    spec.active = False
        return n

    # ------------------------------------------------------------ runs
    def start_run(self, profile_id: str | None, window_start: date, window_end: date, trigger: str) -> m.Run:
        run = m.Run(profile_id=profile_id, window_start=window_start, window_end=window_end, trigger=trigger)
        self.s.add(run)
        self.s.flush()
        return run

    def close_stale_runs(self, hours: int = 3) -> int:
        """A run left 'running' by a crash is marked failed so monitoring does not treat it as live."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        n = 0
        for r in self.s.scalars(select(m.Run).where(m.Run.status == "running")):
            started = r.started_at if r.started_at.tzinfo else r.started_at.replace(tzinfo=timezone.utc)
            if started < cutoff:
                r.status, r.finished_at = "failed", datetime.now(timezone.utc)
                r.stats = {**(r.stats or {}), "abandoned": True}
                n += 1
        return n

    def finish_run(self, run: m.Run, status: str, stats: dict) -> None:
        run.status, run.stats, run.finished_at = status, stats, datetime.now(timezone.utc)

    def log_source(self, run: m.Run, spec_row: m.SearchSpecRow | None, result: SourceResult, status: str, n_new: int, duration_ms: int, note: str | None = None) -> None:
        note = "; ".join(x for x in [note, *result.notes] if x) or None
        self.s.add(m.SourceRunLog(
            run_id=run.id, spec_id=spec_row.id if spec_row else None, source=result.spec.source, path=result.spec.path, label=result.spec.label[:300],
            status=status, n_records=len(result.records), n_new_papers=n_new, total_reported=result.total_reported, requests=result.requests,
            error=result.error or result.skipped_reason, note=note, duration_ms=duration_ms))

    def recent_counts(self, spec_id: int, limit: int = 7, exclude_run: int | None = None) -> list[int]:
        q = select(m.SourceRunLog.n_records).where(m.SourceRunLog.spec_id == spec_id, m.SourceRunLog.status.in_(["ok", "suspect_zero", "truncated"]))
        if exclude_run is not None:
            q = q.where(m.SourceRunLog.run_id != exclude_run)
        return list(self.s.scalars(q.order_by(m.SourceRunLog.id.desc()).limit(limit)))

    # ------------------------------------------------------------ papers
    def find_paper(self, rec: PaperRecord) -> m.Paper | None:
        s = self.s
        for col, val in ((m.Paper.doi, rec.doi), (m.Paper.pmid, rec.pmid), (m.Paper.nct_id, rec.nct_id), (m.Paper.pmcid, rec.pmcid)):
            if val:
                hit = s.scalar(select(m.Paper).where(col == val))
                if hit:
                    return hit
        fp = title_fingerprint(rec.title)
        if fp:
            # title match merges only when it cannot be a preprint/published pair: at least one side has no DOI
            for cand in s.scalars(select(m.Paper).where(m.Paper.title_fp == fp)):
                if not (rec.doi and cand.doi and rec.doi != cand.doi) and not (rec.is_trial or cand.kind == "trial"):
                    return cand
        return None

    def merge_record(self, rec: PaperRecord, now: datetime | None = None, known: m.Paper | None = None) -> MergeOutcome:
        paper = known if known is not None else self.find_paper(rec)      # `known`: the caller has just found it, so it is not looked up twice
        created = paper is None
        now = now or datetime.now(timezone.utc)
        if paper is None:
            paper = m.Paper(first_seen_at=now)
            self.s.add(paper)
        for attr, val in (("doi", rec.doi), ("pmid", rec.pmid), ("pmcid", rec.pmcid), ("nct_id", rec.nct_id)):
            if val and not getattr(paper, attr) and (created or not self._id_taken(attr, val, paper)):     # a new paper cannot clash: find_paper checked every identifier
                setattr(paper, attr, val)
        if rec.title and (not paper.title or (rec.source in ("pubmed", "europepmc") and len(rec.title) >= len(paper.title))):
            paper.title = rec.title
        if paper.pmcid and paper.pmcid_found_at is None:
            paper.pmcid_found_at = now  # first time we hold a PMC ID for this paper; at creation this equals first_seen_at (no lag)
        paper.title_fp = title_fingerprint(paper.title)
        if len(rec.abstract or "") > len(paper.abstract or ""):
            paper.abstract = rec.abstract
        paper.journal = paper.journal or rec.journal
        paper.issns = _union(paper.issns, rec.issns)
        paper.languages = _union(paper.languages, rec.languages)
        paper.pub_types = _union(paper.pub_types, rec.pub_types)
        if len(rec.authors) > len(paper.authors or []):
            paper.authors = _author_dicts(rec)
        paper.published_date = paper.published_date or rec.published_date
        if paper.published_date:
            paper.published_label = paper.published_date.isoformat()
        elif not paper.published_label and rec.extra.get("published_label"):
            paper.published_label = str(rec.extra["published_label"])[:30]
        if rec.entry_date and (paper.first_entry_date is None or rec.entry_date < paper.first_entry_date):
            paper.first_entry_date = rec.entry_date
        paper.url = paper.url or rec.url
        paper.kind = self._kind(paper, rec, created)
        paper.content_type = ct.classify(paper.kind, paper.pub_types, paper.journal, paper.doi, paper.title, paper.abstract)
        if rec.retracted:
            paper.is_retracted = True
            paper.retraction_sources = _union(paper.retraction_sources, [rec.source])
        if rec.retraction_notice:
            paper.is_retraction_notice = True
            paper.retraction_target_doi = paper.retraction_target_doi or rec.retraction_target_doi
            paper.retraction_target_pmid = paper.retraction_target_pmid or rec.retraction_target_pmid
        paper.oa_status = paper.oa_status or rec.oa_status
        paper.licences = _union(paper.licences, rec.licences)
        paper.last_seen_at = now
        self.s.flush()
        self._merge_links(paper, rec, fresh=created)
        return MergeOutcome(paper, created)

    def _id_taken(self, attr: str, val: str, paper: m.Paper) -> bool:
        other = self.s.scalar(select(m.Paper).where(getattr(m.Paper, attr) == val))
        return other is not None and other.id != paper.id

    @staticmethod
    def _kind(paper: m.Paper, rec: PaperRecord, created: bool) -> str:
        if rec.is_trial or paper.kind == "trial":
            return "trial"
        if created:
            return "preprint" if rec.is_preprint else "article"
        # a preprint row becomes an article once any source reports it as published; an article never reverts
        return "article" if (paper.kind == "article" or not rec.is_preprint) else "preprint"

    def _merge_links(self, paper: m.Paper, rec: PaperRecord, fresh: bool = False) -> None:
        have = set() if fresh else {l.url for l in self.s.scalars(select(m.PaperLink).where(m.PaperLink.paper_id == paper.id))}     # a new paper has no links yet
        for link in rec.links:
            if link.url and link.url not in have and len(link.url) <= 1000:
                self.s.add(m.PaperLink(paper_id=paper.id, kind=link.kind, url=link.url, source=link.source, is_free=link.is_free, licence=link.licence))
                have.add(link.url)

    def add_hit(self, paper: m.Paper, run: m.Run, spec_row: m.SearchSpecRow | None, spec: SearchSpec, rec: PaperRecord, fresh: bool = False) -> bool:
        exists = None if fresh else self.s.scalar(select(m.PaperHit.id).where(     # a paper created a moment ago has no hits to clash with
            m.PaperHit.paper_id == paper.id, m.PaperHit.run_id == run.id, m.PaperHit.spec_id == (spec_row.id if spec_row else None),
            m.PaperHit.source_record_id == rec.source_id[:120]))
        if exists:
            return False
        self.s.add(m.PaperHit(
            paper_id=paper.id, run_id=run.id, spec_id=spec_row.id if spec_row else None, source=spec.source, path=spec.path, label=spec.label[:300],
            gated=spec.gated, confidence=spec.confidence, source_record_id=rec.source_id[:120], source_entry_date=rec.entry_date,
            source_published_date=rec.published_date))
        return True

    def hits_for(self, paper_id: int) -> list[m.PaperHit]:
        return list(self.s.scalars(select(m.PaperHit).where(m.PaperHit.paper_id == paper_id)))

    def hit_meta(self, paper_id: int) -> list[tuple[m.PaperHit, dict]]:
        rows = self.s.execute(select(m.PaperHit, m.SearchSpecRow.meta).join(m.SearchSpecRow, m.SearchSpecRow.id == m.PaperHit.spec_id, isouter=True)
                              .where(m.PaperHit.paper_id == paper_id)).all()
        return [(h, meta or {}) for h, meta in rows]

    # ------------------------------------------------------------ matches, checks
    def upsert_match(self, profile_id: str, paper_id: int, **vals) -> tuple[m.ProfileMatch, bool]:
        row = self.s.scalar(select(m.ProfileMatch).where(m.ProfileMatch.profile_id == profile_id, m.ProfileMatch.paper_id == paper_id))
        created = row is None
        if row is None:
            row = m.ProfileMatch(profile_id=profile_id, paper_id=paper_id, **vals)
            self.s.add(row)
        else:
            for k, v in vals.items():
                setattr(row, k, v)
        return row, created

    # ------------------------------------------------------------ bulk reads (one query for many papers: each round trip to a remote database costs real time)
    def _chunks(self, ids: list[int], size: int = 500):
        for i in range(0, len(ids), size):
            yield ids[i:i + size]

    def papers_by_id(self, ids: list[int]) -> dict[int, m.Paper]:
        return {p.id: p for chunk in self._chunks(ids) for p in self.s.scalars(select(m.Paper).where(m.Paper.id.in_(chunk)))}

    def matches_for(self, profile_id: str, paper_ids: list[int]) -> dict[int, m.ProfileMatch]:
        return {mt.paper_id: mt for chunk in self._chunks(paper_ids)
                for mt in self.s.scalars(select(m.ProfileMatch).where(m.ProfileMatch.profile_id == profile_id, m.ProfileMatch.paper_id.in_(chunk)))}

    def hit_meta_many(self, paper_ids: list[int]) -> dict[int, list[tuple[m.PaperHit, dict]]]:
        out: dict[int, list[tuple[m.PaperHit, dict]]] = {}
        for chunk in self._chunks(paper_ids):
            rows = self.s.execute(select(m.PaperHit, m.SearchSpecRow.meta).join(m.SearchSpecRow, m.SearchSpecRow.id == m.PaperHit.spec_id, isouter=True)
                                  .where(m.PaperHit.paper_id.in_(chunk))).all()
            for h, meta in rows:
                out.setdefault(h.paper_id, []).append((h, meta or {}))
        return out

    def paper_ids_with_checks(self, paper_ids: list[int]) -> set[int]:
        return {pid for chunk in self._chunks(paper_ids) for pid in self.s.scalars(select(m.RetractionCheck.paper_id).where(m.RetractionCheck.paper_id.in_(chunk)).distinct())}

    def apply_match(self, existing: m.ProfileMatch | None, profile_id: str, paper_id: int, **vals) -> tuple[m.ProfileMatch, bool]:
        """upsert_match when the caller already knows whether the row exists (it loaded them all in one query)."""
        if existing is None:
            row = m.ProfileMatch(profile_id=profile_id, paper_id=paper_id, **vals)
            self.s.add(row)
            return row, True
        for k, v in vals.items():
            setattr(existing, k, v)
        return existing, False

    def add_retraction_check(self, paper_id: int, source: str, result: str, detail: dict | None = None) -> None:
        self.s.add(m.RetractionCheck(paper_id=paper_id, source=source, result=result, detail=detail or {}))

    def resolve_retraction_targets(self) -> int:
        """Link retraction notices to their original papers and flag the originals as retracted. Returns how many were linked."""
        n = 0
        notices = self.s.scalars(select(m.Paper).where(m.Paper.is_retraction_notice, m.Paper.retraction_target_id.is_(None),
                                                       or_(m.Paper.retraction_target_doi.isnot(None), m.Paper.retraction_target_pmid.isnot(None))))
        for notice in notices:
            conds = []
            if notice.retraction_target_doi:
                conds.append(m.Paper.doi == normalise_doi(notice.retraction_target_doi))
            if notice.retraction_target_pmid:
                conds.append(m.Paper.pmid == notice.retraction_target_pmid)
            target = self.s.scalar(select(m.Paper).where(or_(*conds), m.Paper.id != notice.id))
            if target:
                notice.retraction_target_id = target.id
                if not target.is_retracted:
                    target.is_retracted = True
                target.retraction_sources = _union(target.retraction_sources, ["notice"])
                n += 1
        return n

    # ------------------------------------------------------------ reads shared by the newsletter and the application API
    def digest_candidates(self, profile_id: str, *, since: datetime | None = None, exclude_types: list[str] | None = None,
                          include_unknown_language: bool = True, limit: int | None = None) -> list[tuple[m.Paper, m.ProfileMatch]]:
        """Papers that passed the gate for a profile, newest entry first, skipping the profile's excluded content types."""
        q = (select(m.Paper, m.ProfileMatch).join(m.ProfileMatch, m.ProfileMatch.paper_id == m.Paper.id)
             .where(m.ProfileMatch.profile_id == profile_id, m.ProfileMatch.gate_status == "passed"))
        langs = ["ok", "unknown"] if include_unknown_language else ["ok"]
        q = q.where(m.ProfileMatch.language_status.in_(langs))
        if exclude_types:
            q = q.where(m.Paper.content_type.notin_(exclude_types))
        if since is not None:
            q = q.where(m.Paper.first_seen_at >= since)
        q = q.order_by(m.Paper.first_entry_date.desc(), m.Paper.id.desc())
        if limit:
            q = q.limit(limit)
        return [(p, mt) for p, mt in self.s.execute(q)]

    def reclassify_all(self) -> int:
        """Recompute content_type for every paper (after the rules change). Returns how many rows changed."""
        n = 0
        for p in self.s.scalars(select(m.Paper)):
            new = ct.classify(p.kind, p.pub_types, p.journal, p.doi, p.title, p.abstract)
            if new != p.content_type:
                p.content_type = new
                n += 1
        return n

    # ------------------------------------------------------------ full text
    def fulltext_candidates(self, profile_id: str, *, exclude_types: list[str], limit: int | None = None) -> list[m.Paper]:
        """Gate-passed papers that still have no full text, newest first (trials have none to fetch)."""
        have = select(m.PaperFulltext.paper_id).where(m.PaperFulltext.status == "full_text")
        q = (select(m.Paper).join(m.ProfileMatch, m.ProfileMatch.paper_id == m.Paper.id)
             .where(m.ProfileMatch.profile_id == profile_id, m.ProfileMatch.gate_status.in_(["passed", "unknown"]), m.ProfileMatch.language_status.in_(["ok", "unknown"]),
                    m.Paper.kind != "trial", m.Paper.id.notin_(have)))   # 'unknown' = no abstract to judge relevance by: the text may supply one
        if exclude_types:
            q = q.where(m.Paper.content_type.notin_(exclude_types))
        q = q.order_by(m.Paper.first_entry_date.desc(), m.Paper.id.desc())
        return list(self.s.scalars(q.limit(limit) if limit else q))

    def latest_attempts(self, paper_id: int) -> dict[str, m.FulltextAttempt]:
        out: dict[str, m.FulltextAttempt] = {}
        for a in self.s.scalars(select(m.FulltextAttempt).where(m.FulltextAttempt.paper_id == paper_id).order_by(m.FulltextAttempt.id)):
            out[a.provider] = a
        return out

    def error_streaks(self, paper_id: int) -> dict[str, int]:
        """Per provider: how many of its most recent attempts on this paper in a row ended in an error."""
        streak: dict[str, int] = {}
        for a in self.s.scalars(select(m.FulltextAttempt).where(m.FulltextAttempt.paper_id == paper_id).order_by(m.FulltextAttempt.id)):
            streak[a.provider] = streak.get(a.provider, 0) + 1 if a.outcome == "error" else 0
        return streak

    def add_attempt(self, paper_id: int, provider: str, outcome: str, detail: str | None, at: datetime | None = None) -> None:
        self.s.add(m.FulltextAttempt(paper_id=paper_id, provider=provider, outcome=outcome, detail=(detail or "")[:500], attempted_at=at or datetime.now(timezone.utc)))

    def save_fulltext(self, paper_id: int, **vals) -> m.PaperFulltext:
        row = self.s.get(m.PaperFulltext, paper_id)
        if row is None:
            row = m.PaperFulltext(paper_id=paper_id)
            self.s.add(row)
        for k, v in vals.items():
            setattr(row, k, v)
        return row

    def request_for(self, paper: m.Paper):
        from ...domain.fulltext import FullTextRequest
        links = [{"kind": l.kind, "url": l.url, "is_free": l.is_free, "licence": l.licence} for l in paper.links]
        return FullTextRequest(paper_id=paper.id, doi=paper.doi, pmid=paper.pmid, pmcid=paper.pmcid, journal=paper.journal, title=paper.title,
                               links=links, licences=list(paper.licences or []))

    def merge_enrichment(self, paper: m.Paper, rec: PaperRecord, now: datetime | None = None) -> bool:
        """Fold an identifier/link/abstract lookup into an existing paper. Never creates a paper and never changes its entry date."""
        rec.entry_date = None
        found = self.find_paper(rec)
        if found is None or found.id != paper.id:
            return False
        self.merge_record(rec, now, known=found)
        return True

    def refresh_candidates(self, profile_id: str, *, exclude_types: list[str], not_refreshed_since: datetime, entered_since: date) -> list[m.Paper]:
        """Gate-passed papers with no full text and no PMC ID that were last re-looked-up before `not_refreshed_since` and are recent enough to still change."""
        have = select(m.PaperFulltext.paper_id).where(m.PaperFulltext.status == "full_text")
        fresh = select(m.PaperFulltext.paper_id).where(m.PaperFulltext.refreshed_at >= not_refreshed_since)
        q = (select(m.Paper).join(m.ProfileMatch, m.ProfileMatch.paper_id == m.Paper.id)
             .where(m.ProfileMatch.profile_id == profile_id, m.ProfileMatch.gate_status == "passed", m.ProfileMatch.language_status.in_(["ok", "unknown"]),
                    m.Paper.kind != "trial", m.Paper.doi.isnot(None), m.Paper.pmcid.is_(None), m.Paper.first_entry_date >= entered_since,
                    m.Paper.id.notin_(have), m.Paper.id.notin_(fresh)))
        if exclude_types:
            q = q.where(m.Paper.content_type.notin_(exclude_types))
        return list(self.s.scalars(q.order_by(m.Paper.first_entry_date.desc())))

    def pmc_lag_report(self, profile_id: str, *, exclude_types: list[str], now: datetime) -> dict:
        """How long papers take to get a PMC ID after we first store them, and how old the ones still without one are."""
        rows = self.s.execute(
            select(m.Paper.first_seen_at, m.Paper.pmcid_found_at, m.Paper.pmcid)
            .join(m.ProfileMatch, m.ProfileMatch.paper_id == m.Paper.id)
            .where(m.ProfileMatch.profile_id == profile_id, m.ProfileMatch.gate_status == "passed", m.Paper.kind != "trial", m.Paper.doi.isnot(None),
                   m.Paper.content_type.notin_(exclude_types or ["__none__"]))).all()

        def aware(d):
            return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)

        edges = [(0, "same day"), (7, "1 to 7 days"), (30, "8 to 30 days"), (90, "31 to 90 days"), (10 ** 6, "over 90 days")]

        def bucket(days: int) -> str:
            return next(label for edge, label in edges if days <= edge)

        found, waiting = {}, {}
        for seen, found_at, pmcid in rows:
            seen = aware(seen)
            if pmcid:
                label = bucket(max((aware(found_at) - seen).days, 0)) if found_at else "unknown"
                found[label] = found.get(label, 0) + 1
            else:
                label = bucket(max((now - seen).days, 0))
                waiting[label] = waiting.get(label, 0) + 1
        return {"papers": len(rows), "with_pmcid": sum(found.values()), "found_after": found, "still_without_by_age": waiting}

    # ------------------------------------------------------------ newsletter
    def get_issue(self, profile_id: str, issue_key: str) -> m.Issue | None:
        return self.s.scalar(select(m.Issue).where(m.Issue.profile_id == profile_id, m.Issue.issue_key == issue_key))

    def latest_issue_before(self, profile_id: str, cutoff: datetime) -> m.Issue | None:
        return self.s.scalar(select(m.Issue).where(m.Issue.profile_id == profile_id, m.Issue.window_end < cutoff).order_by(m.Issue.window_end.desc()).limit(1))

    def _sent(self, profile_id: str, role: str, ignore_issue_id: int | None = None):
        """Papers an edition already carries. `ignore_issue_id` is the edition being rebuilt: its own items do not count as sent."""
        q = select(m.IssueItem.paper_id).where(m.IssueItem.profile_id == profile_id, m.IssueItem.role == role)
        return q.where(m.IssueItem.issue_id != ignore_issue_id) if ignore_issue_id else q

    def newsletter_candidates(self, profile_id: str, *, start: datetime, end: datetime, exclude_types: list[str],
                              ignore_issue_id: int | None = None) -> list[tuple[m.Paper, m.ProfileMatch]]:
        """Relevant papers first stored in (start, end] that no earlier edition carried. Retracted papers and retraction notices are handled separately."""
        q = (select(m.Paper, m.ProfileMatch).join(m.ProfileMatch, m.ProfileMatch.paper_id == m.Paper.id)
             .where(m.ProfileMatch.profile_id == profile_id, m.ProfileMatch.gate_status == "passed", m.ProfileMatch.language_status.in_(["ok", "unknown"]),
                    m.Paper.first_seen_at > start, m.Paper.first_seen_at <= end, m.Paper.is_retracted.is_(False), m.Paper.is_retraction_notice.is_(False),
                    m.Paper.id.notin_(self._sent(profile_id, "digest", ignore_issue_id))))
        if exclude_types:
            q = q.where(m.Paper.content_type.notin_(exclude_types))
        return [(p, mt) for p, mt in self.s.execute(q.order_by(m.Paper.id))]

    def retraction_alerts(self, profile_id: str, *, start: datetime, end: datetime, exclude_types: list[str],
                          ignore_issue_id: int | None = None) -> list[tuple[m.Paper, m.ProfileMatch, str | None]]:
        """Retracted papers not yet announced: new ones in the window, and any paper an earlier edition carried that has since been retracted.
        The third value is the key of the edition that carried it, or None."""
        announced = self._sent(profile_id, "retraction", ignore_issue_id)
        base = (select(m.Paper, m.ProfileMatch).join(m.ProfileMatch, m.ProfileMatch.paper_id == m.Paper.id)
                .where(m.ProfileMatch.profile_id == profile_id, m.ProfileMatch.gate_status == "passed", m.Paper.is_retracted.is_(True),
                       m.Paper.is_retraction_notice.is_(False), m.Paper.id.notin_(announced)))
        if exclude_types:
            base = base.where(m.Paper.content_type.notin_(exclude_types))
        sent_in = {pid: key for pid, key in self.s.execute(
            select(m.IssueItem.paper_id, m.Issue.issue_key).join(m.Issue, m.Issue.id == m.IssueItem.issue_id)
            .where(m.IssueItem.profile_id == profile_id, m.IssueItem.role == "digest", m.IssueItem.issue_id != (ignore_issue_id or -1)))}
        out = []
        for p, mt in self.s.execute(base.order_by(m.Paper.id)):
            earlier = sent_in.get(p.id)
            first = p.first_seen_at if p.first_seen_at.tzinfo else p.first_seen_at.replace(tzinfo=timezone.utc)
            if earlier or start < first <= end:
                out.append((p, mt, earlier))
        return out

    # ------------------------------------------------------------ newsletter selection (what the application switched on or off)
    def selection_rows(self, profile_id: str) -> list[m.NewsletterSelection]:
        return list(self.s.scalars(select(m.NewsletterSelection).where(m.NewsletterSelection.profile_id == profile_id)))

    def get_selection(self, profile_id: str) -> Selection:
        off: dict[str, set[str]] = {}
        for r in self.selection_rows(profile_id):
            if not r.enabled:
                off.setdefault(r.kind, set()).add(r.key)
        return Selection({k: frozenset(v) for k, v in off.items()})

    def set_selection(self, profile_id: str, kind: str, key: str, enabled: bool, now: datetime) -> None:
        row = self.s.get(m.NewsletterSelection, (profile_id, kind, key))
        if row is None:
            self.s.add(m.NewsletterSelection(profile_id=profile_id, kind=kind, key=key, enabled=enabled, updated_at=now))
        elif row.enabled != enabled:
            row.enabled, row.updated_at = enabled, now

    def clear_selection(self, profile_id: str) -> int:
        rows = self.selection_rows(profile_id)
        for r in rows:
            self.s.delete(r)
        return len(rows)

    def sources_for(self, paper_ids: list[int]) -> dict[int, set[str]]:
        """Which discovery sources found each paper (from the hits we recorded)."""
        out: dict[int, set[str]] = {}
        for i in range(0, len(paper_ids), 500):
            for pid, source in self.s.execute(select(m.PaperHit.paper_id, m.PaperHit.source).where(m.PaperHit.paper_id.in_(paper_ids[i:i + 500])).distinct()):
                out.setdefault(pid, set()).add(source)
        return out

    def notices_for(self, paper_ids: list[int]) -> dict[int, m.Paper]:
        if not paper_ids:
            return {}
        return {n.retraction_target_id: n for n in self.s.scalars(select(m.Paper).where(m.Paper.retraction_target_id.in_(paper_ids), m.Paper.is_retraction_notice.is_(True)))}

    def fulltext_rows(self, paper_ids: list[int]) -> dict[int, m.PaperFulltext]:
        if not paper_ids:
            return {}
        return {r.paper_id: r for r in self.s.scalars(select(m.PaperFulltext).where(m.PaperFulltext.paper_id.in_(paper_ids)))}

    def papers_sent_since(self, profile_id: str, since: datetime) -> list[m.Paper]:
        """Papers carried by an edition built after `since` that are not known to be retracted yet (for the weekly retraction re-check)."""
        ids = (select(m.IssueItem.paper_id).join(m.Issue, m.Issue.id == m.IssueItem.issue_id)
               .where(m.IssueItem.profile_id == profile_id, m.IssueItem.role == "digest", m.Issue.created_at >= since))
        return list(self.s.scalars(select(m.Paper).where(m.Paper.id.in_(ids), m.Paper.is_retracted.is_(False), m.Paper.doi.isnot(None))))

    def reapply_print_policy(self, allowed) -> int:
        """Recompute `print_allowed` for every stored full text with `allowed(licence_key, rights) -> bool` (after the policy changes). Returns rows changed."""
        n = 0
        for row in self.s.scalars(select(m.PaperFulltext).where(m.PaperFulltext.status == "full_text")):
            new = bool(allowed(row.licence_key, row.rights))
            if row.print_allowed != new:
                row.print_allowed = new
                n += 1
        return n

    def papers_missing_abstract_with_text(self, profile_id: str) -> list[tuple[m.Paper, m.PaperFulltext]]:
        """Gate-passed or undecided papers that still have no abstract but whose PDF or plain text we hold (candidates for abstract recovery without a new download)."""
        q = (select(m.Paper, m.PaperFulltext).join(m.PaperFulltext, m.PaperFulltext.paper_id == m.Paper.id).join(m.ProfileMatch, m.ProfileMatch.paper_id == m.Paper.id)
             .where(m.ProfileMatch.profile_id == profile_id, m.ProfileMatch.gate_status.in_(["passed", "unknown"]), m.Paper.kind != "trial",
                    func.length(func.trim(m.Paper.abstract)) == 0, m.PaperFulltext.status == "full_text", m.PaperFulltext.format.in_(["pdf_text", "plain_text"])))
        return [(p, f) for p, f in self.s.execute(q)]
