"""Decides what a stored paper means for one profile: gate, language, subjects, track, and which watched journals/researchers it came from."""
from __future__ import annotations

from ..domain.field_config import FieldConfig, ProfileConfig
from ..domain.gate import FieldMatcher, GateResult, decide_track, language_status
from ..domain.models import Author, PaperRecord
from ..infrastructure.db import models as m

_CONFIDENCE_RANK = {"id": 3, "name": 2, "query": 1, "name_ambiguous": 0}


def paper_as_record(p: m.Paper) -> PaperRecord:
    return PaperRecord(source="merged", source_id=str(p.id), title=p.title, abstract=p.abstract, doi=p.doi, languages=list(p.languages or []),
                       authors=[Author(a.get("family", ""), a.get("given", ""), a.get("initials", ""), a.get("orcid")) for a in (p.authors or [])])


class ProfileMatcher:
    def __init__(self, profile: ProfileConfig, field: FieldConfig, matcher: FieldMatcher):
        self.profile, self.field, self.matcher = profile, field, matcher
        self.journals = {j.key: j for j in profile.journals}
        self.issn_to_journal = {issn: j for j in profile.journals for issn in j.issns}
        self.orcid_to_researcher = {r.orcid: r for r in profile.researchers if r.orcid}

    def entities(self, paper: m.Paper, hit_meta: list[tuple[m.PaperHit, dict]]) -> list[dict]:
        found: dict[tuple[str, str], dict] = {}

        def add(kind: str, key: str, track: str, via: str, confidence: str) -> None:
            cur = found.get((kind, key))
            if cur is None or _CONFIDENCE_RANK[confidence] > _CONFIDENCE_RANK[cur["confidence"]]:
                found[(kind, key)] = {"type": kind, "key": key, "track": track, "via": via, "confidence": confidence}

        for hit, meta in hit_meta:
            if meta.get("entity_type") and meta.get("entity_key"):
                add(meta["entity_type"], meta["entity_key"], meta.get("track", "A"), f"{hit.source}:{hit.path}", hit.confidence)
        for issn in paper.issns or []:
            j = self.issn_to_journal.get(issn)
            if j:
                add("journal", j.key, j.track, "issn", "id")
        for a in paper.authors or []:
            r = self.orcid_to_researcher.get(a.get("orcid") or "")
            if r:
                add("researcher", r.key, r.track, "orcid", "id")
        return sorted(found.values(), key=lambda e: (e["type"], e["key"]))

    def evaluate(self, paper: m.Paper, hit_meta: list[tuple[m.PaperHit, dict]]) -> dict:
        rec = paper_as_record(paper)
        source_gated = any(h.gated for h, _ in hit_meta)
        gate: GateResult = self.matcher.evaluate_gate(rec, source_gated=source_gated)
        subjects = self.matcher.classify_subjects(rec) if gate.status == "passed" else []
        entities = self.entities(paper, hit_meta)
        entity_tracks = [e["track"] for e in entities if e["confidence"] != "name_ambiguous"]
        bridge_only = gate.status == "passed" and gate.reason.startswith("approved bridge")
        return {
            "gate_status": gate.status, "gate_reason": gate.reason[:200],
            "language_status": language_status(list(paper.languages or []), self.profile.language),
            "track": decide_track(subjects, entity_tracks), "exploratory": bridge_only,
            "watched": any(e["confidence"] != "name_ambiguous" for e in entities),
            "subjects": [{k: v for k, v in s.items() if k != "specific"} | {"specific": s["specific"]} for s in subjects], "entities": entities,
        }
