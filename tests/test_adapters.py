"""Adapters against a mocked network: request shapes, paging, truncation, error mapping and the silent-zero traps."""
import json
from datetime import date

import httpx

from research_agent.domain.models import DateWindow, SearchSpec
from research_agent.infrastructure.http import HttpClient
from research_agent.infrastructure.sources.clinicaltrials import ClinicalTrialsSource
from research_agent.infrastructure.sources.crossref import CrossrefSource
from research_agent.infrastructure.sources.europepmc import EuropePmcSource
from research_agent.infrastructure.sources.openalex import OpenAlexSource
from research_agent.infrastructure.sources.pubmed import PubMedSource
from conftest import fixture_json, fixture_text

W = DateWindow(date(2026, 10, 27), date(2026, 10, 30))


def mk(handler):
    seen = []

    def wrapped(req):
        seen.append(req)
        return handler(req)

    return HttpClient("t", transport=httpx.MockTransport(wrapped), sleep=lambda s: None), seen


def spec(source, **params):
    return SearchSpec("topic", source, "t", params)


def test_pubmed_uses_entry_date_and_pages_efetch():
    ids = [str(i) for i in range(1, 321)]

    def h(req):
        if req.url.path.endswith("esearch.fcgi"):
            return httpx.Response(200, json={"esearchresult": {"count": "320", "idlist": ids, "errorlist": {"phrasesnotfound": [], "fieldsnotfound": []}}})
        return httpx.Response(200, text="<PubmedArticleSet/>")

    http, seen = mk(h)
    res = PubMedSource(http, api_key="K", email="e@x.org", batch=150).run(spec("pubmed", term="migrain*[tiab]"), W)
    q = dict(seen[0].url.params)
    assert q["datetype"] == "edat" and q["mindate"] == "2026/10/27" and q["maxdate"] == "2026/10/30" and q["api_key"] == "K"
    assert len([r for r in seen if r.url.path.endswith("efetch.fcgi")]) == 3  # 320 ids in batches of 150
    assert res.total_reported == 320 and res.error is None and res.requests == 4


def test_pubmed_bad_field_is_an_error_but_unknown_phrase_is_not():
    bad, _ = mk(lambda r: httpx.Response(200, json={"esearchresult": {"count": "0", "idlist": [], "errorlist": {"fieldsnotfound": ["Foo"], "phrasesnotfound": []}}}))
    assert PubMedSource(bad, api_key=None, email=None).run(spec("pubmed", term="x"), W).error
    ok, _ = mk(lambda r: httpx.Response(200, json={"esearchresult": {"count": "0", "idlist": [], "errorlist": {"phrasesnotfound": ["x"], "fieldsnotfound": []}}}))
    assert PubMedSource(ok, api_key=None, email=None).run(spec("pubmed", term="x"), W).error is None


def test_pubmed_failure_is_returned_not_raised_and_hides_the_key():
    http, _ = mk(lambda r: httpx.Response(400, text="api_key=SECRET rejected"))
    res = PubMedSource(http, api_key="SECRET", email=None).run(spec("pubmed", term="x"), W)
    assert res.error and "SECRET" not in res.error and res.status == "error"


def test_europepmc_cursor_paging_and_first_index_date():
    result = fixture_json("europepmc_search.json")["resultList"]["result"]
    pages = [{"hitCount": 3, "nextCursorMark": "c2", "resultList": {"result": result[:2]}},
             {"hitCount": 3, "nextCursorMark": "c2", "resultList": {"result": result[2:]}}]
    it = iter(pages)
    http, seen = mk(lambda r: httpx.Response(200, json=next(it)))
    res = EuropePmcSource(http).run(spec("europepmc", query="TITLE_ABS:migrain*"), W)
    assert len(res.records) == 3 and res.status == "ok"
    assert "FIRST_IDATE:[2026-10-27 TO 2026-10-30]" in dict(seen[0].url.params)["query"]
    assert "LANG:eng" not in dict(seen[0].url.params)["query"]  # would silently drop records with no language
    assert dict(seen[1].url.params)["cursorMark"] == "c2"


def test_truncation_is_reported():
    http, _ = mk(lambda r: httpx.Response(200, json={"hitCount": 500, "resultList": {"result": fixture_json("europepmc_search.json")["resultList"]["result"][:1]}}))
    assert EuropePmcSource(http).run(spec("europepmc", query="q"), W).status == "truncated"


