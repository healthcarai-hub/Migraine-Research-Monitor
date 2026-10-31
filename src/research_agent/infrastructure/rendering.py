"""Jinja2 rendering of the digital edition (HTML and plain text). The print edition has its own renderer, to be added when its template exists."""
from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from ..domain.newsletter import format_pub
from ..domain.selection import SOURCES
from ..ports import RenderedIssue

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"


def badge_tone(label: str, accent: str) -> tuple[str, str]:
    """Background and text colour for a label pill, chosen by what the label says."""
    low = label.lower()
    if low == "retracted":
        return "#fde8e8", "#b42318"
    if low.startswith("full text"):
        return "#e6f4ea", "#166534"
    if low.startswith("open access"):
        return "#e8f0fe", "#1d4ed8"
    if low.startswith("preprint"):
        return "#fff4e0", "#92400e"
    if low.startswith("watched"):
        return "#eef4ff", accent
    return "#f1f5f9", "#475569"


def _list_words(names: list[str]) -> str:
    """['A', 'B', 'C'] -> 'A, B and C'."""
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


class JinjaNewsletterRenderer:
    def __init__(self, directory: Path | None = None):
        self.env = Environment(
            loader=FileSystemLoader(directory or TEMPLATES),
            autoescape=lambda name: bool(name and name.endswith(".html.j2")),   # entity-escape the HTML only, never the plain-text alternative
            trim_blocks=True, lstrip_blocks=True)

    def render(self, *, subject, profile, cfg, week_end, window_start, window_end, sections, counts, built_at, ai_summaries=False, model=None, held_back=0, sources=None) -> RenderedIssue:
        day = lambda d: format_pub(d.date().isoformat() if hasattr(d, "date") else d.isoformat())
        items = [it for sec in sections for _, grp in sec.groups for it in grp]
        stats = {"full_text": sum("Full text available" in it.badges for it in items), "followed": sum(bool(it.watched) for it in items)}
        ctx = {"tone": lambda label: badge_tone(label, cfg.accent_color), "stats": stats, "subject": subject, "cfg": cfg, "sections": sections, "total": sum(counts.values()), "week_end_text": format_pub(week_end.isoformat()),
               "window_text": f"{day(window_start)} to {day(window_end)}", "ai_summaries": ai_summaries, "held_back": held_back, "sources_text": _list_words(sources or list(SOURCES.values())), "built_text": built_at.strftime("%-d %b %Y %H:%M UTC")}
        html = self.env.get_template("newsletter.html.j2").render(**ctx)
        text = re.sub(r"\n{3,}", "\n\n", self.env.get_template("newsletter.txt.j2").render(**ctx)).strip() + "\n"
        return RenderedIssue(subject, html, text)
