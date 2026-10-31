"""Retraction signals that come from the title. Publishers mislabel notices in machine-readable metadata (a real PubMed retraction notice is
typed `erratum` at Crossref), so a conservative title check is an extra signal on top of the metadata flags. Any one signal is enough."""
from __future__ import annotations

import re

from .models import PaperRecord

_NOTICE = re.compile(
    r"^\s*(?:retraction\s+(?:notice|note|statement)\b|retraction\s*(?:to|of)?\s*[:\-\"\u201c\u2018']|withdrawal\s+notice\b|notice\s+of\s+retraction\b)", re.I)
_RETRACTED = re.compile(r"^\s*\[?(?:retracted|withdrawn)\s*(?:article|paper)?\s*[:\]]", re.I)


def apply_title_signals(rec: PaperRecord) -> PaperRecord:
    """Set retraction flags from the title when the title says so; never clears a flag set by metadata."""
    title = rec.title or ""
    if _NOTICE.match(title):
        rec.retraction_notice = True
    elif _RETRACTED.match(title):
        rec.retracted = True
    return rec
