"""Turns a field definition plus a profile's chosen journals/researchers into the concrete searches the daily job runs.

Subjects are NOT searched separately: every search is gated on the field's gate term, so a per-subject query is a subset of the base
topic query. Subjects classify papers locally (see domain.gate) instead of costing extra requests.
"""
from __future__ import annotations

from ..domain.field_config import FieldConfig, ProfileConfig
from ..domain.models import SearchSpec


def _pubmed_gate(field: FieldConfig) -> str:
    parts = [f"{t.text}*[tiab]" if t.match == "prefix" else f'"{t.text}"[tiab]' for t in field.gate.terms]
    return "(" + " OR ".join(parts) + ")"


def _epmc_gate(field: FieldConfig) -> str:
    parts = [f"TITLE_ABS:{t.text}*" if t.match == "prefix" else f'TITLE_ABS:"{t.text}"' for t in field.gate.terms]
    return "(" + " OR ".join(parts) + ")"


def _plain_words(field: FieldConfig) -> list[str]:
    return [t.search_word for t in field.gate.terms]


def build_specs(profile: ProfileConfig, field: FieldConfig, *, sources: set[str] | None = None, paths: set[str] | None = None) -> list[SearchSpec]:
    pm_gate, ep_gate, words = _pubmed_gate(field), _epmc_gate(field), _plain_words(field)
    specs: list[SearchSpec] = [
        # ---- topic path: the field's gate term across every index
        SearchSpec("topic", "pubmed", f"{field.name}: PubMed", {"term": f"{pm_gate}"}, min_expected=1),
        SearchSpec("topic", "europepmc", f"{field.name}: Europe PMC", {"query": ep_gate}, min_expected=1),
        SearchSpec("preprint", "europepmc", f"{field.name}: preprints (Europe PMC SRC:PPR)", {"query": f"{ep_gate} AND SRC:PPR"}),
        SearchSpec("topic", "crossref", f"{field.name}: Crossref", {"endpoint": "works", "query": " ".join(words)}, gated=False, min_expected=1),
        SearchSpec("topic", "openalex", f"{field.name}: OpenAlex", {"filter": "title_and_abstract.search:" + "|".join(words)}, min_expected=1),
        SearchSpec("trial", "ctgov", f"{field.name}: ClinicalTrials.gov", {"term": " OR ".join(words)}),
    ]
    # ---- approved bridge terms (Track B items without the gate word); empty until the client approves a list
    if field.bridge.approved:
        for term in field.bridge.terms:
            specs.append(SearchSpec("bridge", "pubmed", f"Bridge: {term}", {"term": f'"{term}"[tiab]'}, gated=False, meta={"bridge_term": term}))
            specs.append(SearchSpec("bridge", "europepmc", f"Bridge: {term}", {"query": f'TITLE_ABS:"{term}"'}, gated=False, meta={"bridge_term": term}))
    # ---- journal path: one PubMed query per journal, one Crossref query per ISSN (print and online are indexed separately)
    for j in profile.journals:
        meta = {"entity_type": "journal", "entity_key": j.key, "track": j.track}
        issn_q = " OR ".join(f"{i}[ISSN]" for i in j.issns)
        specs.append(SearchSpec("journal", "pubmed", j.name, {"term": f"({issn_q}) AND {pm_gate}"}, meta=meta))
        for issn in j.issns:
            specs.append(SearchSpec("journal", "crossref", f"{j.name} ({issn})", {"endpoint": f"journals/{issn}/works", "query": " ".join(words)},
                                    gated=False, meta=meta))
    # ---- researcher path: PubMed by name (lower confidence), OpenAlex by confirmed ID (high confidence)
    for r in profile.researchers:
        meta = {"entity_type": "researcher", "entity_key": r.key, "track": r.track}
        conf = "name_ambiguous" if r.pubmed_name_ambiguous else "name"
        for name in r.pubmed_names:
            specs.append(SearchSpec("researcher", "pubmed", f"{r.name} ({name})", {"term": f'"{name}"[Au] AND {pm_gate}'}, confidence=conf, meta=meta))
        if r.openalex_ids:
            specs.append(SearchSpec("researcher", "openalex", r.name,
                                    {"filter": "author.id:" + "|".join(r.openalex_ids) + ",title_and_abstract.search:" + "|".join(words)},
                                    confidence="id", meta=meta))
    return [s for s in specs if (not sources or s.source in sources) and (not paths or s.path in paths)]
