"""Newsletter switches (topics, journals, researchers, sources): the rules, the service that stores them, and what they do to an edition."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from research_agent.application.newsletter import NewsletterService
from research_agent.application.selection import SelectionService
from research_agent.domain.names import author_names, entity_tags, fold, words
from research_agent.domain.selection import Selection
from research_agent.infrastructure.db import models as m
from research_agent.infrastructure.db.repository import Repository
from research_agent.infrastructure.db.session import session_scope
from research_agent.infrastructure.rendering import JinjaNewsletterRenderer
from conftest import rec

NOW = datetime(2026, 10, 30, 18, 0, tzinfo=timezone.utc)
WEEK_END = date(2026, 10, 30)
J = lambda key, conf="id": {"type": "journal", "key": key, "track": "A", "via": "issn", "confidence": conf}
R = lambda key, conf="id": {"type": "researcher", "key": key, "track": "A", "via": "orcid", "confidence": conf}
S = lambda code, name="Topic": {"code": code, "name": name, "track": "A", "terms": [], "specific": True}


# ---------------------------------------------------------------- name helpers
def test_names_are_folded_so_accents_and_case_do_not_matter():
    assert fold("Müller") == "muller" == fold("MULLER") and fold(None) == ""
    assert words("Goadsby, P.J.") == ["goadsby", "p", "j"] and words("  ") == [] and words("%%") == []
    assert author_names([{"family": "Müller", "given": "Karl", "initials": "K"}, {"family": "Goadsby", "initials": "PJ"}, {}]) == "muller karl k; goadsby pj"
    assert author_names(None) == ""


def test_entity_tags_list_followed_journals_and_unambiguous_researchers_only():
    assert entity_tags([R("b"), J("a"), R("c", "name_ambiguous"), {"type": "other", "key": "x"}, J("a")]) == "|journal:a|researcher:b|"
    assert entity_tags([]) == "" and entity_tags(None) == ""


def test_the_search_strings_are_kept_in_step_when_authors_or_entities_change(factory, curt):
    profile, field = curt
    with session_scope(factory) as s:
        repo = Repository(s)
        repo.sync_config(profile, field)
        p = repo.merge_record(rec(sid="1", pmid="1", doi="10.1/x"), NOW).paper
        p.authors = [{"family": "Çelik", "given": "Ali", "initials": "A"}]
        mt, _ = repo.upsert_match(profile.id, p.id, gate_status="passed", entities=[J("cephalalgia")])
        s.flush()
        assert p.author_names == "celik ali a" and mt.entity_tags == "|journal:cephalalgia|"
        mt.entities = [R("peter_j_goadsby"), R("x", "name_ambiguous")]
        assert mt.entity_tags == "|researcher:peter_j_goadsby|"


# ---------------------------------------------------------------- the rules
def test_everything_is_on_by_default_and_nothing_is_filtered():
    sel = Selection()
    assert sel.is_default and sel.allows([S("1")], [J("a")], ["pubmed"]) and sel.summary() == {}
    assert sel.active_sources() == ["pubmed", "europepmc", "crossref", "openalex", "ctgov"]


def test_a_paper_stays_while_any_of_its_routes_in_is_still_on():
    sel = Selection({"subjects": frozenset({"2"}), "journals": frozenset({"a"})})
    assert not sel.allows([S("2")], [J("a")], ["pubmed"])                  # its topic and its journal are both off
    assert sel.allows([S("2"), S("3")], [], ["pubmed"])                    # another topic is on
    assert sel.allows([S("2")], [J("b")], ["pubmed"])                      # another journal is on
    assert sel.allows([S("2")], [R("r")], ["pubmed"])                      # a researcher is on
    assert not sel.allows([S("2")], [R("r", "name_ambiguous")], ["pubmed"])    # an ambiguous researcher is not a route in
    assert sel.allows([], [], ["pubmed"])                                  # no routes at all: these switches do not apply


def test_sources_filter_on_the_sources_that_found_the_paper():
    sel = Selection({"sources": frozenset({"openalex"})})
    assert not sel.allows([S("1")], [], ["openalex"]) and sel.allows([S("1")], [], ["openalex", "pubmed"]) and sel.allows([S("1")], [], [])


# ---------------------------------------------------------------- the service
@pytest.fixture()
def seeded(factory, curt):
    profile, field = curt
    with session_scope(factory) as s:
        Repository(s).sync_config(profile, field)
    return profile, field


def service(factory, curt):
    return SelectionService(factory, *curt, now=lambda: NOW)


def enabled(view, kind):
    return {x["key"]: x["enabled"] for x in view[kind]}


def test_the_catalogue_is_the_profile_and_the_five_sources(seeded, factory, curt):
    v = service(factory, curt).view()
    assert len(v["subjects"]) == 12 and len(v["journals"]) == 10 and len(v["researchers"]) == 30 and [x["key"] for x in v["sources"]] == ["pubmed", "europepmc", "crossref", "openalex", "ctgov"]
    assert all(x["enabled"] for k in ("subjects", "journals", "researchers", "sources") for x in v[k]) and v["switched_off"] == {} and v["changed_at"] is None
    assert v["journals"][0] == {"key": "cephalalgia", "name": "Cephalalgia", "track": "A", "tier": "core", "enabled": True}


def test_switching_things_off_and_on_is_remembered(seeded, factory, curt):
    svc = service(factory, curt)
    v = svc.apply({"journals": {"cephalalgia": False}, "subjects": {"2": False, "3": False}, "sources": {"openalex": False}})
    assert enabled(v, "journals")["cephalalgia"] is False and enabled(v, "journals")["headache"] is True
    assert v["switched_off"] == {"journals": ["cephalalgia"], "subjects": ["2", "3"], "sources": ["openalex"]} and v["changed_at"] == NOW.isoformat()
    assert svc.selection().off["subjects"] == frozenset({"2", "3"})
    v = svc.apply({"subjects": {"2": True}})
    assert v["switched_off"]["subjects"] == ["3"]
    with session_scope(factory) as s:
        assert s.get(m.NewsletterSelection, (curt[0].id, "subjects", "2")).enabled is True      # kept, with its change time, rather than deleted


def test_a_bad_request_changes_nothing(seeded, factory, curt):
    svc = service(factory, curt)
    for bad, match in [({"journals": {"cephalalgia": False, "nope": False}}, "unknown journal 'nope'"), ({"colours": {"a": True}}, "unknown kind"),
                       ({"researchers": {"nobody": True}}, "unknown researcher"), ({"sources": {"google": False}}, "unknown source"),
                       ({"journals": {"cephalalgia": "no"}}, "true or false")]:
        with pytest.raises(ValueError, match=match):
            svc.apply(bad)
    assert svc.view()["switched_off"] == {}


def test_the_newsletter_cannot_be_left_with_nothing_to_select_from(seeded, factory, curt):
    svc = service(factory, curt)
    with pytest.raises(ValueError, match="at least one source"):
        svc.apply({"sources": {k: False for k in ("pubmed", "europepmc", "crossref", "openalex", "ctgov")}})
    everything = {"subjects": {str(i): False for i in range(1, 13)}, "journals": {j.key: False for j in curt[0].journals}, "researchers": {r.key: False for r in curt[0].researchers}}
    with pytest.raises(ValueError, match="at least one topic, journal or researcher"):
        svc.apply(everything)
    assert svc.view()["switched_off"] == {}                           # the refused request left no trace
    everything["journals"]["cephalalgia"] = True                      # one journal left on is enough
    assert svc.apply(everything)["switched_off"]["subjects"] == sorted(str(i) for i in range(1, 13))


def test_reset_puts_everything_back_on(seeded, factory, curt):
    svc = service(factory, curt)
    svc.apply({"journals": {"cephalalgia": False}})
    assert svc.reset()["switched_off"] == {} and svc.selection().is_default
    with session_scope(factory) as s:
        assert s.scalars(select(m.NewsletterSelection)).all() == []


# ---------------------------------------------------------------- what they do to an edition
def add(factory, profile, n, *, days_ago=1, subjects=(), entities=(), sources=("pubmed",), **kw):
    with session_scope(factory) as s:
        repo = Repository(s)
        seen = NOW - timedelta(days=days_ago)
        base = dict(sid=str(n), pmid=str(n), doi=f"10.1/p{n}", title=f"Migraine study number {n} with a sufficiently long title", abstract="Conclusions: It worked well. Truly.")
        base.update(kw)
        p = repo.merge_record(rec(**base), seen).paper
        p.first_seen_at = seen
        repo.upsert_match(profile.id, p.id, gate_status="passed", language_status="ok", track="A", watched=bool(entities), subjects=[S(c) for c in subjects], entities=list(entities))
        run = s.scalar(select(m.Run)) or repo.start_run(profile.id, WEEK_END, WEEK_END, "test")
        s.flush()
        for src in sources:
            s.add(m.PaperHit(paper_id=p.id, run_id=run.id, source=src, path="topic", label="x"))
        s.flush()
        return p.id


def build(factory, profile, field, now=NOW, week_end=WEEK_END):
    return NewsletterService(factory, JinjaNewsletterRenderer(), {}, now=lambda: now).build(profile, field, week_end)


def carried(factory):
    with session_scope(factory) as s:
        return {r[0]: r[1] for r in s.execute(select(m.IssueItem.paper_id, m.IssueItem.role))}


def issue_of(factory, issue_id):
    with session_scope(factory) as s:
        return s.get(m.Issue, issue_id)


def test_with_nothing_switched_off_the_edition_is_as_before(seeded, factory):
    profile, field = seeded
    a, b = add(factory, profile, 1, subjects=["2"]), add(factory, profile, 2, subjects=["3"], entities=[J("cephalalgia")])
    r = build(factory, profile, field)
    assert set(carried(factory)) == {a, b} and not any("switched off" in n for n in r.notes)
    stats = issue_of(factory, r.issue_id).stats
    assert stats["left_out_by_selection"] == 0 and stats["selection"] == {}


def test_a_topic_switched_off_leaves_out_papers_that_only_matched_it(seeded, factory, curt):
    profile, field = seeded
    only2, both, none_at_all = add(factory, profile, 1, subjects=["2"]), add(factory, profile, 2, subjects=["2", "3"]), add(factory, profile, 3)
    service(factory, curt).apply({"subjects": {"2": False}})
    r = build(factory, profile, field)
    assert set(carried(factory)) == {both, none_at_all} and only2 not in carried(factory)
    assert any("switched off in the application" in n and "subjects: 2" in n and "1 matching paper" in n for n in r.notes)
    stats = issue_of(factory, r.issue_id).stats
    assert stats["left_out_by_selection"] == 1 and stats["selection"] == {"subjects": ["2"]}


def test_a_journal_switched_off_loses_its_papers_unless_a_topic_keeps_them_and_loses_its_watched_label(seeded, factory, curt):
    profile, field = seeded
    via_journal_only = add(factory, profile, 1, entities=[J("cephalalgia")])
    with_topic = add(factory, profile, 2, subjects=["2"], entities=[J("cephalalgia")])
    other_journal = add(factory, profile, 3, entities=[J("headache")])
    service(factory, curt).apply({"journals": {"cephalalgia": False}})
    r = build(factory, profile, field)
    assert set(carried(factory)) == {with_topic, other_journal} and via_journal_only not in carried(factory)
    html = issue_of(factory, r.issue_id).html
    assert "Monitored journal: Headache" in html and "Monitored journal: Cephalalgia" not in html


def test_a_researcher_switched_off_loses_papers_found_only_through_them(seeded, factory, curt):
    profile, field = seeded
    only_r, other = add(factory, profile, 1, entities=[R("peter_j_goadsby")]), add(factory, profile, 2, entities=[R("messoud_ashina")])
    service(factory, curt).apply({"researchers": {"peter_j_goadsby": False}})
    build(factory, profile, field)
    assert set(carried(factory)) == {other} and only_r not in carried(factory)


def test_a_source_switched_off_leaves_out_papers_only_that_source_found(seeded, factory, curt):
    profile, field = seeded
    only_oa = add(factory, profile, 1, subjects=["1"], sources=["openalex"])
    both = add(factory, profile, 2, subjects=["1"], sources=["openalex", "pubmed"])
    trial = add(factory, profile, 3, subjects=["1"], sources=["ctgov"])
    service(factory, curt).apply({"sources": {"openalex": False, "ctgov": False}})
    r = build(factory, profile, field)
    assert set(carried(factory)) == {both} and only_oa not in carried(factory) and trial not in carried(factory)
    html = issue_of(factory, r.issue_id).html
    assert "PubMed, Europe PMC and Crossref" in html and "OpenAlex" not in html and "ClinicalTrials.gov" not in html


def test_the_footer_names_every_source_when_none_is_off(seeded, factory):
    profile, field = seeded
    add(factory, profile, 1, subjects=["1"])
    r = build(factory, profile, field)
    assert "PubMed, Europe PMC, Crossref, OpenAlex and ClinicalTrials.gov" in issue_of(factory, r.issue_id).html


def test_papers_left_out_are_unsent_so_switching_back_on_lets_a_later_edition_carry_them(seeded, factory, curt):
    profile, field = seeded
    pid = add(factory, profile, 1, subjects=["2"], days_ago=2)
    svc = service(factory, curt)
    svc.apply({"subjects": {"2": False}, "journals": {"headache": False}})
    build(factory, profile, field)
    assert pid not in carried(factory)
    svc.reset()
    later = NOW + timedelta(days=7)
    build(factory, profile, field, now=later, week_end=later.date())
    assert pid in carried(factory)


def test_retraction_alerts_ignore_the_switches(seeded, factory, curt):
    profile, field = seeded
    pid = add(factory, profile, 1, subjects=["2"], entities=[J("cephalalgia")], retracted=True)
    service(factory, curt).apply({"subjects": {"2": False}, "journals": {"cephalalgia": False}, "sources": {"pubmed": False}})
    build(factory, profile, field)
    assert carried(factory) == {pid: "retraction"}


def test_switches_do_not_touch_the_stored_data(seeded, factory, curt):
    profile, field = seeded
    add(factory, profile, 1, subjects=["2"])

    def snapshot():
        with session_scope(factory) as s:
            return [(p.id, p.title, p.is_retracted) for p in s.scalars(select(m.Paper))], [(x.id, x.subjects, x.entities) for x in s.scalars(select(m.ProfileMatch))]

    before = snapshot()
    service(factory, curt).apply({"subjects": {"2": False}, "sources": {"pubmed": False}})
    build(factory, profile, field)
    assert snapshot() == before
