"""The printed edition of a built issue: collect what the edition stored for each paper, group it into bands, and hand it to the PDF renderer.

Everything comes from the stored edition (`issue_items.snapshot["print"]` and `issues.stats["print"]`), so a published edition prints exactly as it was built and
no network or model call happens here. An edition built before the printed edition existed has no such data and has to be rebuilt.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session, sessionmaker

from ..domain.field_config import FieldConfig, ProfileConfig, selected_subjects
from ..domain.newsletter import format_long_date
from ..domain.print_edition import format_range, surveillance_lines
from ..domain.selection import SOURCES
from ..infrastructure.db.repository import Repository
from ..infrastructure.db.session import session_scope


class PrintNotAvailable(Exception):
    """The edition cannot be printed: it does not exist, printing is switched off, or it was built before the printed edition existed."""


@dataclass
class PrintResult:
    pdf: bytes
    filename: str
    pages: int
    fit: list[dict] = field(default_factory=list)


def _edition_date(issue_key: str, fallback: date) -> date:
    try:
        return date.fromisoformat(issue_key[-10:])
    except ValueError:
        return fallback


def _card(item: dict) -> dict:
    snap, pr = item["snapshot"], item["snapshot"]["print"]
    return {"paper_id": item["paper_id"], "anchor": f"p{item['paper_id']}", "title": snap.get("title", ""), "journal": pr.get("journal") or snap.get("journal", ""),
            "authors": pr.get("authors", ""), "year": pr.get("year", ""), "published": pr.get("published", ""), "date_label": pr.get("date_label", "Published"),
            "pmid": pr.get("pmid"), "doi": snap.get("doi"), "qr": pr.get("qr"), "chips": pr.get("chips", []), "tags": pr.get("tags", []), "text": pr.get("text"),
            "band": pr.get("band", "listed"), "group": pr.get("group") or "General", "sources": pr.get("sources", []), "subjects": pr.get("subjects", []),
            "has_abstract": pr.get("has_abstract", True), "printable_text": pr.get("printable_text", False), "previously_sent_in": pr.get("previously_sent_in"),
            "score": snap.get("score", 0), "position": item["position"]}


class PrintEditionService:
    def __init__(self, factory: sessionmaker[Session], renderer_factory=None):
        self.factory = factory
        self.renderer_factory = renderer_factory

    def view(self, profile: ProfileConfig, field_cfg: FieldConfig, issue_key: str) -> dict:
        """The plain-data description of the edition's pages. Raises PrintNotAvailable when there is nothing to print."""
        cfg = profile.newsletter
        pe = cfg.print_edition
        if not pe.enabled:
            raise PrintNotAvailable("the printed edition is switched off for this profile (newsletter.print_edition.enabled)")
        with session_scope(self.factory) as s:
            issue = Repository(s).get_issue(profile.id, issue_key)
            if issue is None:
                raise PrintNotAvailable(f"no issue {issue_key}")
            items = [{"paper_id": i.paper_id, "position": i.position, "snapshot": dict(i.snapshot or {})} for i in issue.items]
            window_start, window_end, stats, key = issue.window_start, issue.window_end, dict(issue.stats or {}), issue.issue_key
        if any("print" not in i["snapshot"] for i in items):
            raise PrintNotAvailable(f"edition {issue_key} was built before the printed edition existed: rebuild it (newsletter build --rebuild) if it has not been published")
        cards = sorted((_card(i) for i in items), key=lambda c: (-c["score"], c["position"]))
        by_band = {b: [c for c in cards if c["band"] == b] for b in ("high", "medium", "listed", "retraction")}
        for n, c in enumerate(by_band["high"], 1):
            c["n"] = n
        groups: dict[str, list[dict]] = {}
        for c in by_band["medium"]:
            groups.setdefault(c["group"], []).append(c)
        subject_counts = Counter(name for c in cards if c["band"] != "retraction" for name in c["subjects"])
        sources = Counter(name for c in cards for name in c["sources"])
        order = {name: i for i, name in enumerate(SOURCES.values())}
        end_date = _edition_date(key, window_end.date())
        printable = sum(1 for c in cards if c["printable_text"])
        monitored = sum(1 for c in cards if any(x["kind"] in ("journal", "researcher") for x in c["chips"]))
        title = pe.title or cfg.title
        signature = cfg.signature.name if cfg.signature else "Research team"
        return {
            "title": title, "label": pe.label, "running_header": pe.running_header or title, "credit": pe.credit if pe.credit is not None else cfg.footer,
            "recipient": pe.recipient or profile.name, "signature": signature, "max_chips": pe.max_chips,
            "date_long": f"{end_date.strftime('%A')}, {format_long_date(end_date)}", "date_short": format_long_date(end_date),
            "range": format_range(window_start.date(), window_end.date()),
            "counts": {"papers": len(cards), "high": len(by_band["high"]), "medium": len(by_band["medium"]), "listed": len(by_band["listed"]),
                       "retractions": len(by_band["retraction"]), "monitored": monitored, "full_text": printable},
            "sources": sorted(sources.items(), key=lambda kv: (-kv[1], order.get(kv[0], 99))),
            "high": by_band["high"], "medium_groups": list(groups.items()), "listed": by_band["listed"], "retractions": by_band["retraction"],
            "surveillance": surveillance_lines([(sub.name, sub.terms) for sub in selected_subjects(profile, field_cfg)], dict(subject_counts)),
            "synthesis": (stats.get("print") or {}).get("synthesis"),
        }

    def render(self, profile: ProfileConfig, field_cfg: FieldConfig, issue_key: str) -> PrintResult:
        view = self.view(profile, field_cfg, issue_key)
        if self.renderer_factory is None:
            from ..infrastructure.print_rendering import PrintEditionRenderer      # imported only when a PDF is asked for: WeasyPrint is optional
            renderer = PrintEditionRenderer()
        else:
            renderer = self.renderer_factory()
        out = renderer.render(view)
        slug = "-".join(view["title"].split())
        return PrintResult(out.pdf, f"{slug}-{view['date_short'].replace(',', '').replace(' ', '-')}.pdf", out.pages, out.fit)
