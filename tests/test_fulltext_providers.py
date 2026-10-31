"""Providers against a mocked network. Elsevier and Wiley are tested here only: no live key exists yet."""
import httpx
import pytest

from research_agent.domain.capabilities import Rights, Tier
from research_agent.domain.fulltext import ERROR, NOT_AVAILABLE, NOT_PERMITTED, OK, FullTextRequest
from research_agent.infrastructure.fulltext import pdf_text, providers as P
from research_agent.infrastructure.http import HttpClient
from test_fulltext_domain import ARTICLE

LONG = " ".join(["migraine"] * 400)
LONG_ARTICLE = ARTICLE.replace("<p>Randomised.</p>", f"<p>{LONG}</p>")
MIN_PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
           b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
           b"4 0 obj<</Length 60>>stream\nBT /F1 12 Tf 72 700 Td (Hello migraine world) Tj ET\nendstream endobj\n"
           b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\ntrailer<</Root 1 0 R/Size 6>>\n%%EOF")


def client(handler):
    seen = []

    def wrapped(req):
        seen.append(req)
        return handler(req)

    return HttpClient("t", transport=httpx.MockTransport(wrapped), sleep=lambda s: None, max_tries=2), seen


def req(**kw):
    base = dict(paper_id=1, doi="10.1016/j.x.2026.1", pmcid=None, title="t")
    base.update(kw)
    return FullTextRequest(**base)


# ------------------------------------------------------------------ Europe PMC (free)
def test_europepmc_fulltext_success_carries_the_licence():
    http, seen = client(lambda r: httpx.Response(200, text=LONG_ARTICLE))
    p = P.EuropePmcFullText(http)
    assert not p.supports(req()) and p.supports(req(pmcid="PMC1"))
    res = p.fetch(req(pmcid="PMC1"))
    assert res.outcome == OK and res.format == "jats_xml" and res.licence == "cc-by-nc" and res.rights == Rights.OPEN and res.words >= 400
    assert seen[0].url.path.endswith("/PMC1/fullTextXML")


def test_europepmc_fulltext_failure_modes():
    for status, outcome in ((404, NOT_AVAILABLE), (403, NOT_PERMITTED), (500, ERROR)):
        http, _ = client(lambda r, s=status: httpx.Response(s, text="x"))
        assert P.EuropePmcFullText(http).fetch(req(pmcid="PMC1")).outcome == outcome
    http, _ = client(lambda r: httpx.Response(200, text="<article><front/></article>"))
    assert P.EuropePmcFullText(http).fetch(req(pmcid="PMC1")).outcome == NOT_AVAILABLE
    http, _ = client(lambda r: httpx.Response(200, text=ARTICLE))  # a body of a few words is a stub, not the paper
    r = P.EuropePmcFullText(http).fetch(req(pmcid="PMC1"))
    assert r.outcome == NOT_AVAILABLE and "words" in r.detail


# ------------------------------------------------------------------ Springer Nature (free key)
def test_springer_needs_a_key_and_a_springer_prefix():
    http, seen = client(lambda r: httpx.Response(200, text="<response><records>" + LONG_ARTICLE + "</records></response>"))
    assert not P.SpringerOpenAccess(http, None).active() and P.SpringerOpenAccess(http, "K").active()
    p = P.SpringerOpenAccess(http, "K")
    assert p.supports(req(doi="10.1186/s10194-026-1")) and not p.supports(req(doi="10.1016/x"))
    res = p.fetch(req(doi="10.1186/s10194-026-1"))
    assert res.outcome == OK and dict(seen[0].url.params)["q"] == "doi:10.1186/s10194-026-1"


def test_springer_paywalled_paper_is_not_available():
    http, _ = client(lambda r: httpx.Response(200, text="<response><records/></response>"))
    assert P.SpringerOpenAccess(http, "K").fetch(req(doi="10.1007/x")).outcome == NOT_AVAILABLE


# ------------------------------------------------------------------ PDF copies (free)
def test_pdftotext_reads_a_real_pdf():
    if not pdf_text.available():
        pytest.skip("pdftotext not installed")
    assert "Hello migraine world" in pdf_text.pdf_to_text(MIN_PDF)
    assert pdf_text.pdf_to_text(b"<html>blocked</html>") is None


