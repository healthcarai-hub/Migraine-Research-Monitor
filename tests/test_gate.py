from datetime import date

import pytest

from research_agent.domain.field_config import BridgeTerms, FieldConfig, Gate, GateTerm, Subject
from research_agent.domain.gate import FieldMatcher, decide_track, language_status, term_regex
from conftest import rec


@pytest.fixture()
def matcher(curt):
    profile, field = curt
    return FieldMatcher(field)


@pytest.mark.parametrize("text,ok", [
    ("Migraine in adults", True), ("chronic MIGRAINES", True), ("migrainous infarction", True), ("anti-migraine drugs", True),
    ("We migrated the database", False), ("headache only", False),
])
def test_gate_prefix_match(matcher, text, ok):
    assert bool(matcher.gate_matches(text)) is ok


def test_acronyms_are_case_sensitive_but_words_are_not():
    assert term_regex("CGRP").search("anti-CGRP antibodies") and not term_regex("CGRP").search("cgrp")
    assert term_regex("CSD").search("CSD waves") and not term_regex("CSD").search("csd")
    assert term_regex("magnesium").search("Magnesium supplementation") and term_regex("aura").search("auras")
    assert not term_regex("aura").search("laura")  # word boundary
    assert term_regex("CoQ10").search("CoQ10 dose")


def test_gate_states(matcher):
    assert matcher.evaluate_gate(rec(title="Migraine x", abstract=""), source_gated=False).status == "passed"
    assert matcher.evaluate_gate(rec(title="Something else entirely about sleep patterns", abstract="Sleep only."), source_gated=True).status == "failed"
    no_abs = rec(title="A long title without the relevant word in it at all", abstract="")
    assert matcher.evaluate_gate(no_abs, source_gated=False).status == "unknown"
    assert matcher.evaluate_gate(no_abs, source_gated=True).status == "passed"  # query was gated, nothing to contradict it


def _field(approved: bool) -> FieldConfig:
    return FieldConfig(id="t", name="T", gate=Gate(terms=[GateTerm(text="theanin", match="prefix", plain="theanine")]),
                       bridge=BridgeTerms(approved=approved, terms=["glutamate signalling"]),
                       subjects=[Subject(code="1", name="S", track="B", terms=["glutamate signalling"])])


def test_bridge_terms_only_count_when_approved():
    r = rec(title="Glutamate signalling in cortical networks and what it means", abstract="Glutamate signalling was measured.")
    assert FieldMatcher(_field(False)).evaluate_gate(r, source_gated=False).status == "failed"
    v = FieldMatcher(_field(True)).evaluate_gate(r, source_gated=False)
    assert v.status == "passed" and "bridge" in v.reason


def test_matcher_works_for_a_different_field():
    m = FieldMatcher(_field(False))
    assert m.gate_matches("L-theanine and sleep") and m.gate_matches("Theanines") and not m.gate_matches("migraine")


def test_subject_classification_and_generic_terms(curt, matcher):
    r = rec(title="CGRP and mitochondrial dysfunction in migraine", abstract="Oxidative stress was reduced.")
    hits = {h["code"]: h for h in matcher.classify_subjects(r)}
    assert {"1", "2", "6", "7"} <= set(hits)
    assert hits["1"]["specific"] is False  # only the generic word 'migraine' matched
    assert hits["2"]["specific"] is True


def test_track_decision():
    a = {"code": "2", "track": "A", "specific": True}
    b = {"code": "7", "track": "B", "specific": True}
    generic = {"code": "1", "track": "A", "specific": False}
    assert decide_track([a, b], []) == "A"
    assert decide_track([b, generic], []) == "B"  # the gate word alone does not make a paper direct
    assert decide_track([generic], ["B"]) == "B"
    assert decide_track([generic], []) == "A"
    assert decide_track([b], ["A"]) == "A"
    assert decide_track([], ["AB"]) == "A"


def test_language_status():
    assert language_status(["eng"]) == "ok" and language_status(["en"]) == "ok" and language_status(["fre", "eng"]) == "ok"
    assert language_status([]) == "unknown" and language_status(["ger"]) == "other"
