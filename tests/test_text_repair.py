"""Glued words in abstracts: what is repaired, what is deliberately left alone, and where the repair happens."""
import re
from argparse import Namespace
from datetime import date

import pytest
from sqlalchemy import select

from research_agent.domain.models import PaperRecord
from research_agent.domain.text_repair import repair_abstract
from research_agent.infrastructure.db import models as m
from research_agent.infrastructure.db.repository import Repository
from research_agent.infrastructure.db.session import session_scope


@pytest.mark.parametrize("glued, fixed", [
    ("ObjectiveTo estimate the prevalence", "Objective To estimate the prevalence"),
    ("Methods: We did X.ResultsMean age was 21.", "Methods: We did X. Results Mean age was 21."),
    ("ConclusionMigraine is common.", "Conclusion Migraine is common."),
    ("symptoms, depression, and anxiety.MethodsThis cross-sectional study", "symptoms, depression, and anxiety. Methods This cross-sectional study"),
    ("did not.The PBPK model", "did not. The PBPK model"),
    ("(PIR≥4).In the group", "(PIR≥4). In the group"),
    ("p<0.05.The effect held", "p<0.05. The effect held"),
    ("To the Editor:We read with interest", "To the Editor: We read with interest"),
    ("BackgroundMigraine can impair", "Background Migraine can impair"),
])
def test_glued_section_labels_and_sentence_ends_get_their_space(glued, fixed):
    assert repair_abstract(glued) == fixed


@pytest.mark.parametrize("fine", [
    "Records came from PubMed and Embase.",
    "SHapley Additive exPlanations (SHAP) and A MeaSurement Tool",
    "Registered at ClinicalTrials.gov under NCT01234567.",
    "See https://doi.org/10.1016/j.Cell.2020.01 for details",
    "Contact author.Name@Example.org today",
    "Visit www.Migraine.Org for more",
    "The U.S. Food and Drug Administration approved it.",
    "Mean age was 21.9 (SD 2.03); n=74/135 (54.8%).",
    "GABAergic and CGRP-mediated signalling in McDonald's study",
    "A crossover trial of developmental outcomes, successfully carried out.",
])
def test_ordinary_text_names_and_addresses_are_left_exactly_as_they_are(fine):
    assert repair_abstract(fine) == fine


EN = "We conducted a prospective multicenter study of adults with chronic migraine and measured the number of headache days during the treatment period. "


@pytest.mark.parametrize("glued, fixed", [
    ("surveyed via selfadministeredelectronic questionnaire", "surveyed via self administered electronic questionnaire"),
    ("depressive symptoms, andhormonal factors", "depressive symptoms, and hormonal factors"),
    ("in highincome countries", "in high income countries"),
    ("an outcomespecific measure", "an outcome specific measure"),
    ("scalpbased and scalptargeted devices", "scalp based and scalp targeted devices"),
    ("the terminologysuch as aura", "the terminology such as aura"),
])
def test_ordinary_words_run_together_in_lowercase_are_split_in_english_text(glued, fixed):
    assert repair_abstract(EN + glued + ".") == EN + fixed + "."


@pytest.mark.parametrize("real", ["atogepant", "lasmiditan", "rimegepant", "erenumab", "semaglutide", "neuroinflammation", "phonophobia", "sphenopalatine", "dexketoprofen",
                                  "hyperintensities", "migraineurs", "antimigraine", "undercoding", "triggerable", "cybersickness", "blockchain", "chatbots", "nailfold",
                                  "prothrombotic", "postdrome", "audiovestibular", "mucoadhesive", "endocannabinoid", "cardiometabolic", "perimenstrual", "plateauing"])
def test_real_terms_are_never_split_even_though_a_dictionary_could_cut_them_up(real):
    text = EN + f"The {real} findings were discussed."
    assert repair_abstract(text) == text


def test_text_that_is_mostly_not_english_is_left_alone():
    indonesian = "Penelitian ini bertujuan mengetahui hubungan kualitas tidur dengan prestasi mahasiswa kedokteran yang mengalami migrain. Responden dikumpulkan highincome."
    assert repair_abstract(indonesian) == indonesian


