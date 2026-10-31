"""Relevance gate, language check and subject classification. Pure functions over a field definition."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .field_config import FieldConfig, Subject, Track
from .models import PaperRecord

_BOUND_L = r"(?<![A-Za-z0-9])"
_BOUND_R = r"(?![A-Za-z0-9])"


def _is_acronym(term: str) -> bool:
    """Acronyms and mixed identifiers (CGRP, CoQ10, eNOS, OXPHOS) are matched case-sensitively."""
    letters = [c for c in term if c.isalpha()]
    return bool(letters) and (term.isupper() or any(c.isdigit() for c in term) or term in ("eNOS",))


def term_regex(term: str, *, prefix: bool = False) -> re.Pattern[str]:
    body = re.escape(term)
    tail = r"\w*" if prefix else r"(?:s|es)?" + _BOUND_R
    flags = 0 if (_is_acronym(term) and not prefix) else re.I
    return re.compile(_BOUND_L + body + tail, flags)


@dataclass
class GateResult:
    status: str  # passed | failed | unknown
    reason: str
    matched: list[str] = field(default_factory=list)


class FieldMatcher:
    """Compiled matchers for one field: gate terms, bridge terms (if approved) and subject terms."""

    def __init__(self, cfg: FieldConfig, subjects: list[Subject] | None = None):
        self.cfg = cfg
        self.gate = [(t.text, term_regex(t.text, prefix=t.match == "prefix")) for t in cfg.gate.terms]
        self.bridge = [(t, term_regex(t)) for t in cfg.bridge.terms] if cfg.bridge.approved else []
        self.subjects = [(s, [(t, term_regex(t)) for t in s.terms]) for s in (subjects if subjects is not None else cfg.subjects)]

    def gate_matches(self, text: str) -> list[str]:
        return [name for name, rx in self.gate if rx.search(text)]

    def evaluate_gate(self, rec: PaperRecord, *, source_gated: bool) -> GateResult:
        text = f"{rec.title}\n{rec.abstract}"
        hits = self.gate_matches(text)
        if hits:
            return GateResult("passed", "gate term in title/abstract", hits)
        bridge_hits = [t for t, rx in self.bridge if rx.search(text)]
        if bridge_hits:
            return GateResult("passed", "approved bridge term (exploratory)", bridge_hits)
        if rec.abstract.strip():
            return GateResult("failed", "abstract present, no gate term")
        if source_gated:
            return GateResult("passed", "source query was gated; record has no abstract to confirm", [])
        return GateResult("unknown", "no abstract available to check")

    def classify_subjects(self, rec: PaperRecord) -> list[dict]:
        text = f"{rec.title}\n{rec.abstract}"
        found = []
        for subject, patterns in self.subjects:
            terms = [t for t, rx in patterns if rx.search(text)]
            if terms:
                generic = {g.casefold() for g in subject.generic_terms}
                specific = [t for t in terms if t.casefold() not in generic]
                found.append({"code": subject.code, "name": subject.name, "track": subject.track, "terms": terms, "specific": bool(specific)})
        return found


def decide_track(subject_hits: list[dict], entity_tracks: list[str]) -> Track:
    """Proposal (not client-approved): direct (A) evidence wins; B only when every specific signal is mechanism-only.

    Matches on a subject's generic terms (the gate word itself) do not count, otherwise every gated paper would be A.
    """
    tracks = {h["track"] for h in subject_hits if h.get("specific", True)} | set(entity_tracks)
    if "A" in tracks:
        return "A"
    if "B" in tracks:
        return "B"
    return "A"  # nothing or only dual-track (AB) signals: default to the direct track


def language_status(languages: list[str], wanted: str = "en") -> str:
    """ok | unknown | other. Unknown (no value) is kept: Europe PMC leaves language empty on ~5% of records."""
    if not languages:
        return "unknown"
    norm = {l.strip().lower() for l in languages}
    aliases = {"en": {"en", "eng", "english"}}.get(wanted, {wanted})
    return "ok" if norm & aliases else "other"
