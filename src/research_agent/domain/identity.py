"""Identifier normalisation and the title fingerprint used when a record has no shared identifier."""
from __future__ import annotations

import hashlib
import re
import unicodedata

_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.I)


def normalise_doi(value: str | None) -> str | None:
    if not value:
        return None
    v = _DOI_PREFIX.sub("", value.strip()).strip().rstrip(".,;)")
    v = v.lower()
    return v if v.startswith("10.") and "/" in v else None


def normalise_pmid(value: str | None) -> str | None:
    v = (value or "").strip()
    return v if v.isdigit() else None


def normalise_pmcid(value: str | None) -> str | None:
    v = (value or "").strip().upper()
    if v.isdigit():
        v = "PMC" + v
    return v if re.fullmatch(r"PMC\d+", v) else None


def normalise_issn(value: str | None) -> str | None:
    v = (value or "").strip().upper().replace(" ", "")
    if re.fullmatch(r"\d{7}[\dX]", v):
        v = v[:4] + "-" + v[4:]
    return v if re.fullmatch(r"\d{4}-\d{3}[\dX]", v) else None


def normalise_title(title: str | None) -> str:
    t = unicodedata.normalize("NFKD", title or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).casefold()
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def title_fingerprint(title: str | None) -> str | None:
    """Stable hash of the normalised title; None for titles too short to identify anything."""
    n = normalise_title(title)
    if len(n) < 25:
        return None
    return hashlib.sha1(n.encode()).hexdigest()[:20]
