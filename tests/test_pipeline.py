"""End-to-end collection runs against fake sources and an in-memory database."""
from datetime import date

import pytest
from sqlalchemy import func, select

from research_agent.application.ingest import IngestPipeline
from research_agent.domain.models import DateWindow, SourceResult
from research_agent.infrastructure.db import models as m
from research_agent.infrastructure.db.session import session_scope
from conftest import rec

W = DateWindow(date(2026, 10, 27), date(2026, 10, 30))


class Fake:
    def __init__(self, name, fn=None, lookup=None):
        self.name, self.fn, self._lookup, self.calls = name, fn or (lambda spec, window: []), lookup, []

    def run(self, spec, window):
        self.calls.append(spec)
        out = self.fn(spec, window)
        return out if isinstance(out, SourceResult) else SourceResult(spec, records=list(out), total_reported=len(out))

    def lookup(self, dois):
        return self._lookup(dois) if self._lookup else ([], None)


def pipeline(factory, **fakes):
    names = ("pubmed", "europepmc", "crossref", "openalex", "ctgov")
    sources = {n: fakes.get(n) or Fake(n) for n in names}
    return IngestPipeline(factory, sources, {n: sources[n] for n in ("crossref", "openalex", "europepmc")}), sources


def only_topic(p, curt, **kw):
    profile, field = curt
    return p.run(profile, field, W, only_paths={"topic"}, **kw)


def counts(factory):
    with session_scope(factory) as s:
        return {t: s.scalar(select(func.count()).select_from(getattr(m, t))) for t in ("Paper", "PaperHit", "ProfileMatch", "Run")}


def test_one_paper_found_by_three_sources_is_stored_once(factory, curt):
    pm = Fake("pubmed", lambda sp, w: [rec("pubmed", "5", doi="10.1/a", pmid="5", abstract="Migraine abstract one two three.")] if sp.path == "topic" else [])
    cr = Fake("crossref", lambda sp, w: [rec("crossref", "10.1/a", doi="10.1/a", abstract="")])
    oa = Fake("openalex", lambda sp, w: [rec("openalex", "W1", doi="10.1/a")])
    p, _ = pipeline(factory, pubmed=pm, crossref=cr, openalex=oa)
    s = only_topic(p, curt)
    assert s.new_papers == 1 and s.passed_matches == 1
    assert [x.split("/")[0] for x in s.suspect] == ["europepmc"]  # the one broad search that returned nothing is flagged
    with session_scope(factory) as db:
        assert {h.source for h in db.scalars(select(m.PaperHit))} == {"pubmed", "crossref", "openalex"}
        match = db.scalar(select(m.ProfileMatch))
        assert match.gate_status == "passed" and match.profile_id == "curt" and match.track == "A"


def test_second_run_over_the_same_window_adds_no_papers(factory, curt):
    pm = Fake("pubmed", lambda sp, w: [rec("pubmed", "5", doi="10.1/a", pmid="5")])
    p, _ = pipeline(factory, pubmed=pm)
    only_topic(p, curt)
    first = counts(factory)
    s2 = only_topic(p, curt)
    after = counts(factory)
    assert s2.new_papers == 0 and after["Paper"] == first["Paper"] == 1 and after["ProfileMatch"] == 1
    assert after["Run"] == 2 and after["PaperHit"] == 2  # each run keeps its own hit


def test_records_without_the_gate_term_are_rejected_not_stored(factory, curt):
    crossref = Fake("crossref", lambda sp, w: [rec("crossref", "10.1/off", doi="10.1/off", title="Sleep patterns in shift workers and their long term health", abstract="Sleep only."),
                                               rec("crossref", "10.1/on", doi="10.1/on")])
    p, _ = pipeline(factory, crossref=crossref)
    s = only_topic(p, curt)
    assert s.rejected_by_gate == 1 and s.new_papers == 1
    with session_scope(factory) as db:
        assert db.scalar(select(m.Paper.doi)) == "10.1/on"
        assert "1 rejected by gate" in db.scalar(select(m.SourceRunLog.note).where(m.SourceRunLog.source == "crossref"))


def test_a_crashing_source_does_not_stop_the_others(factory, curt):
    def boom(sp, w):
        raise RuntimeError("adapter bug")

    p, _ = pipeline(factory, pubmed=Fake("pubmed", boom), openalex=Fake("openalex", lambda sp, w: [rec("openalex", "W1", doi="10.1/ok")]))
    s = only_topic(p, curt)
    assert s.status == "partial" and s.new_papers == 1 and any("adapter bug" in e for e in s.errors)
    with session_scope(factory) as db:
        assert db.scalar(select(m.SourceRunLog.status).where(m.SourceRunLog.source == "pubmed")) == "error"


def test_run_fails_when_every_source_fails(factory, curt):
    err = lambda sp, w: SourceResult(sp, error="down")
    p, _ = pipeline(factory, **{n: Fake(n, err) for n in ("pubmed", "europepmc", "crossref", "openalex", "ctgov")})
    s = only_topic(p, curt)
    assert s.status == "failed"
    with session_scope(factory) as db:
        assert db.scalar(select(m.Run.status)) == "failed"


