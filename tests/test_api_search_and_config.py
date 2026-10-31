"""The search (date, journal, author, researcher filters; date, relevance, full-text sorts) and the newsletter settings routes, over HTTP."""
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from research_agent.domain.models import Author
from research_agent.infrastructure.db import models as m
from research_agent.infrastructure.db.models import Base
from research_agent.infrastructure.db.repository import Repository
from research_agent.infrastructure.db.session import make_engine, make_session_factory, session_scope
from research_agent.interfaces.api.app import create_app
from research_agent.settings import Settings
from conftest import rec

READ, ADMIN = "read-token-123456", "admin-token-654321"
H, HA = {"Authorization": f"Bearer {READ}"}, {"Authorization": f"Bearer {ADMIN}"}
NOW = datetime.now(timezone.utc)
J = lambda key, conf="id": {"type": "journal", "key": key, "track": "A", "via": "issn", "confidence": conf}
R = lambda key, conf="id": {"type": "researcher", "key": key, "track": "A", "via": "orcid", "confidence": conf}


@pytest.fixture()
def env(tmp_path, curt):
    """Five papers: A (followed journal + researcher, full text), B (followed journal), C (full text, no followed source), D (no exact date; researcher only by name), E."""
    profile, field = curt
    url = f"sqlite:///{tmp_path / 'search.sqlite'}"
    write = make_engine(url)
    Base.metadata.create_all(write)
    wf = make_session_factory(write)
    ids = {}
    with session_scope(wf) as s:
        repo = Repository(s)
        repo.sync_config(profile, field)

        def add(name, n, *, days_ago, journal, authors, published, entities=(), watched=False, text=False, **kw):
            base = dict(sid=str(n), pmid=str(n), doi=f"10.1/p{n}", journal=journal, published_date=published, authors=authors, abstract="Conclusions: it worked.")
            base.update(kw)
            p = repo.merge_record(rec(**base), NOW - timedelta(days=days_ago)).paper
            p.first_seen_at = NOW - timedelta(days=days_ago)
            repo.upsert_match(profile.id, p.id, gate_status="passed", language_status="ok", track="A", watched=watched, entities=list(entities), subjects=[])
            s.flush()
            if text:
                s.add(m.PaperFulltext(paper_id=p.id, status="full_text", format="jats_xml", provider="europepmc_fulltext", tier="free", rights="open", print_allowed=True, words=900, text="body"))
            ids[name] = p.id

        add("A", 1, days_ago=1, journal="Cephalalgia", published=date(2026, 10, 20), watched=True, text=True, title="Migraine CGRP antibody trial in adults",
            authors=[Author("Goadsby", "Peter", "PJ"), Author("Müller", "Karl", "K")], entities=[J("cephalalgia"), R("peter_j_goadsby")])
        add("B", 2, days_ago=2, journal="Headache", published=date(2026, 9, 1), watched=True, title="Sleep and migraine attacks", authors=[Author("Lipton", "Richard", "RB")],
            entities=[J("headache")])
        add("C", 3, days_ago=3, journal="Brain", published=date(2026, 6, 1), text=True, title="Migraine genetics and the genetics of aura", authors=[Author("Smith", "Jane", "J")])
        add("D", 4, days_ago=4, journal="Neurology", published=None, title="Migraine imaging", authors=[Author("Ashina", "Messoud", "M")],
            entities=[J("neurology"), R("messoud_ashina", "name_ambiguous")], watched=True)
        add("E", 5, days_ago=5, journal="Frontiers in Neurology", published=date(2026, 10, 1), title="Migraine in the clinic", authors=[Author("Smith", "John", "J")])

    def build(*, admin=True, **kw):
        base = dict(database_url=url, api_tokens=(READ,), api_admin_tokens=(ADMIN,) if admin else ())
        base.update(kw)
        ro = make_session_factory(make_engine(url, read_only=True))
        return TestClient(create_app(Settings(**base), ro, profile, field, wf if admin else None))

    return build, ids, wf, url


@pytest.fixture()
def client(env):
    return env[0]()


