"""Small parsing helpers shared by the source adapters."""
from __future__ import annotations

import html
import re
from datetime import date, datetime

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def strip_tags(text: str | None) -> str:
    """Remove markup (JATS, HTML) and unescape entities; keeps sentence text."""
    if not text:
        return ""
    return _WS.sub(" ", html.unescape(_TAG.sub(" ", text))).strip()


def parse_date(value: str | None) -> date | None:
    """YYYY-MM-DD, or a timestamp starting with it. Partial dates (YYYY, YYYY-MM) return None: an unknown day must not look exact."""
    if not value:
        return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(value))
    if not m:
        return None
    try:
        return date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None


def parse_date_parts(parts: list[int] | None) -> date | None:
    """Crossref `date-parts` like [2026, 10, 30]; partial dates return None."""
    if not parts or len(parts) < 3:
        return None
    try:
        return date(parts[0], parts[1], parts[2])
    except (ValueError, TypeError):
        return None


def to_date(dt_value: str | None) -> date | None:
    if not dt_value:
        return None
    try:
        return datetime.fromisoformat(dt_value.replace("Z", "+00:00")).date()
    except ValueError:
        return parse_date(dt_value)


def ymd(window_date: date, sep: str = "-") -> str:
    return window_date.strftime(f"%Y{sep}%m{sep}%d")


def clean_text(text: str | None) -> str | None:
    """Unescape HTML entities in short metadata strings (journal names arrive as 'Research &amp; Yoga')."""
    return _WS.sub(" ", html.unescape(text)).strip() if text else text