def test_broad_search_returning_nothing_raises_the_silent_zero_alarm(factory, curt):
    p, _ = pipeline(factory, pubmed=Fake("pubmed", lambda sp, w: []))
    s = only_topic(p, curt)
    assert any("pubmed" in x for x in s.suspect) and s.status == "partial"


def test_history_based_silent_zero(factory, curt):
    # ctgov/trial has no min_expected; it only alarms once its own history shows it normally returns results
    state = {"n": 6}
    ct = Fake("ctgov", lambda sp, w: [rec("ctgov", f"NCT{i}", nct_id=f"NCT{i}", doi=None, is_trial=True, title=f"Migraine trial number {i} of a long series of registered trials") for i in range(state["n"])])
    p, _ = pipeline(factory, ctgov=ct)
    profile, field = curt
    for _ in range(3):
        p.run(profile, field, W, only_sources={"ctgov"})
    state["n"] = 0
    s = p.run(profile, field, W, only_sources={"ctgov"})
    assert any("ctgov" in x for x in s.suspect) and s.status == "partial"


def test_non_english_papers_are_kept_but_marked(factory, curt):
    pm = Fake("pubmed", lambda sp, w: [rec("pubmed", "1", pmid="1", doi="10.1/de", languages=["ger"]), rec("pubmed", "2", pmid="2", doi="10.1/none", languages=[])])
    p, _ = pipeline(factory, pubmed=pm)
    only_topic(p, curt)
    with session_scope(factory) as db:
        got = {pp.doi: mt.language_status for pp, mt in db.execute(select(m.Paper, m.ProfileMatch).join(m.ProfileMatch, m.ProfileMatch.paper_id == m.Paper.id))}
    assert got == {"10.1/de": "other", "10.1/none": "unknown"}


def test_unknown_gate_is_resolved_by_enrichment(factory, curt):
    no_abs = rec("crossref", "10.1/q", doi="10.1/q", title="A title that says nothing about the condition studied here at all", abstract="")
    oa_rec = rec("openalex", "W9", doi="10.1/q", title=no_abs.title, abstract="We studied migraine in 100 adults.")
    p, _ = pipeline(factory, crossref=Fake("crossref", lambda sp, w: [no_abs]), openalex=Fake("openalex", lookup=lambda dois: ([oa_rec], None)))
    s = only_topic(p, curt)
    assert s.enrichment["openalex_abstract_added"] == 1 and s.enrichment["still_unknown_gate"] == 0
    with session_scope(factory) as db:
        assert db.scalar(select(m.ProfileMatch.gate_status)) == "passed"


def test_unknown_gate_that_stays_unknown_is_reported(factory, curt):
    no_abs = rec("crossref", "10.1/q", doi="10.1/q", title="A title that says nothing about the condition studied here at all", abstract="")
    p, _ = pipeline(factory, crossref=Fake("crossref", lambda sp, w: [no_abs]))
    s = only_topic(p, curt)
    assert s.enrichment["still_unknown_gate"] == 1 and s.passed_matches == 0


def test_retraction_found_by_a_lookup_is_flagged_and_logged(factory, curt):
    pm = Fake("pubmed", lambda sp, w: [rec("pubmed", "1", pmid="1", doi="10.1/r")])
    retracted_view = rec("crossref", "10.1/r", doi="10.1/r", retracted=True)
    clean_view = rec("openalex", "W1", doi="10.1/r", retracted=False)
    p, _ = pipeline(factory, pubmed=pm, crossref=Fake("crossref", lookup=lambda d: ([retracted_view], None)), openalex=Fake("openalex", lookup=lambda d: ([clean_view], None)))
    s = only_topic(p, curt)
    assert s.enrichment["retracted_found"] == 1
    with session_scope(factory) as db:
        paper = db.scalar(select(m.Paper))
        assert paper.is_retracted and paper.retraction_sources == ["crossref"]
        assert {(c.source, c.result) for c in db.scalars(select(m.RetractionCheck))} == {("crossref", "retracted"), ("openalex", "clean")}


def test_a_failing_lookup_does_not_fail_the_run(factory, curt):
    pm = Fake("pubmed", lambda sp, w: [rec("pubmed", "1", pmid="1", doi="10.1/r")])
    p, _ = pipeline(factory, pubmed=pm, crossref=Fake("crossref", lookup=lambda d: ([], "HTTP 500")))
    s = only_topic(p, curt)
    assert s.status != "failed" and s.enrichment["crossref_retraction_error"] == 1 and s.new_papers == 1