def found(client, **params):
    r = client.get("/v1/papers", params=params, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def names(client, env, **params):
    back = {v: k for k, v in env[1].items()}
    return [back[i["id"]] for i in found(client, **params)["items"]]


# ---------------------------------------------------------------- filters
def test_filter_by_publication_date(client, env):
    assert set(names(client, env, published_from="2026-09-01", published_to="2026-10-31")) == {"A", "B", "E"}
    assert set(names(client, env, published_from="2026-10-02")) == {"A"}
    assert set(names(client, env, published_to="2026-06-30")) == {"C"}
    assert "D" not in names(client, env, published_from="2000-01-01")               # no exact date: left out of date-range searches
    assert client.get("/v1/papers?published_from=yesterday", headers=H).status_code == 422


def test_filter_by_journal_key_name_or_several(client, env):
    assert names(client, env, journal="headache") == ["B"]
    assert names(client, env, journal="neurology") == ["D"]                         # a followed journal's key is exact: not 'Frontiers in Neurology'
    assert names(client, env, journal="frontiers") == ["E"]                         # anything else is part of a journal name
    assert set(names(client, env, journal=["cephalalgia", "headache"])) == {"A", "B"}
    assert names(client, env, journal="no such journal") == []


def test_filter_by_author_ignores_accents_and_case_and_needs_every_word(client, env):
    assert names(client, env, author="goadsby") == ["A"] and names(client, env, author="Goadsby P") == ["A"]
    assert names(client, env, author="muller") == ["A"] and names(client, env, author="MÜLLER") == ["A"]
    assert names(client, env, author="lipton goadsby") == []                         # both words must be in the paper's author list
    assert set(names(client, env, author=["lipton", "smith j"])) == {"B", "C", "E"}
    assert names(client, env, author="smith jane") == ["C"]
    assert client.get("/v1/papers?author=%25%25", headers=H).status_code == 422       # nothing searchable in the value


def test_filter_by_researcher_uses_identity_not_names(client, env):
    assert names(client, env, researcher="peter_j_goadsby") == ["A"] and names(client, env, researcher="Peter J. Goadsby") == ["A"]
    assert names(client, env, researcher="messoud_ashina") == []                    # D's link to this researcher is only a name match, so it is not counted
    r = client.get("/v1/papers?researcher=someone_else", headers=H)
    assert r.status_code == 422 and "unknown researcher" in r.json()["detail"]


def test_filters_combine_with_and(client, env):
    assert names(client, env, journal="cephalalgia", author="goadsby") == ["A"]
    assert names(client, env, journal="headache", author="goadsby") == []
    assert set(names(client, env, q="migraine", has_full_text="true")) == {"A", "C"}
    assert names(client, env, author="smith", published_from="2026-07-01") == ["E"]


def tag(env, curt, *, hits=(), topics=()):
    """Record which sources found which papers and which topics they matched (the fixture papers have neither)."""
    _, ids, wf, _ = env
    profile, field = curt
    code = {sub.name: sub for sub in field.subjects}
    with session_scope(wf) as s:
        repo = Repository(s)
        run = repo.start_run(profile.id, date(2026, 10, 1), date(2026, 10, 2), "test")
        s.flush()
        for name, source in hits:
            s.add(m.PaperHit(paper_id=ids[name], run_id=run.id, source=source, path="topic", label="x"))
        for name, topic in topics:
            mt = s.scalar(select(m.ProfileMatch).where(m.ProfileMatch.paper_id == ids[name]))
            mt.subjects = [*mt.subjects, {"code": code[topic].code, "name": topic, "track": "A", "terms": [], "specific": True}]
        s.flush()


def test_filter_by_source_matches_any_of_the_sources_that_found_the_paper(client, env, curt):
    tag(env, curt, hits=[("A", "pubmed"), ("A", "crossref"), ("B", "crossref"), ("C", "openalex")])
    assert set(names(client, env, source="crossref")) == {"A", "B"}
    assert names(client, env, source="PubMed") == ["A"]                              # the display name works too
    assert set(names(client, env, source=["pubmed", "openalex"])) == {"A", "C"}
    assert names(client, env, source="europepmc") == []
    assert names(client, env, source="crossref", journal="headache") == ["B"]          # combines with the other filters by AND
    r = client.get("/v1/papers?source=google", headers=H)
    assert r.status_code == 422 and "unknown source" in r.json()["detail"]


def test_filter_by_topic_takes_a_code_or_a_name_and_ors_several(client, env, curt):
    tag(env, curt, topics=[("A", "Migraine phenotype"), ("B", "Cortical excitability"), ("C", "Migraine phenotype"), ("C", "Genetics & channels")])
    phenotype, cortical = next(x.code for x in curt[1].subjects if x.name == "Migraine phenotype"), "Cortical excitability"
    assert set(names(client, env, topic=phenotype)) == {"A", "C"} and set(names(client, env, topic="migraine phenotype")) == {"A", "C"}
    assert names(client, env, topic=cortical) == ["B"]
    assert set(names(client, env, topic=[phenotype, cortical])) == {"A", "B", "C"}
    assert names(client, env, topic="Hormonal regulation") == []
    r = client.get("/v1/papers?topic=astrology", headers=H)
    assert r.status_code == 422 and "unknown topic" in r.json()["detail"]


def test_a_topic_code_is_matched_whole_not_as_part_of_another_code(client, env, curt):
    """Codes such as '1' and '11' must not be confused."""
    tag(env, curt, topics=[("A", "Migraine phenotype"), ("B", "Metabolism & vascular")])
    codes = {x.name: x.code for x in curt[1].subjects}
    assert codes["Migraine phenotype"] == "1" and codes["Metabolism & vascular"] == "11"
    assert names(client, env, topic="1") == ["A"] and names(client, env, topic="11") == ["B"]


def test_the_filters_route_counts_sources_and_topics(client, env, curt):
    tag(env, curt, hits=[("A", "pubmed"), ("A", "crossref"), ("A", "crossref"), ("B", "crossref")], topics=[("A", "Migraine phenotype"), ("C", "Migraine phenotype")])
    f = client.get("/v1/papers/filters", headers=H).json()
    assert {x["key"]: x["papers"] for x in f["sources"]} == {"pubmed": 1, "europepmc": 0, "crossref": 2, "openalex": 0, "ctgov": 0}     # each paper counted once per source
    assert next(x for x in f["sources"] if x["key"] == "ctgov")["name"] == "ClinicalTrials.gov"
    assert len(f["topics"]) == 12 and next(x for x in f["topics"] if x["name"] == "Migraine phenotype")["papers"] == 2 and next(x for x in f["topics"] if x["name"] == "Hormonal regulation")["papers"] == 0


# ---------------------------------------------------------------- sorting
def test_sort_by_date_published_newest_first_unknown_dates_last(client, env):
    assert names(client, env, sort="published") == ["A", "E", "B", "C", "D"]
    assert names(client, env, sort="published", direction="asc") == ["C", "B", "E", "A", "D"]


def test_sort_by_date_we_stored_it(client, env):
    assert names(client, env, sort="newest") == ["A", "B", "C", "D", "E"] and names(client, env, sort="newest", direction="asc") == ["E", "D", "C", "B", "A"]


def test_sort_by_full_text_puts_papers_with_text_first_then_newest(client, env):
    assert names(client, env, sort="fulltext") == ["A", "C", "B", "D", "E"]
    assert names(client, env, sort="fulltext", direction="asc") == ["B", "D", "E", "A", "C"]


def test_sort_by_relevance_scores_query_words_followed_sources_and_held_full_text(client, env):
    only_c = found(client, q="migraine genetics", sort="relevance")["items"]
    assert [i["id"] for i in only_c] == [env[1]["C"]] and only_c[0]["relevance"] == 3 + 3 + 3          # two title words and full text; every query word must be present
    got = found(client, q="migraine", sort="relevance")["items"]
    back = {v: k for k, v in env[1].items()}
    # A: title 3 + followed 3 + text 3. B, D: title + followed. C: title + text. E: title only. Ties: the more recently stored first.
    assert [(back[i["id"]], i["relevance"]) for i in got] == [("A", 9), ("B", 6), ("C", 6), ("D", 6), ("E", 3)]
    assert found(client, sort="relevance")["items"][0]["id"] == env[1]["A"]                         # without words, followed sources and held text decide
    assert [back[i["id"]] for i in found(client, q="migraine", sort="relevance", direction="asc")["items"]][0] == "E"


def test_a_word_in_the_title_counts_more_than_one_only_in_the_abstract(client, env):
    with session_scope(env[2]) as s:
        s.get(m.Paper, env[1]["E"]).abstract = "aura aura"
    items = found(client, q="aura", sort="relevance")["items"]
    assert [(i["id"], i["relevance"]) for i in items] == [(env[1]["C"], 3 + 3), (env[1]["E"], 1)]   # title word + full text, then an abstract-only word


def test_bad_sort_or_direction_is_refused(client):
    assert client.get("/v1/papers?sort=random", headers=H).status_code == 422 and client.get("/v1/papers?direction=sideways", headers=H).status_code == 422


# ---------------------------------------------------------------- the filter options
def test_filters_route_lists_what_a_form_can_offer(client, env):
    f = client.get("/v1/papers/filters", headers=H).json()
    assert f["papers"] == 5 and f["sort"] == ["newest", "published", "relevance", "fulltext"] and f["direction"] == ["desc", "asc"]
    assert {"name": "Cephalalgia", "papers": 1} in f["journals_in_database"]
    assert next(j for j in f["followed_journals"] if j["key"] == "cephalalgia") == {"key": "cephalalgia", "name": "Cephalalgia", "papers": 1}
    assert len(f["followed_journals"]) == 10 and len(f["researchers"]) == 30
    assert next(r for r in f["researchers"] if r["key"] == "peter_j_goadsby")["papers"] == 1 and next(r for r in f["researchers"] if r["key"] == "messoud_ashina")["papers"] == 0
    assert f["published_between"] == ["2026-06-01", "2026-10-20"]
    assert client.get("/v1/papers/filters").status_code == 401


# ---------------------------------------------------------------- newsletter settings
def test_anyone_with_a_token_can_read_the_settings(env):
    c = env[0]()
    v = c.get("/v1/newsletter/config", headers=H).json()
    assert len(v["subjects"]) == 12 and len(v["journals"]) == 10 and len(v["researchers"]) == 30 and len(v["sources"]) == 5 and v["switched_off"] == {}
    assert "newsletter" in v["applies_to"] and c.get("/v1/newsletter/config").status_code == 401
    assert c.get("/v1/newsletter/config", headers=HA).status_code == 200            # an admin token reads too
    assert c.get("/v1/papers", headers=HA).status_code == 200


def test_only_an_admin_token_can_change_the_settings(env):
    c = env[0]()
    body = {"journals": {"cephalalgia": False}}
    assert c.patch("/v1/newsletter/config", json=body).status_code == 401
    assert c.patch("/v1/newsletter/config", json=body, headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = c.patch("/v1/newsletter/config", json=body, headers=H)
    assert r.status_code == 403 and "admin token" in r.json()["detail"]
    for call in (lambda: c.put("/v1/newsletter/config/journals/cephalalgia", json={"enabled": False}, headers=H), lambda: c.delete("/v1/newsletter/config", headers=H)):
        assert call().status_code == 403
    assert c.get("/v1/newsletter/config", headers=H).json()["switched_off"] == {}


def test_changing_the_settings_is_off_when_no_admin_token_is_configured(env):
    c = env[0](admin=False)
    assert c.patch("/v1/newsletter/config", json={"journals": {"cephalalgia": False}}, headers=H).status_code == 403
    assert c.patch("/v1/newsletter/config", json={"journals": {"cephalalgia": False}}, headers=HA).status_code == 401
    assert c.get("/v1/newsletter/config", headers=H).status_code == 200


def test_patch_switches_several_things_and_get_shows_them(env):
    c = env[0]()
    r = c.patch("/v1/newsletter/config", headers=HA, json={"journals": {"cephalalgia": False}, "subjects": {"2": False}, "researchers": {"peter_j_goadsby": False}, "sources": {"openalex": False}})
    assert r.status_code == 200
    assert r.json()["switched_off"] == {"journals": ["cephalalgia"], "researchers": ["peter_j_goadsby"], "sources": ["openalex"], "subjects": ["2"]}
    again = c.get("/v1/newsletter/config", headers=H).json()
    assert again["switched_off"] == r.json()["switched_off"] and again["changed_at"]
    assert next(j for j in again["journals"] if j["key"] == "cephalalgia")["enabled"] is False and next(j for j in again["journals"] if j["key"] == "headache")["enabled"] is True


def test_put_switches_one_thing(env):
    c = env[0]()
    assert c.put("/v1/newsletter/config/sources/crossref", json={"enabled": False}, headers=HA).json()["switched_off"] == {"sources": ["crossref"]}
    assert c.put("/v1/newsletter/config/sources/crossref", json={"enabled": True}, headers=HA).json()["switched_off"] == {}
    assert c.put("/v1/newsletter/config/colours/red", json={"enabled": True}, headers=HA).status_code == 422
    assert c.put("/v1/newsletter/config/journals/nope", json={"enabled": True}, headers=HA).status_code == 422
    assert c.put("/v1/newsletter/config/journals/headache", json={"enabled": "no"}, headers=HA).status_code == 422
    assert c.put("/v1/newsletter/config/journals/headache", json={"enabled": False, "extra": 1}, headers=HA).status_code == 422


def test_delete_puts_everything_back_on(env):
    c = env[0]()
    c.patch("/v1/newsletter/config", json={"journals": {"cephalalgia": False}}, headers=HA)
    r = c.delete("/v1/newsletter/config", headers=HA)
    assert r.status_code == 200 and r.json()["switched_off"] == {} and c.get("/v1/newsletter/config", headers=H).json()["switched_off"] == {}


def test_bad_requests_are_refused_and_change_nothing(env):
    c = env[0]()
    for body in [{}, {"journals": {}}, {"journals": {"cephalalgia": False, "nope": False}}, {"colours": {"a": True}}, {"journals": {"cephalalgia": "false"}}, {"journals": {"cephalalgia": 0}},
                 {"sources": {k: False for k in ("pubmed", "europepmc", "crossref", "openalex", "ctgov")}}]:
        assert c.patch("/v1/newsletter/config", json=body, headers=HA).status_code == 422, body
    assert c.patch("/v1/newsletter/config", content="not json", headers={**HA, "Content-Type": "application/json"}).status_code == 422
    assert c.get("/v1/newsletter/config", headers=H).json()["switched_off"] == {}


def test_switching_things_off_changes_the_newsletter_only_not_the_search_or_the_data(env):
    c, wf = env[0](), env[2]
    before = names(c, env, sort="newest")
    c.patch("/v1/newsletter/config", headers=HA, json={"journals": {"cephalalgia": False, "headache": False}, "sources": {"pubmed": False}, "subjects": {"1": False}})
    assert names(c, env, sort="newest") == before and names(c, env, journal="cephalalgia") == ["A"]
    assert len(found(c, watched="true")["items"]) == 3                                # the stored 'watched' flags are untouched
    with session_scope(wf) as s:
        assert s.scalar(select(func.count()).select_from(m.Paper)) == 5


def test_the_data_routes_still_cannot_write(env):
    c = env[0]()
    for method in ("post", "put", "patch", "delete"):
        for path in ("/v1/papers", "/v1/papers/1", "/v1/papers/filters", "/v1/issues", "/v1/stats"):
            assert getattr(c, method)(path, headers=HA).status_code == 405, (method, path)
    with make_engine(env[3], read_only=True).connect() as conn:                      # the data connection itself refuses writes
        with pytest.raises(Exception):
            conn.execute(text("update papers set title = 'x'"))


# ---------------------------------------------------------------- the web page
def test_the_web_page_is_served_without_a_token_and_holds_no_data(env):
    c = env[0]()
    r = c.get("/app")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html") and "Curt migraine research" in r.text
    assert "Cephalalgia" not in r.text and "10.1/p" not in r.text                        # nothing from the database is in the page itself
    assert c.get("/", follow_redirects=False).headers["location"] == "/app"
    assert c.get("/v1/papers").status_code == 401                                         # the data still needs the token


def test_the_web_page_can_run_only_its_own_script_and_call_only_this_server(env):
    c = env[0]()
    csp = c.get("/app").headers["content-security-policy"]
    assert "default-src 'none'" in csp and "script-src 'self'" in csp and "connect-src 'self'" in csp and "frame-ancestors 'none'" in csp and "unsafe-eval" not in csp
    assert "script-src 'self' 'unsafe-inline'" not in csp
    html = c.get("/app").text
    assert '<script src="/app/app.js"></script>' in html and html.count("<script") == 1    # no inline script
    js = c.get("/app/app.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]


def test_the_web_script_never_puts_paper_text_into_the_page_as_html(env):
    js = env[0]().get("/app/app.js").text
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert banned not in js, banned
    assert "safeUrl" in js and 'protocol === "https:"' in js                              # links from the data are only followed when http(s)


def test_the_page_has_chip_filters_a_published_dropdown_and_a_full_text_box_but_no_author_box(env):
    html = env[0]().get("/app").text
    for value in ('value="7">Last 7 days', 'value="15">Last 15 days', 'value="30">Last 30 days', 'value="90">Last 90 days', 'value="all">All time', 'value="custom">Custom range'):
        assert value in html
    assert html.index('<option value="7">') < html.index('<option value="15">')                        # 7 days is the first and so the default choice
    assert 'id="fulltext" type="checkbox"' in html and 'id="tray"' in html and 'id="grps"' in html
    assert html.index('id="grps"') < html.index('id="tray"')                                          # "Filtering by" comes after the chips and the other filters
    assert 'id="author"' not in html and "Author" not in html and 'id="researcher"' not in html and 'id="journal"' not in html     # the old boxes are gone


def test_the_script_asks_for_every_chip_filter_and_the_chosen_period(env):
    js = env[0]().get("/app/app.js").text
    for param in ('param: "source"', 'param: "journal"', 'param: "researcher"', 'param: "topic"', '"published_from"', '"has_full_text"'):
        assert param in js
    assert js.index('title: "Sources"') < js.index('title: "Journals"') < js.index('title: "Researchers"') < js.index('title: "Topics"')   # sources first
    assert "searchSeq" in js and "author" not in js.replace("authors", "")                                                          # an old answer never overwrites a newer one; no author box