def test_open_access_pdf_tries_links_and_survives_blocked_hosts(monkeypatch):
    monkeypatch.setattr(pdf_text, "available", lambda: True)
    monkeypatch.setattr(pdf_text, "pdf_to_text", lambda data: LONG if data.startswith(b"%PDF") else None)

    def handler(r):
        return httpx.Response(403) if r.url.host == "blocked.test" else httpx.Response(200, content=MIN_PDF)

    http, seen = client(handler)
    p = P.OpenAccessPdf(http)
    links = [{"kind": "pdf", "url": "https://blocked.test/a.pdf", "is_free": True, "licence": None},
             {"kind": "pdf", "url": "https://ok.test/a.pdf", "is_free": True, "licence": "cc-by"},
             {"kind": "pdf", "url": "https://paid.test/a.pdf", "is_free": None, "licence": None}]
    assert not p.supports(req(links=[])) and p.supports(req(links=links))
    res = p.fetch(req(links=links))
    assert res.outcome == OK and res.format == "pdf_text" and res.licence == "cc-by" and res.source_url == "https://ok.test/a.pdf"
    assert "paid.test" not in {r.url.host for r in seen}  # only links known to be free are tried


def test_open_access_pdf_when_every_link_fails(monkeypatch):
    monkeypatch.setattr(pdf_text, "available", lambda: True)
    http, _ = client(lambda r: httpx.Response(403))
    res = P.OpenAccessPdf(http).fetch(req(links=[{"kind": "pdf", "url": "https://x.test/a.pdf", "is_free": True}]))
    assert res.outcome == NOT_PERMITTED


def test_open_access_pdf_inactive_without_poppler(monkeypatch):
    monkeypatch.setattr(pdf_text, "available", lambda: False)
    http, _ = client(lambda r: httpx.Response(200))
    info = P.OpenAccessPdf(http).info()
    assert not info.active and "pdftotext" in info.detail


# ------------------------------------------------------------------ Elsevier (premium)
def test_elsevier_is_off_without_a_key_and_premium_when_on():
    http, _ = client(lambda r: httpx.Response(200))
    off, on = P.ElsevierFullText(http, None).info(), P.ElsevierFullText(http, "K").info()
    assert not off.active and "ELSEVIER_API_KEY" in off.detail and on.active and on.tier == Tier.PREMIUM


def test_elsevier_request_and_entitled_rights():
    http, seen = client(lambda r: httpx.Response(200, text=LONG))
    p = P.ElsevierFullText(http, "KEY", "INST")
    assert p.supports(req()) and not p.supports(req(doi="10.1002/x"))
    res = p.fetch(req())
    h = seen[0].headers
    assert h["x-els-apikey"] == "KEY" and h["x-els-insttoken"] == "INST" and h["accept"] == "text/plain"
    assert seen[0].url.path == "/content/article/doi/10.1016/j.x.2026.1"
    assert res.outcome == OK and res.rights == Rights.ENTITLED and res.licence is None and res.format == "plain_text"


def test_elsevier_open_access_paper_is_open_rights():
    http, _ = client(lambda r: httpx.Response(200, text=LONG))
    res = P.ElsevierFullText(http, "K").fetch(req(licences=["https://creativecommons.org/licenses/by/4.0/"]))
    assert res.rights == Rights.OPEN and res.licence == "cc-by"


def test_elsevier_no_entitlement_and_abstract_only_responses():
    http, _ = client(lambda r: httpx.Response(401, text="not entitled"))
    assert P.ElsevierFullText(http, "K").fetch(req()).outcome == NOT_PERMITTED
    http, _ = client(lambda r: httpx.Response(200, text="Abstract only. " * 10))
    assert P.ElsevierFullText(http, "K").fetch(req()).outcome == NOT_AVAILABLE
    http, _ = client(lambda r: httpx.Response(404))
    assert P.ElsevierFullText(http, "K").fetch(req()).outcome == NOT_AVAILABLE