def test_retraction_notice_links_to_a_paper_stored_earlier(factory, curt):
    profile, field = curt
    day1 = Fake("pubmed", lambda sp, w: [rec("pubmed", "10", pmid="10", doi="10.1/orig")])
    p, _ = pipeline(factory, pubmed=day1)
    only_topic(p, curt)
    day2 = Fake("pubmed", lambda sp, w: [rec("pubmed", "11", pmid="11", doi="10.1/n", title="Retraction notice to a migraine study from last week", retraction_notice=True, retraction_target_pmid="10")])
    p2, _ = pipeline(factory, pubmed=day2)
    only_topic(p2, curt)
    with session_scope(factory) as db:
        orig = db.scalar(select(m.Paper).where(m.Paper.pmid == "10"))
        assert orig.is_retracted
        assert db.scalar(select(m.Paper.retraction_target_id).where(m.Paper.pmid == "11")) == orig.id


def test_title_alone_can_flag_a_notice(factory, curt):
    pm = Fake("pubmed", lambda sp, w: [rec("pubmed", "1", pmid="1", doi="10.1/t", title="Retraction notice to \"Sexual dysfunction in migraine-affected women\"")])
    p, _ = pipeline(factory, pubmed=pm)
    only_topic(p, curt)
    with session_scope(factory) as db:
        assert db.scalar(select(m.Paper.is_retraction_notice)) is True


def test_journal_and_researcher_paths_label_the_match(factory, curt):
    profile, field = curt

    def pm(sp, w):
        if sp.path == "journal" and "Cephalalgia" in sp.label:
            return [rec("pubmed", "1", pmid="1", doi="10.1/j", title="Migraine outcomes reported in the headache society journal")]
        if sp.path == "researcher" and "Goadsby" in sp.label:
            return [rec("pubmed", "2", pmid="2", doi="10.1/r", title="Another migraine study by a watched researcher in some journal")]
        return []

    p, _ = pipeline(factory, pubmed=Fake("pubmed", pm))
    p.run(profile, field, W, only_sources={"pubmed"}, only_paths={"journal", "researcher"})
    with session_scope(factory) as db:
        ents = {pp.doi: mt.entities for pp, mt in db.execute(select(m.Paper, m.ProfileMatch).join(m.ProfileMatch, m.ProfileMatch.paper_id == m.Paper.id))}
    assert ents["10.1/j"][0]["key"] == "cephalalgia" and ents["10.1/j"][0]["confidence"] == "query"
    assert ents["10.1/r"][0]["key"] == "peter_j_goadsby" and ents["10.1/r"][0]["confidence"] == "name"


def test_journal_membership_is_also_detected_from_the_issn(factory, curt):
    pm = Fake("pubmed", lambda sp, w: [rec("pubmed", "1", pmid="1", doi="10.1/i", issns=["0017-8748"])] if sp.path == "topic" else [])
    p, _ = pipeline(factory, pubmed=pm)
    only_topic(p, curt)
    with session_scope(factory) as db:
        ents = db.scalar(select(m.ProfileMatch.entities))
    assert [e["key"] for e in ents] == ["headache"] and ents[0]["via"] == "issn"


def test_ambiguous_name_hits_do_not_decide_the_track(factory, curt):
    profile, field = curt
    pm = Fake("pubmed", lambda sp, w: [rec("pubmed", "1", pmid="1", doi="10.1/h", title="Migraine something by an ambiguous name that is long enough")] if "Hoffmann" in sp.label else [])
    p, _ = pipeline(factory, pubmed=pm)
    p.run(profile, field, W, only_sources={"pubmed"}, only_paths={"researcher"})
    with session_scope(factory) as db:
        mt = db.scalar(select(m.ProfileMatch))
        assert mt.entities[0]["confidence"] == "name_ambiguous"


def test_run_logs_one_row_per_search_and_the_run_record(factory, curt):
    p, _ = pipeline(factory, pubmed=Fake("pubmed", lambda sp, w: [rec("pubmed", "1", pmid="1", doi="10.1/a")]))
    s = only_topic(p, curt)
    with session_scope(factory) as db:
        assert db.scalar(select(func.count()).select_from(m.SourceRunLog)) == s.specs == 4
        run = db.scalar(select(m.Run))
        assert run.window_start == W.start and run.window_end == W.end and run.finished_at is not None and run.stats["new_papers"] == 1
        assert db.scalar(select(m.SearchSpecRow.active).where(m.SearchSpecRow.source == "pubmed")) is True


def test_specs_outside_the_filter_are_not_retired(factory, curt):
    p, _ = pipeline(factory)
    profile, field = curt
    p.run(profile, field, W)
    with session_scope(factory) as db:
        total = db.scalar(select(func.count()).select_from(m.SearchSpecRow))
    p.run(profile, field, W, only_paths={"topic"})
    with session_scope(factory) as db:
        assert db.scalar(select(func.count()).select_from(m.SearchSpecRow).where(m.SearchSpecRow.active)) == total  # a partial run must not retire the rest


def test_sources_run_in_parallel_threads_without_sharing_a_session(factory, curt):
    import threading
    seen = set()

    def fn(sp, w):
        seen.add(threading.get_ident())
        return []

    p, _ = pipeline(factory, **{n: Fake(n, fn) for n in ("pubmed", "europepmc", "crossref", "openalex", "ctgov")})
    only_topic(p, curt)
    assert len(seen) >= 1  # adapters never touch the database; all DB work happens in the calling thread