def test_words_next_to_dots_slashes_or_hyphens_are_not_split():
    for text in ("Registered at clinicaltrials.gov under the number.", "See https://example.org/highincomecountries for details", "a high-incomecountries analysis", "mail selfadministeredelectronic@x.org"):
        assert repair_abstract(EN + text) == EN + text


def test_without_the_dictionary_package_only_the_two_precise_repairs_run(monkeypatch):
    from research_agent.domain import text_repair
    monkeypatch.setattr(text_repair, "_ws", False)
    assert repair_abstract(EN + "in highincome countries.The end") == EN + "in highincome countries. The end"


def test_repairing_twice_changes_nothing_more():
    once = repair_abstract(EN + "via selfadministeredelectronic questionnaire.ResultsMean age was 21.")
    assert repair_abstract(once) == once and "self administered electronic" in once and "Results Mean" in once


def test_it_only_adds_spaces_never_changes_other_characters_and_repeating_it_changes_nothing():
    messy = "ObjectiveTo test.MethodsWe did\nthis.ResultsOne  two\tthree.ConclusionsFine."
    once = repair_abstract(messy)
    assert once.replace(" ", "") == messy.replace(" ", "") and repair_abstract(once) == once
    assert "\n" in once and "  " in once and "\t" in once                        # existing whitespace is kept as it was


def test_empty_and_missing_text_is_safe():
    assert repair_abstract("") == "" and repair_abstract(None) == "" and repair_abstract("   ") == "   "


def test_every_record_from_every_source_is_repaired_when_it_is_created():
    r = PaperRecord(source="openalex", source_id="W1", title="T", abstract="ObjectiveTo estimate it.MethodsWe did.")
    assert r.abstract == "Objective To estimate it. Methods We did."
    assert PaperRecord(source="pubmed", source_id="1").abstract == ""


def test_the_stored_abstract_is_the_repaired_one_and_a_better_summary_follows(factory, curt):
    from research_agent.domain.newsletter import summarise
    from conftest import rec
    profile, field = curt
    with session_scope(factory) as s:
        repo = Repository(s)
        repo.sync_config(profile, field)
        p = repo.merge_record(rec("openalex", "W1", doi="10.1/g", pmid=None,
                                  abstract="ObjectiveTo test migraine drugs.MethodsWe enrolled 40 adults.ResultsDays fell by 3.ConclusionsThe drug worked well. Safety was good.")).paper
        s.flush()
        assert p.abstract.startswith("Objective To test migraine drugs. Methods We enrolled")
        assert summarise(p.abstract) == "The drug worked well. Safety was good."          # the concluding section is found now that the labels are separate


def test_the_fix_command_reports_first_and_changes_nothing_without_apply(factory, curt, monkeypatch, capsys):
    from research_agent import cli
    from conftest import rec
    profile, field = curt
    with session_scope(factory) as s:
        repo = Repository(s)
        repo.sync_config(profile, field)
        p = repo.merge_record(rec("pubmed", "1", doi="10.1/a", pmid="1", abstract="fine abstract.")).paper
        p.abstract = "ObjectiveTo test.MethodsWe did."                                  # as stored before the repair existed
        s.flush()
        pid = p.id
    monkeypatch.setattr("research_agent.cli.session_factory", lambda _s: factory)
    assert cli.cmd_fix_abstracts(Namespace(apply=False), None) == 0
    out = capsys.readouterr().out
    assert "1 abstract(s) would be repaired" in out and "Run again with --apply" in out
    with session_scope(factory) as s:
        assert s.get(m.Paper, pid).abstract == "ObjectiveTo test.MethodsWe did."
    assert cli.cmd_fix_abstracts(Namespace(apply=True), None) == 0 and "1 abstract(s) repaired" in capsys.readouterr().out
    with session_scope(factory) as s:
        assert s.get(m.Paper, pid).abstract == "Objective To test. Methods We did."
    assert cli.cmd_fix_abstracts(Namespace(apply=True), None) == 0 and "0 abstract(s) repaired" in capsys.readouterr().out      # a second run finds nothing
