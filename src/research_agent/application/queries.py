"""Read-only questions about what the agent has collected, for the application API (and anything else that only reads).

Nothing here writes. Each method opens its own short session, so the same service works behind a web framework, a CLI or a test.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy import and_, case, func, literal, or_, select
from sqlalchemy.orm import Session, sessionmaker

from ..domain.field_config import FieldConfig, ProfileConfig
from ..domain.names import fold, words
from ..domain.newsletter import DEFAULT_WEIGHTS, authors_short, format_pub, summarise
from ..infrastructure.db import models as m
from ..infrastructure.db.session import session_scope

MAX_PAGE = 200
SORTS = ("newest", "published", "relevance", "fulltext")
TITLE_TERM, ABSTRACT_TERM = 3, 1    # relevance points for each query word found in the title / in the abstract


@dataclass
class Page:
    total: int
    limit: int
    offset: int
    items: list[dict[str, Any]]


def iso(d: datetime | None) -> str | None:
    if d is None:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).isoformat()


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _like(term: str) -> str:
    return "%" + _esc(term.lower()) + "%"


class QueryService:
    def __init__(self, factory: sessionmaker[Session], profile: ProfileConfig, field: FieldConfig):
        self.factory, self.profile, self.field = factory, profile, field
        self._journals = {j.key: j for j in profile.journals}
        self._researchers = {r.key: r for r in profile.researchers}

    # ------------------------------------------------------------------ papers
    def _visible(self, include_hidden: bool) -> list:
        """What the search may show: matched to this profile, past the gate, English or unknown language; and, unless asked, no corrections, deposits, letters."""
        where = [m.ProfileMatch.profile_id == self.profile.id, m.ProfileMatch.gate_status == "passed", m.ProfileMatch.language_status.in_(["ok", "unknown"])]
        if not include_hidden:
            where.append(m.Paper.is_retraction_notice.is_(False))
            if self.profile.digest.exclude_content_types:
                where.append(m.Paper.content_type.notin_(self.profile.digest.exclude_content_types))
        return where

    def _journal_filter(self, values: list[str]):
        """A journal key from the profile (exact: matched by ISSN or by its exact name), or any other text (a part of the journal name)."""
        conds = []
        for v in (x.strip() for x in values):
            if not v:
                continue
            j = self._journals.get(v.lower())
            if j is None:
                conds.append(func.lower(m.Paper.journal).like(_like(v), escape="\\"))
                continue
            conds.append(or_(m.ProfileMatch.entity_tags.like(f"%|journal:{_esc(j.key)}|%", escape="\\"),
                             func.lower(m.Paper.journal).in_([n.lower() for n in (j.name, *j.aliases)])))
        return or_(*conds) if conds else None

    def _researcher_filter(self, values: list[str]):
        by_name = {fold(r.name): r for r in self._researchers.values()}
        conds = []
        for v in (x.strip() for x in values):
            if not v:
                continue
            r = self._researchers.get(v.lower()) or by_name.get(fold(v))
            if r is None:
                raise ValueError(f"unknown researcher '{v}' (GET /v1/papers/filters lists the researchers we follow)")
            conds.append(m.ProfileMatch.entity_tags.like(f"%|researcher:{_esc(r.key)}|%", escape="\\"))
        return or_(*conds) if conds else None

    @staticmethod
    def _author_filter(values: list[str]):
        """Every word of a name must appear in the same paper's author list (accents and case ignored): 'goadsby p' finds Goadsby P, 'müller' finds Muller."""
        conds = []
        for v in values:
            if not v.strip():
                continue
            ws = words(v)
            if not ws:
                raise ValueError(f"author '{v}' has no letters or numbers to search for")
            conds.append(and_(*[m.Paper.author_names.like(_like(w), escape="\\") for w in ws]))
        return or_(*conds) if conds else None

    def papers(self, *, days: int | None = None, since: date | None = None, published_from: date | None = None, published_to: date | None = None,
               q: str | None = None, track: str | None = None, kind: str | None = None, content_type: str | None = None, watched: bool | None = None,
               has_full_text: bool | None = None, retracted: bool | None = None, journal: str | Sequence[str] | None = None, author: Sequence[str] | None = None,
               researcher: Sequence[str] | None = None, include_hidden: bool = False, sort: str = "newest", direction: str = "desc", limit: int = 50,
               offset: int = 0) -> Page:
        """The search. Filters combine with AND; several values of one filter (journal, author, researcher) combine with OR.
        Sorts: newest (when we first stored it), published (publication date; unknown dates last), relevance, fulltext (full text held first, then newest)."""
        limit, offset = max(1, min(limit, MAX_PAGE)), max(0, offset)
        if sort not in SORTS:
            raise ValueError(f"sort must be one of {SORTS}")
        if direction not in ("asc", "desc"):
            raise ValueError("direction must be asc or desc")
        base = (select(m.Paper, m.ProfileMatch, m.PaperFulltext).join(m.ProfileMatch, m.ProfileMatch.paper_id == m.Paper.id)
                .join(m.PaperFulltext, m.PaperFulltext.paper_id == m.Paper.id, isouter=True).where(*self._visible(include_hidden)))
        if days is not None:
            base = base.where(m.Paper.first_seen_at >= datetime.now(timezone.utc) - timedelta(days=days))
        if since is not None:
            base = base.where(m.Paper.first_seen_at >= datetime.combine(since, datetime.min.time(), tzinfo=timezone.utc))
        if published_from is not None:
            base = base.where(m.Paper.published_date >= published_from)
        if published_to is not None:
            base = base.where(m.Paper.published_date <= published_to)
        terms = (q or "").lower().split()
        for term in terms:
            pat = _like(term)
            base = base.where(or_(func.lower(m.Paper.title).like(pat, escape="\\"), func.lower(m.Paper.abstract).like(pat, escape="\\")))
        if track:
            base = base.where(m.ProfileMatch.track == track)
        if kind:
            base = base.where(m.Paper.kind == kind)
        if content_type:
            base = base.where(m.Paper.content_type == content_type)
        if watched is not None:
            base = base.where(m.ProfileMatch.watched.is_(watched))
        if retracted is not None:
            base = base.where(m.Paper.is_retracted.is_(retracted))
        for cond in (self._journal_filter([journal] if isinstance(journal, str) else list(journal or [])), self._author_filter(list(author or [])),
                     self._researcher_filter(list(researcher or []))):
            if cond is not None:
                base = base.where(cond)
        if has_full_text is True:
            base = base.where(m.PaperFulltext.status == "full_text")
        elif has_full_text is False:
            base = base.where(or_(m.PaperFulltext.paper_id.is_(None), m.PaperFulltext.status != "full_text"))
        w = {**DEFAULT_WEIGHTS, **self.profile.newsletter.ranking.weights}
        relevance = literal(0)
        for term in terms:      # each query word: more for the title than the abstract; then the profile's own weights for watched sources and held full text
            pat = _like(term)
            relevance = (relevance + case((func.lower(m.Paper.title).like(pat, escape="\\"), TITLE_TERM), else_=0)
                         + case((func.lower(m.Paper.abstract).like(pat, escape="\\"), ABSTRACT_TERM), else_=0))
        relevance = (relevance + case((m.ProfileMatch.watched.is_(True), max(w["watched_journal"], w["watched_researcher"])), else_=0)
                     + case((m.PaperFulltext.status == "full_text", w["printable_full_text"]), else_=0))
        d = (lambda c: c.desc()) if direction == "desc" else (lambda c: c.asc())
        if sort == "newest":
            order = [d(m.Paper.first_seen_at), d(m.Paper.id)]
        elif sort == "published":
            order = [m.Paper.published_date.is_(None), d(m.Paper.published_date), d(m.Paper.id)]
        elif sort == "relevance":
            order = [d(relevance), m.Paper.first_seen_at.desc(), m.Paper.id.desc()]
        else:
            order = [d(case((m.PaperFulltext.status == "full_text", 1), else_=0)), m.Paper.first_seen_at.desc(), m.Paper.id.desc()]
        with session_scope(self.factory) as s:
            total = s.scalar(select(func.count()).select_from(base.order_by(None).subquery()))
            rows = s.execute(base.add_columns(relevance.label("relevance")).order_by(*order).limit(limit).offset(offset)).all()
            summaries = self._summaries(s, [p.id for p, _, _, _ in rows])
            items = [{**self._brief(p, mt, ft, summaries.get(p.id)), "relevance": int(rel or 0)} for p, mt, ft, rel in rows]
            return Page(total or 0, limit, offset, items)

    def filters(self) -> dict[str, Any]:
        """What a search form can offer: the journals in the database (with counts), the journals and researchers we follow, date ranges, sort choices."""
        with session_scope(self.factory) as s:
            visible = self._visible(False)
            journals = s.execute(select(m.Paper.journal, func.count()).join(m.ProfileMatch, m.ProfileMatch.paper_id == m.Paper.id)
                                 .where(*visible, m.Paper.journal.isnot(None)).group_by(m.Paper.journal).order_by(func.count().desc(), m.Paper.journal).limit(200)).all()
            tags: Counter[str] = Counter()
            for (t,) in s.execute(select(m.ProfileMatch.entity_tags).join(m.Paper, m.ProfileMatch.paper_id == m.Paper.id).where(*visible, m.ProfileMatch.entity_tags != "")):
                tags.update(x for x in t.split("|") if x)
            lo, hi, first, last, total = s.execute(select(func.min(m.Paper.published_date), func.max(m.Paper.published_date), func.min(m.Paper.first_seen_at),
                                                          func.max(m.Paper.first_seen_at), func.count()).join(m.ProfileMatch, m.ProfileMatch.paper_id == m.Paper.id).where(*visible)).one()
        day = lambda x: x.isoformat() if x else None
        return {"papers": total,
                "journals_in_database": [{"name": n, "papers": c} for n, c in journals],
                "followed_journals": [{"key": j.key, "name": j.name, "papers": tags[f"journal:{j.key}"]} for j in self.profile.journals],
                "researchers": [{"key": r.key, "name": r.name, "papers": tags[f"researcher:{r.key}"]} for r in self.profile.researchers],
                "published_between": [day(lo), day(hi)], "stored_between": [iso(first), iso(last)],
                "sort": list(SORTS), "direction": ["desc", "asc"]}

    def paper(self, paper_id: int) -> dict[str, Any] | None:
        with session_scope(self.factory) as s:
            row = s.execute(select(m.Paper, m.ProfileMatch, m.PaperFulltext).join(m.ProfileMatch, m.ProfileMatch.paper_id == m.Paper.id)
                            .join(m.PaperFulltext, m.PaperFulltext.paper_id == m.Paper.id, isouter=True)
                            .where(m.Paper.id == paper_id, m.ProfileMatch.profile_id == self.profile.id)).first()
            if row is None:
                return None
            p, mt, ft = row
            out = self._brief(p, mt, ft, self._summaries(s, [p.id]).get(p.id))
            notice = s.scalar(select(m.Paper).where(m.Paper.retraction_target_id == p.id, m.Paper.is_retraction_notice.is_(True)))
            sent = s.execute(select(m.Issue.issue_key, m.IssueItem.role, m.IssueItem.section).join(m.Issue, m.Issue.id == m.IssueItem.issue_id)
                             .where(m.IssueItem.paper_id == p.id, m.IssueItem.profile_id == self.profile.id).order_by(m.Issue.window_end)).all()
            hits = s.execute(select(m.PaperHit.source, m.PaperHit.path, func.count()).where(m.PaperHit.paper_id == p.id).group_by(m.PaperHit.source, m.PaperHit.path)).all()
            out.update({
                "abstract": p.abstract or "", "abstract_source": p.abstract_source, "language": list(p.languages or []), "publication_types": list(p.pub_types or []),
                "authors_full": [{"family": a.get("family"), "given": a.get("given"), "orcid": a.get("orcid")} for a in (p.authors or [])],
                "links": [{"kind": l.kind, "url": l.url, "source": l.source, "free": l.is_free, "licence": l.licence} for l in p.links],
                "subjects_detail": mt.subjects or [], "match": {"gate": mt.gate_status, "reason": mt.gate_reason, "language": mt.language_status, "exploratory": mt.exploratory},
                "found_by": [{"source": a, "path": b, "times": n} for a, b, n in hits],
                "retraction": {"retracted": p.is_retracted, "flagged_by": list(p.retraction_sources or []), "notice": self._link(notice) if notice else None},
                "sent_in": [{"issue": k, "role": r, "section": sec} for k, r, sec in sent],
                "open_access": p.oa_status, "licences": list(p.licences or []),
            })
            return out

    def fulltext(self, paper_id: int) -> dict[str, Any] | None:
        with session_scope(self.factory) as s:
            ok = s.scalar(select(m.ProfileMatch.id).where(m.ProfileMatch.paper_id == paper_id, m.ProfileMatch.profile_id == self.profile.id))
            ft = s.get(m.PaperFulltext, paper_id) if ok else None
            if ft is None or ft.status != "full_text":
                return None
            return {"paper_id": paper_id, "provider": ft.provider, "tier": ft.tier, "rights": ft.rights, "licence": ft.licence_key, "licence_url": ft.licence,
                    "printable": bool(ft.print_allowed), "format": ft.format, "words": ft.words, "source_url": ft.source_url, "fetched_at": iso(ft.fetched_at), "text": ft.text or ""}

    # ------------------------------------------------------------------ retractions
    def retractions(self, *, limit: int = 50, offset: int = 0) -> Page:
        limit, offset = max(1, min(limit, MAX_PAGE)), max(0, offset)
        base = (select(m.Paper, m.ProfileMatch, m.PaperFulltext).join(m.ProfileMatch, m.ProfileMatch.paper_id == m.Paper.id)
                .join(m.PaperFulltext, m.PaperFulltext.paper_id == m.Paper.id, isouter=True)
                .where(m.ProfileMatch.profile_id == self.profile.id, m.ProfileMatch.gate_status == "passed", m.Paper.is_retracted.is_(True), m.Paper.is_retraction_notice.is_(False)))
        with session_scope(self.factory) as s:
            total = s.scalar(select(func.count()).select_from(base.subquery()))
            rows = s.execute(base.order_by(m.Paper.first_seen_at.desc()).limit(limit).offset(offset)).all()
            notices = {n.retraction_target_id: n for n in s.scalars(select(m.Paper).where(m.Paper.retraction_target_id.in_([p.id for p, _, _ in rows] or [0]), m.Paper.is_retraction_notice.is_(True)))}
            items = []
            for p, mt, ft in rows:
                d = self._brief(p, mt, ft, None)
                d["notice"] = self._link(notices[p.id]) if p.id in notices else None
                d["flagged_by"] = list(p.retraction_sources or [])
                items.append(d)
            return Page(total or 0, limit, offset, items)

    # ------------------------------------------------------------------ editions
    def issues(self, limit: int = 20) -> list[dict[str, Any]]:
        with session_scope(self.factory) as s:
            rows = s.scalars(select(m.Issue).where(m.Issue.profile_id == self.profile.id).order_by(m.Issue.window_end.desc()).limit(max(1, min(limit, MAX_PAGE))))
            return [self._issue_meta(i) for i in rows]

    def issue(self, key: str) -> dict[str, Any] | None:
        with session_scope(self.factory) as s:
            i = s.scalar(select(m.Issue).where(m.Issue.profile_id == self.profile.id, m.Issue.issue_key == key))
            if i is None:
                return None
            out = self._issue_meta(i)
            out["items"] = [{"paper_id": it.paper_id, "section": it.section, "role": it.role, "position": it.position, **{k: it.snapshot.get(k) for k in
                            ("title", "journal", "doi", "track", "group", "summary", "summary_source", "fulltext", "previously_sent_in")}} for it in i.items]
            return out

    def issue_body(self, key: str, fmt: str) -> str | None:
        with session_scope(self.factory) as s:
            i = s.scalar(select(m.Issue).where(m.Issue.profile_id == self.profile.id, m.Issue.issue_key == key))
            return None if i is None else (i.html if fmt == "html" else i.text)

    # ------------------------------------------------------------------ health and overview
    def stats(self) -> dict[str, Any]:
        with session_scope(self.factory) as s:
            matches = (select(m.Paper.id).join(m.ProfileMatch, m.ProfileMatch.paper_id == m.Paper.id)
                       .where(m.ProfileMatch.profile_id == self.profile.id, m.ProfileMatch.gate_status == "passed"))
            by_type = dict(s.execute(select(m.Paper.content_type, func.count()).where(m.Paper.id.in_(matches)).group_by(m.Paper.content_type)).all())
            with_text = s.scalar(select(func.count()).select_from(m.PaperFulltext).where(m.PaperFulltext.status == "full_text", m.PaperFulltext.paper_id.in_(matches)))
            last = lambda kind: s.scalar(select(m.Run).where(m.Run.kind == kind, m.Run.profile_id == self.profile.id).order_by(m.Run.id.desc()).limit(1))
            week = datetime.now(timezone.utc) - timedelta(days=7)
            recent = dict(s.execute(select(m.Run.status, func.count()).where(m.Run.profile_id == self.profile.id, m.Run.started_at >= week).group_by(m.Run.status)).all())
            issue = s.scalar(select(m.Issue).where(m.Issue.profile_id == self.profile.id).order_by(m.Issue.window_end.desc()).limit(1))
            def run(r):
                return None if r is None else {"id": r.id, "status": r.status, "started_at": iso(r.started_at), "finished_at": iso(r.finished_at),
                                               "window": [r.window_start.isoformat(), r.window_end.isoformat()], "errors": len((r.stats or {}).get("errors", []))}
            return {"profile": self.profile.id, "relevant_papers": sum(by_type.values()), "by_content_type": by_type, "with_full_text": with_text or 0,
                    "retracted": s.scalar(select(func.count()).where(m.Paper.id.in_(matches), m.Paper.is_retracted.is_(True))) or 0,
                    "last_collection": run(last("collect")), "last_full_text_pass": run(last("fulltext")), "runs_last_7_days": recent,
                    "latest_issue": self._issue_meta(issue) if issue else None}

    def runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with session_scope(self.factory) as s:
            return [{"id": r.id, "kind": r.kind, "status": r.status, "trigger": r.trigger, "window": [r.window_start.isoformat(), r.window_end.isoformat()],
                     "started_at": iso(r.started_at), "finished_at": iso(r.finished_at),
                     "stats": {k: v for k, v in (r.stats or {}).items() if k in ("new_papers", "passed_matches", "records", "obtained", "considered", "pmcid_found", "abstracts_recovered")},
                     "errors": (r.stats or {}).get("errors", [])[:10]}
                    for r in s.scalars(select(m.Run).where(m.Run.profile_id == self.profile.id).order_by(m.Run.id.desc()).limit(max(1, min(limit, MAX_PAGE))))]

    def profile_summary(self) -> dict[str, Any]:
        return {"id": self.profile.id, "name": self.profile.name, "field": {"id": self.field.id, "name": self.field.name},
                "journals": [{"key": j.key, "name": j.name, "track": j.track, "tier": j.tier, "issns": j.issns} for j in self.profile.journals],
                "researchers": [{"key": r.key, "name": r.name, "track": r.track} for r in self.profile.researchers],
                "subjects": [{"code": x.code, "name": x.name, "track": x.track} for x in self.field.subjects],
                "hidden_content_types": list(self.profile.digest.exclude_content_types)}

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _link(p: m.Paper) -> dict[str, Any]:
        return {"paper_id": p.id, "title": p.title, "url": f"https://doi.org/{p.doi}" if p.doi else p.url}

    @staticmethod
    def _issue_meta(i: m.Issue) -> dict[str, Any]:
        return {"key": i.issue_key, "status": i.status, "subject": i.subject, "window": [iso(i.window_start), iso(i.window_end)], "counts": (i.stats or {}).get("counts", {}),
                "created_at": iso(i.created_at), "published_at": iso(i.published_at), "sent_at": iso(i.sent_at), "held_back": (i.stats or {}).get("held_back", {}),
                "left_out_as_stale": (i.stats or {}).get("left_out_as_stale", 0)}

    @staticmethod
    def _summaries(s: Session, ids: list[int]) -> dict[int, str]:
        out: dict[int, str] = {}
        for r in s.scalars(select(m.PaperSummary).where(m.PaperSummary.paper_id.in_(ids or [0])).order_by(m.PaperSummary.id)):
            out[r.paper_id] = r.text
        return out

    def _brief(self, p: m.Paper, mt: m.ProfileMatch, ft: m.PaperFulltext | None, model_summary: str | None) -> dict[str, Any]:
        watched = []
        for e in mt.entities or []:
            if e["type"] == "journal" and e["key"] in self._journals:
                watched.append({"type": "journal", "name": self._journals[e["key"]].name})
            elif e["type"] == "researcher" and e["key"] in self._researchers and e["confidence"] != "name_ambiguous":
                watched.append({"type": "researcher", "name": self._researchers[e["key"]].name})
        text = bool(ft and ft.status == "full_text")
        return {"id": p.id, "title": p.title, "journal": p.journal, "authors": authors_short(p.authors or []), "published": format_pub(p.published_label), "published_date": p.published_date.isoformat() if p.published_date else None,
                "first_seen_at": iso(p.first_seen_at), "doi": p.doi, "pmid": p.pmid, "pmcid": p.pmcid, "nct_id": p.nct_id,
                "url": f"https://doi.org/{p.doi}" if p.doi else p.url, "kind": p.kind, "content_type": p.content_type, "track": mt.track, "exploratory": mt.exploratory,
                "subjects": [x["name"] for x in (mt.subjects or []) if x.get("specific", True)], "watched": watched, "retracted": p.is_retracted, "preprint": p.kind == "preprint",
                "summary": model_summary or summarise(p.abstract or "") or None, "summary_source": "model" if model_summary else ("abstract" if p.abstract else None),
                "full_text": {"available": text, "printable": bool(text and ft.print_allowed), "words": ft.words if text else 0, "format": ft.format if text else None, "provider": ft.provider if text else None}}