def test_error_messages_never_contain_keys():
    http, _ = client(lambda r: httpx.Response(500, text="boom api_key=SECRET"))
    res = P.SpringerOpenAccess(http, "SECRET").fetch(req(doi="10.1007/x"))
    assert res.outcome == ERROR and "SECRET" not in res.detail


# ------------------------------------------------------------------ Wiley (premium)
def test_wiley_token_header_pdf_and_prefixes(monkeypatch):
    monkeypatch.setattr(pdf_text, "available", lambda: True)
    monkeypatch.setattr(pdf_text, "pdf_to_text", lambda data: LONG)
    http, seen = client(lambda r: httpx.Response(200, content=MIN_PDF))
    p = P.WileyTdmFullText(http, "TOK")
    assert p.active() and p.tier == Tier.PREMIUM and p.supports(req(doi="10.1111/head.1")) and not p.supports(req(doi="10.1016/x"))
    res = p.fetch(req(doi="10.1002/abc/def"))
    assert seen[0].headers["wiley-tdm-client-token"] == "TOK" and "10.1002%2Fabc%2Fdef" in str(seen[0].url)
    assert res.outcome == OK and res.rights == Rights.ENTITLED and res.format == "pdf_text"
    assert not P.WileyTdmFullText(http, None).active()


def test_wiley_forbidden_is_not_permitted():
    http, _ = client(lambda r: httpx.Response(403))
    assert P.WileyTdmFullText(http, "TOK").fetch(req(doi="10.1002/x")).outcome == NOT_PERMITTED


# ------------------------------------------------------------------ HTTP: credentials never follow a redirect to another host
def test_credentials_are_dropped_on_cross_host_redirects_but_kept_on_same_host():
    def handler(r):
        if r.url.host == "api.pub.test" and r.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://cdn.other.test/file"})
        if r.url.host == "api.pub.test" and r.url.path == "/same":
            return httpx.Response(302, headers={"location": "/final"})
        return httpx.Response(200, text="ok")

    http, seen = client(handler)
    http.get("https://api.pub.test/start", host="x", min_interval=0, headers={"X-Key": "SECRET"})
    assert seen[0].headers.get("x-key") == "SECRET" and "x-key" not in seen[1].headers and seen[1].url.host == "cdn.other.test"
    http.get("https://api.pub.test/same", host="x", min_interval=0, headers={"X-Key": "SECRET"})
    assert seen[-1].headers.get("x-key") == "SECRET"


def test_a_plan_limited_429_is_not_retried():
    http, seen = client(lambda r: httpx.Response(429, text="Plan upgrade required"))
    from research_agent.infrastructure.http import HttpError
    with pytest.raises(HttpError):
        http.get("https://x.test/", host="x", min_interval=0)
    assert len(seen) == 1
    http2, seen2 = client(lambda r: httpx.Response(429, text="slow down"))
    with pytest.raises(HttpError):
        http2.get("https://x.test/", host="x", min_interval=0)
    assert len(seen2) == 2  # an ordinary 429 is retried


def test_a_credential_echoed_back_by_a_server_is_scrubbed_from_errors():
    from research_agent.infrastructure.http import HttpError
    http, _ = client(lambda r: httpx.Response(401, text="Invalid key LONGSECRET123 for this account"))
    with pytest.raises(HttpError) as e:
        http.get("https://x.test/", host="x", headers={"X-ELS-APIKey": "LONGSECRET123"})
    assert "LONGSECRET123" not in str(e.value) and "401" in str(e.value)
    http2, _ = client(lambda r: httpx.Response(403, text="token ABCDEFGH rejected"))
    with pytest.raises(HttpError) as e2:
        http2.post_json("https://x.test/", {}, host="x", headers={"Authorization": "Bearer ABCDEFGH"})
    assert "ABCDEFGH" not in str(e2.value)
    http3, _ = client(lambda r: httpx.Response(400, text="bad param SECRETPARAM1"))
    with pytest.raises(HttpError) as e3:
        http3.get("https://x.test/", {"api_key": "SECRETPARAM1"}, host="x")
    assert "SECRETPARAM1" not in str(e3.value)
