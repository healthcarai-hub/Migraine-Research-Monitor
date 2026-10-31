"""Weekly digital edition: pick what is new for the profile, group it, render it, and freeze it as an issue.

Selection is by the date our database first stored a paper (`first_seen_at`), never by publication or issue date. A paper is carried by at most
one edition (the database enforces it); a retraction alert about a paper an earlier edition carried is allowed once.
The print edition is not part of this module: its template has not been supplied.
"""
from __future__ import annotations

import html
import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..domain.field_config import FieldConfig, ProfileConfig
from ..domain.gate import FieldMatcher
from ..domain.newsletter import ItemView, SectionView, apply_caps, authors_short, format_long_date, format_pub, is_stale, score, tidy_title
from ..domain.selection import SOURCES, Selection
from ..domain.summary import SummaryRequest, SummaryResult
from ..infrastructure.db import models as m
from ..infrastructure.db.repository import Repository
from ..infrastructure.db.session import session_scope
from ..ports import NewsletterRenderer, RecordEnricher
from .summaries import SummaryService, final_summary

log = logging.getLogger(__name__)
SECTION_ORDER = ("retractions", "direct", "mechanisms", "preprints", "trials")
GMAIL_CLIP_BYTES = 100_000   # Gmail shows "[Message clipped]" beyond roughly this size (general knowledge, not tested here)
OPEN_OA = {"gold", "diamond", "hybrid", "green", "bronze", "open"}


class IssueExists(Exception):
    pass


@dataclass
class IssueResult:
    issue_id: int
    issue_key: str
    status: str
    created: bool           # False when an existing unpublished issue was returned untouched
    subject: str
    counts: dict[str, int] = field(default_factory=dict)
    window_start: datetime | None = None
    window_end: datetime | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def cutoff_for(week_end: date, now: datetime) -> datetime:
    """A build for today covers everything stored up to now; a build for a past date covers up to the end of that day (UTC)."""
    if week_end >= now.date():
        return now
    return datetime.combine(week_end, time(23, 59, 59), tzinfo=timezone.utc)


