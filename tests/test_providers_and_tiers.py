"""Tier-aware routing at the registry level: a credential in the settings changes what is active and how it is used."""
from datetime import date

import httpx

from research_agent.domain.capabilities import Cap, Tier
from research_agent.domain.models import DateWindow, SearchSpec
from research_agent.infrastructure.http import HttpClient
from research_agent.infrastructure.registry import build_providers, order_fulltext
from research_agent.infrastructure.sources.crossref import CrossrefSource
from research_agent.infrastructure.sources.openalex import OpenAlexSource
from research_agent.settings import Settings, normalise_db_url
from conftest import fixture_json

W = DateWindow(date(2026, 10, 27), date(2026, 10, 30))


def settings(**kw) -> Settings:
    return Settings(database_url="sqlite://", **kw)


def http(handler=lambda r: httpx.Response(200, json={})):
    seen = []

    def wrapped(req):
        seen.append(req)
        return handler(req)

    return HttpClient("t", transport=httpx.MockTransport(wrapped), sleep=lambda s: None, max_tries=2), seen


def test_free_only_setup():
    p = build_providers(settings(), http()[0])
    names = [x.name for x in p.fulltext]
    assert "elsevier_sciencedirect" not in names and "wiley_tdm" not in names and "springer_open_access" not in names
    assert names[0] == "europepmc_fulltext"
    info = {i.name: i for i in p.describe()}
    assert info["elsevier_sciencedirect"].active is False and "ELSEVIER_API_KEY" in info["elsevier_sciencedirect"].detail
    assert info["openalex"].tier == Tier.FREE and Cap.DISCOVER_PUBLICATION_DATE in info["openalex"].capabilities
    assert info["crossref"].tier == Tier.FREE


def test_adding_keys_switches_providers_on_and_orders_them(monkeypatch):
    from research_agent.infrastructure.fulltext import pdf_text
    monkeypatch.setattr(pdf_text, "available", lambda: True)
    p = build_providers(settings(springer_api_key="s", elsevier_api_key="e", wiley_tdm_token="w", crossref_plus_token="c", openalex_plan="premium"), http()[0])
    assert [x.name for x in p.fulltext] == ["europepmc_fulltext", "springer_open_access", "elsevier_sciencedirect", "wiley_tdm", "open_access_pdf"]
    info = {i.name: i for i in p.describe()}
    assert info["elsevier_sciencedirect"].active and info["elsevier_sciencedirect"].tier == Tier.PREMIUM
    assert info["openalex"].tier == Tier.PREMIUM and Cap.DISCOVER_ENTRY_DATE in info["openalex"].capabilities
    assert info["crossref"].tier == Tier.PREMIUM


def test_fulltext_order_override_puts_named_providers_first():
    p = build_providers(settings(springer_api_key="s", elsevier_api_key="e"), http()[0])
    reordered = order_fulltext(p.fulltext_all, ("elsevier_sciencedirect", "open_access_pdf"))
    assert [x.name for x in reordered][:2] == ["elsevier_sciencedirect", "open_access_pdf"]
    assert [x.name for x in reordered][2:] == ["europepmc_fulltext", "springer_open_access", "wiley_tdm"]


def test_settings_read_credentials_from_the_environment_without_exposing_them(monkeypatch, tmp_path):
    for k, v in {"ELSEVIER_API_KEY": "SECRETE", "WILEY_TDM_TOKEN": "SECRETW", "OPENALEX_PLAN": "PREMIUM", "FULLTEXT_ORDER": "wiley_tdm, open_access_pdf",
                 "CROSSREF_PLUS_TOKEN": "SECRETC", "DATABASE_URL": "postgres://u:pw@h/db"}.items():
        monkeypatch.setenv(k, v)
    s = Settings.from_env()
    assert s.elsevier_api_key == "SECRETE" and s.openalex_plan == "premium" and s.fulltext_order == ("wiley_tdm", "open_access_pdf")
    assert s.database_url == "postgresql+psycopg://u:pw@h/db"
    shown = repr(s)
    assert "SECRET" not in shown and "pw@" not in shown   # keys and the database password stay out of any printout
    assert normalise_db_url("postgresql://a@b/c") == "postgresql+psycopg://a@b/c" and normalise_db_url("sqlite:///x") == "sqlite:///x"


# ---------------------------------------------------------------- OpenAlex: premium uses the entry date, and degrades by itself
def oa_spec():
    return SearchSpec("topic", "openalex", "t", {"filter": "title_and_abstract.search:migraine"})


def test_openalex_premium_filters_on_the_created_date():
    ok = {"meta": {"count": 0, "next_cursor": None}, "results": []}
    h, seen = http(lambda r: httpx.Response(200, json=ok))
    OpenAlexSource(h, api_key="K", plan="premium").run(oa_spec(), W)
    f = dict(seen[0].url.params)["filter"]
    assert "from_created_date:2026-10-27" in f and "to_created_date:2026-10-30" in f and "publication_date" not in f  # exact, no lookback needed


def test_openalex_free_plan_uses_the_publication_lookback():
    ok = {"meta": {"count": 0, "next_cursor": None}, "results": []}
    h, seen = http(lambda r: httpx.Response(200, json=ok))
    OpenAlexSource(h, api_key="K", plan="free").run(oa_spec(), W)
    f = dict(seen[0].url.params)["filter"]
    assert "from_publication_date:2026-10-16" in f and "created_date" not in f


def test_openalex_premium_refused_falls_back_once_and_says_so():
    works = fixture_json("openalex_works.json")

    def handler(r):
        if "created_date" in dict(r.url.params).get("filter", ""):
            return httpx.Response(429, text='{"error":"Plan upgrade required"}')
        return httpx.Response(200, json={"meta": {"count": 3, "next_cursor": None}, "results": works["results"]})

    h, seen = http(handler)
    oa = OpenAlexSource(h, api_key="K", plan="premium")
    first = oa.run(oa_spec(), W)
    assert first.error is None and len(first.records) == 3 and any("premium" in n for n in first.notes) and oa.degraded and not oa.entry_date_mode
    n_requests = len(seen)
    second = oa.run(oa_spec(), W)
    assert len(seen) == n_requests + 1 and second.error is None   # the refusal is remembered: no second premium attempt
    assert sum("created_date" in dict(r.url.params).get("filter", "") for r in seen) == 1   # and the plan error was not retried


# ---------------------------------------------------------------- Crossref Plus
def test_crossref_plus_token_is_sent_as_a_header_and_speeds_pacing_up():
    h, seen = http(lambda r: httpx.Response(200, json={"message": {"total-results": 0, "items": [], "next-cursor": None}}))
    plus = CrossrefSource(h, mailto=None, plus_token="TOK")
    plus.run(SearchSpec("topic", "crossref", "t", {"endpoint": "works", "query": "migraine"}), W)
    assert seen[0].headers["crossref-plus-api-token"] == "Bearer TOK" and plus.min_interval < 1.0
    public = CrossrefSource(h, mailto=None)
    assert public.min_interval == 1.0 and public._headers() is None
    assert CrossrefSource(h, mailto=None, plus_token="T", min_interval=0.5).min_interval == 0.5
