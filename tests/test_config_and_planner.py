import pytest
from pydantic import ValidationError

from research_agent.application.planner import build_specs
from research_agent.domain.field_config import FieldConfig, JournalEntry, ProfileConfig, ResearcherEntry, load_profile, selected_subjects
from research_agent.settings import PROJECT_ROOT


def test_curt_config_matches_the_client_sheet(curt):
    profile, field = curt
    assert len(profile.journals) == 10 and len(profile.researchers) == 30 and len(field.subjects) == 12
    assert {s.track for s in field.subjects} == {"A", "B"}
    assert all(r.openalex_ids and r.orcid for r in profile.researchers)
    assert next(r for r in profile.researchers if r.key == "stephen_d_silberstein").openalex_ids == ["A5060976552", "A5108076630"]
    assert {r.key for r in profile.researchers if r.pubmed_name_ambiguous} == {"shuu_jiun_wang", "andrew_charles", "jan_hoffmann"}
    assert next(j for j in profile.journals if j.key == "j_headache_pain").issns == ["1129-2369", "1129-2377"]


def test_config_validation():
    with pytest.raises(ValidationError):
        ResearcherEntry(key="x", name="X", track="A")  # no PubMed name and no OpenAlex ID
    with pytest.raises(ValidationError):
        ProfileConfig(id="p", name="P", field="f", journals=[JournalEntry(key="a", name="A", track="A", issns=["1"]), JournalEntry(key="a", name="B", track="A", issns=["2"])])
    with pytest.raises(ValidationError):
        FieldConfig.model_validate({"id": "f", "name": "F", "gate": {"terms": []}})
    with pytest.raises(ValidationError):
        FieldConfig.model_validate({"id": "f", "name": "F", "gate": {"terms": [{"text": "x"}]},
                                    "subjects": [{"code": "1", "name": "a", "track": "A", "terms": ["t"]}, {"code": "1", "name": "b", "track": "A", "terms": ["t"]}]})


def test_duplicate_researcher_names_are_collapsed():
    r = ResearcherEntry(key="x", name="X", track="A", pubmed_names=["Doe J", "Doe J"], openalex_ids=["A1", "A1"])
    assert r.pubmed_names == ["Doe J"] and r.openalex_ids == ["A1"]


def test_profile_subject_selection(curt):
    profile, field = curt
    narrowed = profile.model_copy(update={"subjects": ["1", "2"]})
    assert [s.code for s in selected_subjects(narrowed, field)] == ["1", "2"]


def test_unknown_subject_in_profile_is_rejected(tmp_path):
    import shutil
    shutil.copytree(PROJECT_ROOT / "config", tmp_path / "config")
    p = tmp_path / "config" / "profiles" / "curt.yaml"
    p.write_text(p.read_text().replace("subjects: all", "subjects: ['99']"))
    with pytest.raises(ValueError, match="unknown subjects"):
        load_profile(tmp_path / "config", "curt")


def test_planner_builds_every_path_once(curt):
    profile, field = curt
    specs = build_specs(profile, field)
    assert len({s.key for s in specs}) == len(specs)  # no duplicate searches
    by = {(s.path, s.source) for s in specs}
    assert {("topic", "pubmed"), ("topic", "europepmc"), ("preprint", "europepmc"), ("topic", "crossref"), ("topic", "openalex"), ("trial", "ctgov"),
            ("journal", "pubmed"), ("journal", "crossref"), ("researcher", "pubmed"), ("researcher", "openalex")} == by
    assert sum(1 for s in specs if s.path == "journal" and s.source == "crossref") == 17  # one per ISSN
    assert sum(1 for s in specs if s.path == "researcher" and s.source == "openalex") == 30


def test_planner_query_shapes(curt):
    profile, field = curt
    specs = build_specs(profile, field)
    topic = {s.source: s for s in specs if s.path == "topic"}
    assert topic["pubmed"].params["term"] == "(migrain*[tiab])"
    assert topic["europepmc"].params["query"] == "(TITLE_ABS:migrain*)"
    assert topic["openalex"].params["filter"] == "title_and_abstract.search:migraine"  # stemming engines get the plain word, never a wildcard
    assert topic["crossref"].gated is False and topic["pubmed"].gated is True
    j = next(s for s in specs if s.path == "journal" and s.source == "pubmed" and "Cephalalgia" in s.label)
    assert j.params["term"] == "(0333-1024[ISSN] OR 1468-2982[ISSN]) AND (migrain*[tiab])"
    assert any(s.params.get("endpoint") == "journals/1468-2982/works" for s in specs)
    amb = next(s for s in specs if s.path == "researcher" and s.source == "pubmed" and "Hoffmann" in s.label)
    assert amb.confidence == "name_ambiguous" and '"Hoffmann J"[Au]' in amb.params["term"]
    oa = next(s for s in specs if s.path == "researcher" and s.source == "openalex" and "Silberstein" in s.label)
    assert oa.confidence == "id" and "A5060976552|A5108076630" in oa.params["filter"]


def test_planner_filters_and_bridge(curt):
    profile, field = curt
    assert {s.source for s in build_specs(profile, field, sources={"pubmed"})} == {"pubmed"}
    assert {s.path for s in build_specs(profile, field, paths={"journal"})} == {"journal"}
    assert not any(s.path == "bridge" for s in build_specs(profile, field))  # unapproved list never searches
    approved = field.model_copy(update={"bridge": field.bridge.model_copy(update={"approved": True, "terms": ["spreading depolarization"]})})
    bridge = [s for s in build_specs(profile, approved) if s.path == "bridge"]
    assert len(bridge) == 2 and all(not s.gated for s in bridge)


def test_spec_key_ignores_meta_and_label(curt):
    from research_agent.domain.models import SearchSpec
    a = SearchSpec("topic", "pubmed", "one label", {"term": "x"}, meta={"a": 1})
    b = SearchSpec("topic", "pubmed", "another", {"term": "x"}, meta={"b": 2})
    assert a.key == b.key  # identity is path + source + params only
    assert a.key != SearchSpec("topic", "pubmed", "one label", {"term": "y"}).key