def test_crossref_filter_uses_created_date_and_pages():
    items = fixture_json("crossref_works.json")["message"]["items"]
    pages = iter([{"message": {"total-results": 5, "next-cursor": "n1", "items": items[:3]}}, {"message": {"total-results": 5, "next-cursor": "n2", "items": items[3:]}}])
    http, seen = mk(lambda r: httpx.Response(200, json=next(pages)))
    res = CrossrefSource(http, mailto="m@x.org").run(spec("crossref", endpoint="works", query="migraine"), W)
    f = dict(seen[0].url.params)["filter"]
    assert "from-created-date:2026-10-27" in f and "until-created-date:2026-10-30" in f and "index" not in f
    assert len(res.records) == 5 and dict(seen[1].url.params)["cursor"] == "n1"


def test_crossref_unregistered_journal_issn_is_skipped_not_an_error():
    http, _ = mk(lambda r: httpx.Response(404, text="Resource not found."))
    res = CrossrefSource(http, mailto=None).run(spec("crossref", endpoint="journals/1474-4465/works", query="migraine"), W)
    assert res.status == "skipped" and res.error is None
    res = CrossrefSource(http, mailto=None).run(spec("crossref", endpoint="works", query="migraine"), W)
    assert res.status == "error"  # a 404 on the main endpoint is a real fault


def test_crossref_lookup_batches_dois():
    http, seen = mk(lambda r: httpx.Response(200, json={"message": {"items": fixture_json("crossref_works.json")["message"]["items"][:1]}}))
    recs, err = CrossrefSource(http, mailto=None).lookup([f"10.1/{i}" for i in range(85)])
    assert err is None and len(seen) == 3 and len(recs) == 3  # 40 + 40 + 5


def test_openalex_window_is_widened_and_credit_guard_stops_requests():
    works = fixture_json("openalex_works.json")
    http, seen = mk(lambda r: httpx.Response(200, json={"meta": {"count": 3, "next_cursor": None}, "results": works["results"]}, headers={"x-ratelimit-remaining": "9000"}))
    oa = OpenAlexSource(http, api_key="K", lookback_days=14)
    res = oa.run(spec("openalex", filter="title_and_abstract.search:migraine"), W)
    flt = dict(seen[0].url.params)["filter"]
    assert "from_publication_date:2026-10-16" in flt and "to_publication_date:2026-10-30" in flt  # 14 days after the 3-day overlap is subtracted
    assert len(res.records) == 3 and oa.credits_remaining == 9000
    http2, seen2 = mk(lambda r: httpx.Response(200, json={"meta": {"count": 0, "next_cursor": None}, "results": []}, headers={"x-ratelimit-remaining": "100"}))
    low = OpenAlexSource(http2, api_key="K", min_credits=500)
    low.run(spec("openalex", filter="f"), W)
    skipped = low.run(spec("openalex", filter="f"), W)
    assert skipped.status == "skipped" and "credit guard" in skipped.skipped_reason and len(seen2) == 1


def test_openalex_request_cap():
    http, seen = mk(lambda r: httpx.Response(200, json={"meta": {"count": 0, "next_cursor": None}, "results": []}))
    oa = OpenAlexSource(http, api_key=None, max_requests=2)
    for _ in range(4):
        last = oa.run(spec("openalex", filter="f"), W)
    assert len(seen) == 2 and last.status == "skipped"


def test_ctgov_uses_first_post_date_and_detects_a_dead_filter():
    data = fixture_json("ctgov_studies.json")
    http, seen = mk(lambda r: httpx.Response(200, json={**data, "nextPageToken": None}))
    res = ClinicalTrialsSource(http).run(spec("ctgov", term="migraine"), W)
    assert "AREA[StudyFirstPostDate]RANGE[2026-10-27,2026-10-30]" in dict(seen[0].url.params)["filter.advanced"] and len(res.records) == 2
    # a response missing totalCount means the filter was not understood: fail loudly instead of returning a quiet zero
    http2, _ = mk(lambda r: httpx.Response(200, json={"studies": []}))
    assert ClinicalTrialsSource(http2).run(spec("ctgov", term="migraine"), W).status == "error"


def test_source_status_rules():
    from research_agent.domain.models import SourceResult
    s = SearchSpec("topic", "pubmed", "t", {}, min_expected=1)
    assert SourceResult(s).status == "suspect_zero"
    assert SourceResult(SearchSpec("topic", "pubmed", "t", {})).status == "ok"
    assert SourceResult(s, error="x").status == "error" and SourceResult(s, skipped_reason="y").status == "skipped"
