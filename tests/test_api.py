"""The read-only application API: access control, what it shows and hides, filters, pagination, and that it cannot write."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from research_agent.infrastructure.db import models as m
from research_agent.infrastructure.db.models import Base
from research_agent.infrastructure.db.repository import Repository
from research_agent.infrastructure.db.session import make_engine, make_session_factory, session_scope
from research_agent.interfaces.api.app import ApiNotConfigured, create_app
from research_agent.settings import Settings
from conftest import rec

TOKEN = "test-token-123456"
NOW = datetime.now(timezone.utc)
J = {"type": "journal", "key": "cephalalgia", "track": "A", "via": "issn", "confidence": "id"}


def settings(**kw):
    base = dict(database_url="sqlite://", api_tokens=(TOKEN,))
    base.update(kw)
    return Settings(**base)


@pytest.fixture()
def env(tmp_path, curt):
    profile, field = curt
    url = f"sqlite:///{tmp_path / 'api.sqlite'}"
    write = make_engine(url)
    Base.metadata.create_all(write)
    wf = make_session_factory(write)
    ids = {}
    with session_scope(wf) as s:
        repo = Repository(s)
        repo.sync_config(profile, field)

        def add(name, n, *, gate="passed", lang="ok", days_ago=1, track="A", watched=False, entities=(), subjects=(), **kw):
            base = dict(sid=str(n), pmid=str(n), doi=f"10.1/p{n}", title=f"Migraine study {name} with a sufficiently long descriptive title", abstract="Conclusions: It worked well. Truly.")
            base.update(kw)
            p = repo.merge_record(rec(**base), NOW - timedelta(days=days_ago)).paper
            p.first_seen_at = NOW - timedelta(days=days_ago)
            repo.upsert_match(profile.id, p.id, gate_status=gate, language_status=lang, track=track, watched=watched, entities=list(entities),
                              subjects=[{"code": c, "name": nm, "track": t, "terms": [], "specific": True} for c, nm, t in subjects])
            s.flush()
            ids[name] = p.id
            return p

        add("watched", 1, watched=True, entities=[J], journal="Cephalalgia", subjects=[("2", "Trigeminovascular & neuropeptides", "A")], published_date=None)
        add("plain", 2, days_ago=3)
        add("old", 3, days_ago=40, track="B")
        add("preprint", 4, source="europepmc", sid="PPR:4", is_preprint=True, pmid=None, doi="10.1101/4")
        add("trial", 5, source="ctgov", sid="NCT5", nct_id="NCT5", doi=None, pmid=None, is_trial=True, url="https://clinicaltrials.gov/study/NCT5")
        add("deposit", 6, doi="10.5281/zenodo.6")
        add("letter", 7, title="Reader Response: a long migraine letter title that is long enough here", abstract="")
        bad = add("retracted", 8, retracted=True)
        notice = add("notice", 9, title="Retraction notice to a migraine study that was retracted later", retraction_notice=True)
        notice.retraction_target_id = bad.id
        add("failed_gate", 10, gate="failed")
        add("german", 11, lang="other", languages=["ger"])
        add("percent", 12, title="Migraine 100% effective claims in a study of sleep with a long title", abstract="Plain.")
        s.add(m.PaperFulltext(paper_id=ids["watched"], status="full_text", format="jats_xml", provider="europepmc_fulltext", tier="free", rights="open", licence_key="cc-by",
                              print_allowed=True, words=5000, text="Full text body.\n\nSecond paragraph.", source_url="https://x/y", fetched_at=NOW))
        s.add(m.PaperFulltext(paper_id=ids["plain"], status="abstract_and_links_only"))
        s.add(m.PaperSummary(paper_id=ids["watched"], model="qwen", prompt_version="v1", abstract_hash="h", text="A model-written summary of the watched paper."))
        s.add(m.PaperLink(paper_id=ids["watched"], kind="pdf", url="https://x/y.pdf", source="openalex", is_free=True))
        issue = m.Issue(profile_id=profile.id, issue_key="curt-2026-10-31", window_start=NOW - timedelta(days=7), window_end=NOW, status="published", subject="Digest",
                        html="<p>html body</p>", text="text body", stats={"counts": {"direct": 1}, "held_back": {"section cap (direct)": 3}, "left_out_as_stale": 2})
        s.add(issue)
        s.flush()
        s.add(m.IssueItem(issue_id=issue.id, profile_id=profile.id, paper_id=ids["watched"], role="digest", section="direct", position=1,
                          snapshot={"title": "t", "summary": "s", "summary_source": "llm", "fulltext": "full_text"}))
        run = repo.start_run(profile.id, NOW.date() - timedelta(days=3), NOW.date(), "cron")
        repo.finish_run(run, "ok", {"new_papers": 5, "passed_matches": 4, "errors": ["x"], "secret_looking": "do-not-expose"})
    def build(**kw):
        ro = make_session_factory(make_engine(url, read_only=True))
        return TestClient(create_app(settings(**kw), ro, profile, field))
    return build, ids, url


@pytest.fixture()
def client(env):
    return env[0]()


H = {"Authorization": f"Bearer {TOKEN}"}


# ---------------------------------------------------------------- access control
def test_the_api_refuses_to_exist_without_a_token(env, curt):
    profile, field = curt
    with pytest.raises(ApiNotConfigured, match="API_TOKENS"):
        create_app(settings(api_tokens=()), make_session_factory(make_engine("sqlite://")), profile, field)


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic " + TOKEN}, {"Authorization": TOKEN}, {"Authorization": "Bearer "}])
def test_data_routes_need_the_right_bearer_token(client, headers):
    for path in ("/v1/papers", "/v1/papers/1", "/v1/stats", "/v1/issues", "/v1/retractions", "/v1/profile", "/v1/runs", "/v1/issues/x/html"):
        r = client.get(path, headers=headers)
        assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer", path


def test_a_valid_token_works_and_several_tokens_can_be_configured(env):
    c = env[0](api_tokens=("first-token", "second-token-xyz"))
    assert c.get("/v1/stats", headers={"Authorization": "Bearer second-token-xyz"}).status_code == 200
    assert c.get("/v1/stats", headers={"Authorization": "Bearer first-token"}).status_code == 200
    assert c.get("/v1/stats", headers=H).status_code == 401
    assert c.get("/v1/stats", headers={"authorization": "bearer second-token-xyz"}).status_code == 200      # scheme is case-insensitive


def test_health_is_open_and_returns_no_data(client):
    r = client.get("/health")
    assert r.status_code == 200 and set(r.json()) == {"status", "version"}
    assert client.get("/ready").json() == {"status": "ready"}


def test_docs_are_off_unless_switched_on(env):
    assert env[0]().get("/docs").status_code == 404 and env[0]().get("/openapi.json").status_code == 404
    assert env[0](api_docs=True).get("/openapi.json").status_code == 200


def test_responses_are_never_cached_and_carry_safe_headers(client):
    r = client.get("/v1/stats", headers=H)
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff" and r.headers["referrer-policy"] == "no-referrer"


def test_cors_allows_only_configured_origins(env):
    c = env[0](api_cors_origins=("https://app.example.org",))
    ok = c.options("/v1/papers", headers={"Origin": "https://app.example.org", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"})
    bad = c.options("/v1/papers", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "https://app.example.org" and "access-control-allow-origin" not in bad.headers
    assert "access-control-allow-origin" not in env[0]().options("/v1/papers", headers={"Origin": "https://app.example.org", "Access-Control-Request-Method": "GET"}).headers


# ---------------------------------------------------------------- it cannot write
def test_the_database_connection_itself_refuses_writes(env):
    _, ids, url = env
    ro = make_engine(url, read_only=True)
    with ro.connect() as c:
        with pytest.raises(OperationalError):
            c.execute(text("update papers set title = 'x'"))
        assert c.execute(text("select count(*) from papers")).scalar() > 0


def test_there_are_no_write_routes(client):
    for method in ("post", "put", "patch", "delete"):
        for path in ("/v1/papers", "/v1/papers/1", "/v1/issues", "/v1/stats"):
            assert getattr(client, method)(path, headers=H).status_code == 405, (method, path)


# ---------------------------------------------------------------- papers: what is shown
def titles(r):
    return [i["title"] for i in r.json()["items"]]


def test_default_list_shows_only_relevant_visible_english_papers(client, env):
    r = client.get("/v1/papers", headers=H)
    assert r.status_code == 200
    ids = {i["id"] for i in r.json()["items"]}
    _, idmap, _ = env
    assert {idmap[k] for k in ("watched", "plain", "old", "preprint", "trial", "retracted", "percent")} <= ids
    for hidden in ("deposit", "letter", "notice", "failed_gate", "german"):
        assert idmap[hidden] not in ids, hidden
    assert r.json()["total"] == len(ids)


def test_include_hidden_adds_deposits_and_letters_but_never_failed_or_foreign_language_papers(client, env):
    _, idmap, _ = env
    ids = {i["id"] for i in client.get("/v1/papers?include_hidden=true", headers=H).json()["items"]}
    assert {idmap["deposit"], idmap["letter"], idmap["notice"]} <= ids and idmap["failed_gate"] not in ids and idmap["german"] not in ids


def test_each_paper_carries_what_a_reader_needs(client, env):
    _, idmap, _ = env
    item = next(i for i in client.get("/v1/papers", headers=H).json()["items"] if i["id"] == idmap["watched"])
    assert item["journal"] == "Cephalalgia" and item["url"] == "https://doi.org/10.1/p1" and item["track"] == "A" and item["subjects"] == ["Trigeminovascular & neuropeptides"]
    assert item["watched"] == [{"type": "journal", "name": "Cephalalgia"}]
    assert item["summary"] == "A model-written summary of the watched paper." and item["summary_source"] == "model"
    assert item["full_text"] == {"available": True, "printable": True, "words": 5000, "format": "jats_xml", "provider": "europepmc_fulltext"}
    plain = next(i for i in client.get("/v1/papers", headers=H).json()["items"] if i["id"] == idmap["plain"])
    assert plain["full_text"]["available"] is False and plain["summary"] == "It worked well. Truly." and plain["summary_source"] == "abstract"
    assert "abstract" not in item                                                    # the list is brief; the full abstract is on the detail route


# ---------------------------------------------------------------- filters, sorting, paging
def test_time_filters(client, env):
    _, idmap, _ = env
    recent = {i["id"] for i in client.get("/v1/papers?days=7", headers=H).json()["items"]}
    assert idmap["watched"] in recent and idmap["old"] not in recent
    since = (NOW - timedelta(days=10)).date().isoformat()
    assert idmap["old"] not in {i["id"] for i in client.get(f"/v1/papers?since={since}", headers=H).json()["items"]}


def test_text_search_needs_every_word_and_treats_wildcards_literally(client, env):
    _, idmap, _ = env
    got = lambda q: {i["id"] for i in client.get("/v1/papers", params={"q": q}, headers=H).json()["items"]}
    assert idmap["percent"] in got("sleep") and idmap["watched"] not in got("sleep")
    assert got("migraine sleep") == {idmap["percent"]} and got("migraine zzzzz") == set()
    assert got("100%") == {idmap["percent"]} and got("%") == {idmap["percent"]}            # a typed % is not a wildcard that matches everything
    assert got("SLEEP") == got("sleep")


def test_attribute_filters(client, env):
    _, idmap, _ = env
    ids = lambda qs: {i["id"] for i in client.get(f"/v1/papers?{qs}", headers=H).json()["items"]}
    assert ids("watched=true") == {idmap["watched"]} and idmap["watched"] not in ids("watched=false")
    assert ids("has_full_text=true") == {idmap["watched"]} and idmap["watched"] not in ids("has_full_text=false") and idmap["plain"] in ids("has_full_text=false")
    assert ids("kind=preprint") == {idmap["preprint"]} and ids("kind=trial") == {idmap["trial"]}
    assert idmap["old"] in ids("track=B") and idmap["watched"] not in ids("track=B")
    assert ids("retracted=true") == {idmap["retracted"]}
    assert ids("journal=cephal") == {idmap["watched"]}
    assert ids("content_type=repository_deposit&include_hidden=true") == {idmap["deposit"]}


def test_sorting_and_paging(client, env):
    r = client.get("/v1/papers?limit=2&offset=0", headers=H).json()
    r2 = client.get("/v1/papers?limit=2&offset=2", headers=H).json()
    assert len(r["items"]) == 2 and r["total"] == r2["total"] and r["limit"] == 2 and r2["offset"] == 2
    assert {i["id"] for i in r["items"]}.isdisjoint({i["id"] for i in r2["items"]})
    newest = client.get("/v1/papers?sort=newest", headers=H).json()["items"]
    assert [i["first_seen_at"] for i in newest] == sorted((i["first_seen_at"] for i in newest), reverse=True)
    assert client.get("/v1/papers?sort=published", headers=H).status_code == 200


@pytest.mark.parametrize("qs", ["limit=0", "limit=201", "offset=-1", "track=C", "kind=book", "sort=best", "days=0", "since=notadate"])
def test_bad_query_values_are_rejected(client, qs):
    assert client.get(f"/v1/papers?{qs}", headers=H).status_code == 422


# ---------------------------------------------------------------- one paper
def test_paper_detail(client, env):
    _, idmap, _ = env
    d = client.get(f"/v1/papers/{idmap['watched']}", headers=H).json()
    assert d["abstract"].startswith("Conclusions") and d["links"] and any(l["kind"] == "pdf" and l["free"] for l in d["links"])
    assert d["sent_in"] == [{"issue": "curt-2026-10-31", "role": "digest", "section": "direct"}] and d["found_by"] == []
    assert d["match"]["gate"] == "passed" and d["retraction"] == {"retracted": False, "flagged_by": [], "notice": None}
    bad = client.get(f"/v1/papers/{idmap['retracted']}", headers=H).json()
    assert bad["retraction"]["retracted"] and bad["retraction"]["notice"]["title"].startswith("Retraction notice")


def test_unknown_or_unmatched_papers_are_404(client, env):
    _, idmap, _ = env
    assert client.get("/v1/papers/999999", headers=H).status_code == 404
    assert client.get("/v1/papers/notanumber", headers=H).status_code == 422


def test_full_text_as_json_or_plain_text(client, env):
    _, idmap, _ = env
    j = client.get(f"/v1/papers/{idmap['watched']}/fulltext", headers=H).json()
    assert j["text"].startswith("Full text body") and j["provider"] == "europepmc_fulltext" and j["licence"] == "cc-by" and j["printable"] is True and j["words"] == 5000
    t = client.get(f"/v1/papers/{idmap['watched']}/fulltext?format=text", headers=H)
    assert t.text.startswith("Full text body") and t.headers["content-type"].startswith("text/plain") and t.headers["x-full-text-provider"] == "europepmc_fulltext"
    assert client.get(f"/v1/papers/{idmap['plain']}/fulltext", headers=H).status_code == 404       # abstract and links only
    assert client.get(f"/v1/papers/{idmap['trial']}/fulltext", headers=H).status_code == 404


# ---------------------------------------------------------------- retractions, editions, overview
def test_retractions_list_links_the_notice(client, env):
    _, idmap, _ = env
    r = client.get("/v1/retractions", headers=H).json()
    assert r["total"] == 1 and r["items"][0]["id"] == idmap["retracted"] and r["items"][0]["notice"]["paper_id"] == idmap["notice"]


def test_issues(client):
    lst = client.get("/v1/issues", headers=H).json()
    assert lst[0]["key"] == "curt-2026-10-31" and lst[0]["counts"] == {"direct": 1} and lst[0]["held_back"] == {"section cap (direct)": 3} and lst[0]["left_out_as_stale"] == 2
    one = client.get("/v1/issues/curt-2026-10-31", headers=H).json()
    assert one["items"][0]["summary"] == "s" and one["items"][0]["section"] == "direct"
    html = client.get("/v1/issues/curt-2026-10-31/html", headers=H)
    assert html.text == "<p>html body</p>" and html.headers["content-type"].startswith("text/html") and "default-src 'none'" in html.headers["content-security-policy"]
    assert client.get("/v1/issues/curt-2026-10-31/text", headers=H).text == "text body"
    for path in ("/v1/issues/nope", "/v1/issues/nope/html", "/v1/issues/nope/text"):
        assert client.get(path, headers=H).status_code == 404


def test_stats_runs_and_profile(client):
    st = client.get("/v1/stats", headers=H).json()
    assert st["relevant_papers"] >= 8 and st["with_full_text"] == 1 and st["retracted"] == 1 and st["by_content_type"]["journal_article"] >= 3
    assert st["last_collection"]["status"] == "ok" and st["last_collection"]["errors"] == 1 and st["latest_issue"]["key"] == "curt-2026-10-31"
    runs = client.get("/v1/runs", headers=H).json()
    assert runs[0]["stats"] == {"new_papers": 5, "passed_matches": 4} and "secret_looking" not in str(runs)          # only whitelisted stats are exposed
    pr = client.get("/v1/profile", headers=H).json()
    assert pr["id"] == "curt" and len(pr["journals"]) == 10 and len(pr["researchers"]) == 30 and len(pr["subjects"]) == 12 and "letter" in pr["hidden_content_types"]
    assert "openalex_ids" not in str(pr)


def test_no_credentials_appear_in_any_response(client):
    blob = ""
    for path in ("/v1/stats", "/v1/profile", "/v1/runs", "/v1/papers", "/v1/issues", "/health"):
        blob += client.get(path, headers=H).text
    assert TOKEN not in blob