class NewsletterService:
    def __init__(self, factory: sessionmaker[Session], renderer: NewsletterRenderer, enrichers: dict[str, RecordEnricher] | None = None,
                 summaries: SummaryService | None = None, *, now=lambda: datetime.now(timezone.utc)):
        self.factory, self.renderer, self.enrichers, self.summaries, self.now = factory, renderer, enrichers or {}, summaries, now
        self._last_stale = 0
        self._last_deselected = 0
        self._selection = Selection()

    # ------------------------------------------------------------------ build
    def build(self, profile: ProfileConfig, field_cfg: FieldConfig, week_end: date, *, rebuild: bool = False, recheck: bool = True,
              use_llm: bool = True) -> IssueResult:
        cfg = profile.newsletter
        cutoff = cutoff_for(week_end, self.now())
        key = f"{profile.id}-{week_end.isoformat()}"
        notes: list[str] = []
        summaries, report = self._model_summaries(profile, field_cfg, key, cutoff, rebuild) if use_llm and cfg.use_llm else ({}, None)
        with session_scope(self.factory) as s:
            repo = Repository(s)
            existing = repo.get_issue(profile.id, key)
            if existing is not None:
                if existing.status != "built":
                    raise IssueExists(f"issue {key} is already {existing.status}; a published or sent edition is never rebuilt")
                if not rebuild:
                    return IssueResult(existing.id, key, existing.status, False, existing.subject, existing.stats.get("counts", {}), existing.window_start,
                                       existing.window_end, ["existing unpublished issue returned unchanged (use rebuild to replace it)"])
                s.delete(existing)
                s.flush()
            # First edition: the normal window. After that the window reaches back to the catch-up limit, so papers an earlier edition could not carry
            # (a missed week, a section cap) are still picked up; the database never lets a paper be carried twice.
            start = cutoff - timedelta(days=cfg.max_catchup_days if repo.latest_issue_before(profile.id, cutoff) else cfg.lookback_days)
            if recheck and cfg.recheck_retraction_weeks > 0:
                notes += self._recheck_retractions(repo, profile, cutoff)
            items, cands, alerts = self._gather(repo, profile, field_cfg, start, cutoff, None, summaries)
            if self._last_stale:
                notes.append(f"{self._last_stale} paper(s) published more than {cfg.freshness.max_publication_age_days} days before we first found them were left out "
                             "(newsletter.freshness); they stay in the database and the app")
            sel = self._selection
            if not sel.is_default:
                notes.append("switched off in the application: " + "; ".join(f"{k}: {', '.join(v)}" for k, v in sel.summary().items())
                             + f". {self._last_deselected} matching paper(s) left out for that reason; they stay unsent and are picked up if switched on again within {cfg.max_catchup_days} days")
            if report is not None:
                notes.append(f"summaries: {report.generated} written by the model, {report.cached} from cache, {report.failed} failed checks or errors (extract used)"
                             + (f", {report.skipped_over_cap} over the per-run cap (extract used)" if report.skipped_over_cap else "")
                             + (f". e.g. {report.reasons[0]}" if report.reasons else ""))
            kept_items, held = apply_caps(items, cfg.caps.per_section, cfg.caps.total, cfg.caps.priority_to_watched)
            subject = cfg.subject.format(title=cfg.title, date=format_long_date(week_end))
            ai = any(i.summary_source == "llm" for i in items)

            def render(chosen: list[ItemView]):
                secs = self._sections(chosen, profile, field_cfg, cfg)
                cnt = {sec.key: sec.count for sec in secs}
                return secs, cnt, self.renderer.render(subject=subject, profile=profile, cfg=cfg, week_end=week_end, window_start=start, window_end=cutoff,
                                                       sections=secs, counts=cnt, built_at=self.now(), ai_summaries=ai, held_back=len(held),
                                                       model=self.summaries.model if self.summaries else None, sources=[SOURCES[k] for k in sel.active_sources()])

            sections, counts, rendered = render(kept_items)
            limit = cfg.caps.max_html_kb * 1024 if cfg.caps.max_html_kb else None
            for _ in range(6):   # safety net: drop the lowest-ranked non-retraction items until the HTML fits (the held-back count shown in the footer is re-rendered each time)
                size = len(rendered.html.encode("utf8"))
                trimmable = sorted((i for i in kept_items if i.section != "retractions"), key=lambda i: (bool(cfg.caps.priority_to_watched and i.watched), i.score, i.paper_id))   # non-watched go first
                if not limit or size <= limit or not trimmable:
                    break
                drop = trimmable[:max(1, math.ceil((size - limit) / (size / max(len(kept_items), 1))))]
                gone = {i.paper_id for i in drop}
                kept_items = [i for i in kept_items if i.paper_id not in gone]
                held += [(i, "size limit") for i in drop]
                sections, counts, rendered = render(kept_items)
            size = len(rendered.html.encode("utf8"))
            held_by = {}
            for it, why in held:
                held_by[why] = held_by.get(why, 0) + 1
            if held:
                notes.append(f"{len(held)} matching paper(s) held back to keep the edition short: " + ", ".join(f"{n} by {w}" for w, n in sorted(held_by.items()))
                             + ". Each may return in the next edition only, ranked lower; after that it stays in the database and the app")
            carried = sum(1 for i in kept_items if i.carried_over)
            if carried:
                notes.append(f"{carried} paper(s) carried over from the previous edition's held-back list, ranked {cfg.carryover_penalty} point(s) lower than new ones")
            if size > GMAIL_CLIP_BYTES:
                notes.append(f"HTML is {size // 1024} KB: Gmail clips messages over about 100 KB, so readers may need 'View entire message'. Lower newsletter.caps.")
            items = kept_items
            issue = m.Issue(profile_id=profile.id, issue_key=key, window_start=start, window_end=cutoff, subject=subject, html=rendered.html, text=rendered.text,
                            stats={"html_bytes": size, "counts": counts, "summaries": {"llm": sum(i.summary_source == "llm" for i in items), "extractive": sum(i.summary_source != "llm" for i in items)}, "candidates": len(cands), "retraction_alerts": len(alerts), "held_back": held_by, "left_out_as_stale": self._last_stale, "left_out_by_selection": self._last_deselected, "carried_over": sum(1 for i in kept_items if i.carried_over), "selection": sel.summary(), "notes": notes, "empty": not any(counts.values())})
            s.add(issue)
            s.flush()
            pos = 0
            for sec in sections:
                for _, grp in sec.groups:
                    for it in grp:
                        pos += 1
                        s.add(m.IssueItem(issue_id=issue.id, profile_id=profile.id, paper_id=it.paper_id, role="retraction" if sec.key == "retractions" else "digest",
                                          section=sec.key, position=pos, snapshot={"title": it.title, "journal": it.journal, "doi": it.doi, "track": it.track, "group": it.group,
                                                                                    "fulltext": it.fulltext_status, "summary": it.summary, "summary_source": it.summary_source, "score": it.score,
                                                                                    "previously_sent_in": it.previously_sent_in, "first_seen": it.first_seen, "carried_over": it.carried_over}))
            s.flush()
            return IssueResult(issue.id, key, issue.status, True, subject, counts, start, cutoff, notes)

    # ------------------------------------------------------------------ pieces
    def _gate(self, field_cfg: FieldConfig) -> FieldMatcher:
        cache = self.__dict__.setdefault("_gate_cache", {})
        if field_cfg.id not in cache:
            cache[field_cfg.id] = FieldMatcher(field_cfg)
        return cache[field_cfg.id]

    def _gather(self, repo: Repository, profile: ProfileConfig, field_cfg: FieldConfig, start: datetime, cutoff: datetime, ignore: int | None,
                summaries: dict[int, SummaryResult]):
        """Everything an edition could carry as view models: new papers plus retraction alerts. Shared by the summary pre-pass and the build."""
        cfg, excluded = profile.newsletter, profile.digest.exclude_content_types
        cands = repo.newsletter_candidates(profile.id, start=start, end=cutoff, exclude_types=excluded, ignore_issue_id=ignore)
        limit = cfg.freshness.max_publication_age_days
        fresh = [(p, mt) for p, mt in cands if not is_stale(p.published_date, p.published_label, p.first_seen_at.date(), limit)]
        self._last_stale = len(cands) - len(fresh)      # papers published long before we found them; reported, never marked as sent
        cands = fresh
        sel = self._selection = repo.get_selection(profile.id)     # what the application switched off; retraction alerts are never filtered by it
        found_by = repo.sources_for([p.id for p, _ in cands])
        if not sel.is_default:
            allowed = [(p, mt) for p, mt in cands if sel.allows(mt.subjects, mt.entities, found_by.get(p.id, ()))]
            self._last_deselected = len(cands) - len(allowed)     # left unsent, so switching the item back on lets a later edition carry them
            cands = allowed
        else:
            self._last_deselected = 0
        alerts = repo.retraction_alerts(profile.id, start=start, end=cutoff, exclude_types=excluded, ignore_issue_id=ignore)
        ids = [p.id for p, _ in cands] + [p.id for p, _, _ in alerts]
        found_by.update(repo.sources_for([p.id for p, _, _ in alerts if p.id not in found_by]))
        ft, notices = repo.fulltext_rows(ids), repo.notices_for(ids)
        carried_before = cutoff - timedelta(days=cfg.lookback_days)       # first stored before this = held back by an earlier edition
        items = [self._view(p, mt, profile, field_cfg, ft.get(p.id), None, cfg, summaries.get(p.id), selection=sel, carried_before=carried_before, found_by=found_by.get(p.id)) for p, mt in cands]
        items += [self._view(p, mt, profile, field_cfg, ft.get(p.id), (notices.get(p.id), earlier), cfg, summaries.get(p.id), retraction=True, selection=sel, found_by=found_by.get(p.id)) for p, mt, earlier in alerts]
        return items, cands, alerts

    def _model_summaries(self, profile: ProfileConfig, field_cfg: FieldConfig, key: str, cutoff: datetime, rebuild: bool):
        """Pre-pass, in its own sessions so no database transaction is held open during slow model calls: summarise what this edition will carry."""
        if self.summaries is None or self.summaries.llm is None:
            return {}, None
        cfg = profile.newsletter
        with session_scope(self.factory) as s:
            repo = Repository(s)
            existing = repo.get_issue(profile.id, key)
            if existing is not None and not rebuild:
                return {}, None
            start = cutoff - timedelta(days=cfg.max_catchup_days if repo.latest_issue_before(profile.id, cutoff) else cfg.lookback_days)
            ignore = existing.id if existing is not None else None   # a rebuild replaces that edition, so its papers are still candidates
            items, _, _ = self._gather(repo, profile, field_cfg, start, cutoff, ignore, {})
            kept, _ = apply_caps(items, cfg.caps.per_section, cfg.caps.total, cfg.caps.priority_to_watched)   # scores do not depend on the summary, so only what will be printed is summarised
            keep = {i.paper_id for i in kept}
            papers = {p.id: p for p in s.scalars(select(m.Paper).where(m.Paper.id.in_(keep or {0})))}
            reqs = [SummaryRequest(pid, papers[pid].title or "", papers[pid].abstract or "") for pid in sorted(keep)]
        return self.summaries.ensure(reqs)

    def _view(self, p: m.Paper, mt: m.ProfileMatch, profile: ProfileConfig, field_cfg: FieldConfig, ft: m.PaperFulltext | None,
              notice, cfg, model_summary: SummaryResult | None = None, retraction: bool = False, selection: Selection | None = None, carried_before: datetime | None = None,
              found_by: set[str] | None = None) -> ItemView:
        selection = selection or Selection()
        seen = p.first_seen_at if p.first_seen_at.tzinfo else p.first_seen_at.replace(tzinfo=timezone.utc)
        late = bool(carried_before is not None and not retraction and seen <= carried_before)
        specific = [s for s in (mt.subjects or []) if s.get("specific", True)]
        order = {s.code: i for i, s in enumerate(field_cfg.subjects)}
        specific.sort(key=lambda s: order.get(s["code"], 99))
        journals = {j.key: j for j in profile.journals if selection.is_on("journals", j.key)}           # a journal or researcher switched off is no longer 'watched':
        researchers = {r.key: r for r in profile.researchers if selection.is_on("researchers", r.key)}   # no label, no priority, no score bonus
        watched, watched_j, watched_r = [], False, False
        mon_journal, mon_researchers = None, []
        journal_name = html.unescape(p.journal or "")       # older rows may still hold '&amp;'; the template escapes once, not twice
        for e in mt.entities or []:
            if e["type"] == "journal" and e["key"] in journals:
                watched_j = True
                journal_name = journals[e["key"]].name   # one spelling for a watched journal, whatever each index calls it
                mon_journal = mon_journal or journals[e["key"]]
                watched.append(f"Monitored journal: {journals[e['key']].name}")
            elif e["type"] == "researcher" and e["key"] in researchers and e["confidence"] != "name_ambiguous":
                watched_r = True
                mon_researchers.append(researchers[e["key"]].name)
                watched.append(f"Monitored researcher: {researchers[e['key']].name}")
        core = any(e["type"] == "journal" and journals.get(e["key"]) and journals[e["key"]].tier == "core" for e in mt.entities or [])
        open_text = bool(ft and ft.status == "full_text" and ft.rights == "open")     # drives the 'Open access' label
        printable_text = bool(ft and ft.status == "full_text" and ft.print_allowed)    # full text in hand that the print edition may carry
        links: list[tuple[str, str]] = []
        primary = f"https://doi.org/{p.doi}" if p.doi else (p.url or "")
        if p.pmid:
            links.append(("PubMed", f"https://pubmed.ncbi.nlm.nih.gov/{p.pmid}/"))
        if p.pmcid:
            links.append(("Free full text (PMC)", f"https://pmc.ncbi.nlm.nih.gov/articles/{p.pmcid}/"))
        pdf = next((l for l in p.links if l.kind == "pdf" and l.is_free), None)
        if pdf:
            links.append(("Free PDF", pdf.url))
        if p.kind == "trial" and p.url:
            primary = p.url
        badges = []
        if p.kind == "preprint":
            badges.append("Preprint: not peer reviewed")
        if retraction or p.is_retracted:
            badges.append("RETRACTED")
        if (p.oa_status in OPEN_OA) or open_text:
            badges.append("Open access")
        if printable_text:
            badges.append("Full text available")           # the printed edition carries this paper in full
        if p.abstract_source == "pdf_intro":
            badges.append("No abstract: summary from the introduction")
        elif p.abstract_source == "pdf_opening":
            badges.append("No abstract: summary from the opening of the text")
        elif p.abstract_source.startswith("pdf_"):
            badges.append("Abstract recovered from the full text")
        if late:
            badges.append("Held over from last week")
        if mt.exploratory:
            badges.append("Exploratory")
        notice_paper, earlier = notice if retraction and notice else (None, None)
        track = mt.track
        summary = final_summary(model_summary, p.abstract or "", sentences=cfg.summary_sentences, max_chars=cfg.summary_max_chars)
        if retraction:
            section = "retractions"
        elif p.kind == "trial":
            section = "trials"
        elif p.kind == "preprint":
            section = "preprints"
        elif mt.exploratory or track == "B":
            section = "mechanisms"
        else:
            section = "direct"
        grouped = section in ("direct", "mechanisms")
        group = (specific[0]["name"] if specific else "General") if grouped else ""
        has_abstract = bool((p.abstract or "").strip())
        if grouped and not has_abstract:
            group = cfg.no_abstract_heading          # gathered at the end of the section, under their own heading
        return ItemView(
            paper_id=p.id, title=tidy_title(p.title) or "(untitled)", journal=journal_name, published=format_pub(p.published_label), authors=authors_short(p.authors or []),
            url=primary, doi=p.doi, summary=summary.text or "No abstract available.", summary_source=summary.source,
            section=section, group=group, kind=p.kind, track=track, badges=badges, links=links, watched=watched, retracted=bool(retraction or p.is_retracted),
            notice_url=(f"https://doi.org/{notice_paper.doi}" if notice_paper is not None and notice_paper.doi else (notice_paper.url if notice_paper is not None else None)),
            sources=[SOURCES[k] for k in SOURCES if k in (found_by or ())], journal_key=mon_journal.key if mon_journal else None, journal_name=mon_journal.name if mon_journal else "",
            journal_tier=mon_journal.tier if mon_journal else "", researchers=mon_researchers,
            previously_sent_in=earlier, fulltext_status=(ft.status if ft else "unknown"), carried_over=late, has_abstract=has_abstract, first_seen=(p.first_seen_at.date().isoformat() if p.first_seen_at else ""),
            date_label="Registered" if p.kind == "trial" else "Published",
            score=score(title_match=bool(self._gate(field_cfg).gate_matches(p.title or "")), watched_journal=watched_j, watched_researcher=watched_r, specific_subjects=len(specific), has_abstract=bool(p.abstract), printable_full_text=printable_text, core_journal=core, weights=cfg.ranking.weights, carried_over=late, carryover_penalty=cfg.carryover_penalty))

    def _sections(self, items: list[ItemView], profile: ProfileConfig, field_cfg: FieldConfig, cfg) -> list[SectionView]:
        subject_order = {s.name: i for i, s in enumerate(field_cfg.subjects)}
        out = []
        for key in SECTION_ORDER:
            demote = key != "retractions"            # papers with no abstract go last in their section; a retraction alert never does
            mine = sorted((i for i in items if i.section == key), key=lambda i: (demote and not i.has_abstract, -i.score, -i.paper_id))
            if not mine:
                continue
            groups: dict[str, list[ItemView]] = {}
            for it in mine:
                groups.setdefault(it.group, []).append(it)
            ordered = sorted(groups.items(), key=lambda kv: (kv[0] == cfg.no_abstract_heading, kv[0] == "General", subject_order.get(kv[0], 99), kv[0]))
            out.append(SectionView(key, cfg.section_titles.get(key, key.title()), ordered))
        return out

    def _recheck_retractions(self, repo: Repository, profile: ProfileConfig, cutoff: datetime) -> list[str]:
        """Ask Crossref and OpenAlex again about papers sent in recent editions: a retraction is usually announced after publication."""
        since = cutoff - timedelta(weeks=profile.newsletter.recheck_retraction_weeks)
        papers = repo.papers_sent_since(profile.id, since)
        notes = []
        if not papers:
            return notes
        flagged = 0
        for name in ("crossref", "openalex"):
            enricher = self.enrichers.get(name)
            if enricher is None:
                continue
            recs, err = enricher.lookup([p.doi for p in papers])
            if err:
                notes.append(f"retraction re-check via {name} failed: {err[:100]}")
            by_doi = {r.doi: r for r in recs if r.doi}
            for p in papers:
                r = by_doi.get(p.doi)
                if r is None:
                    continue
                repo.add_retraction_check(p.id, name, "retracted" if r.retracted else "clean", {"recheck": True})
                if r.retracted and not p.is_retracted:
                    repo.merge_enrichment(p, r, self.now())
                    flagged += 1
        repo.resolve_retraction_targets()
        if flagged:
            notes.append(f"retraction re-check flagged {flagged} previously sent paper(s)")
        return notes

    # ------------------------------------------------------------------ lifecycle
    def mark_published(self, profile_id: str, issue_key: str, draft_ref: str) -> None:
        with session_scope(self.factory) as s:
            issue = Repository(s).get_issue(profile_id, issue_key)
            issue.status, issue.draft_ref, issue.published_at = "published", draft_ref, self.now()

    def mark_sent(self, profile_id: str, issue_key: str) -> None:
        with session_scope(self.factory) as s:
            issue = Repository(s).get_issue(profile_id, issue_key)
            if issue.status == "built":
                raise ValueError("issue has not been published as a draft yet")
            issue.status, issue.sent_at = "sent", self.now()
