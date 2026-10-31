"""Full-text providers. Free providers work out of the box; premium providers switch on when their credential is configured.

Premium adapters (Elsevier, Wiley) are written from the publishers' documentation and covered by mocked-network tests only. They have NOT been
run against the live services because no keys exist yet: the first real run with a key is the real test.
"""
from __future__ import annotations

from urllib.parse import quote

from ...domain.capabilities import Cap, ProviderInfo, Rights, Tier
from ...domain.fulltext import ERROR, MIN_WORDS, NOT_AVAILABLE, NOT_PERMITTED, OK, FullTextRequest, FullTextResult, extract_abstract, jats_to_text, licence_key, text_matches_paper
from ..http import HttpClient, HttpError, redact
from . import pdf_text


def _rights_for(licence: str | None, default: Rights) -> Rights:
    return Rights.OPEN if licence and licence.startswith("cc") else default


def _req_licence(req: FullTextRequest) -> str | None:
    """An open licence recorded for the paper elsewhere (Crossref, OpenAlex), used when the text itself carries none."""
    return next((k for k in (licence_key(l) for l in req.licences) if k and k.startswith("cc")), None)


class BaseProvider:
    name = "base"
    tier = Tier.FREE
    priority = 100
    capabilities: frozenset[str] = frozenset()
    default_rights = Rights.UNKNOWN
    needs = ""       # credential or tool required
    upgrade = ""

    def __init__(self, http: HttpClient):
        self.http = http

    def active(self) -> bool:
        return True

    def inactive_reason(self) -> str:
        return ""

    def info(self) -> ProviderInfo:
        on = self.active()
        return ProviderInfo(self.name, "fulltext", self.tier, on, self.capabilities, "" if on else (self.inactive_reason() or f"needs {self.needs}"), self.upgrade)

    def supports(self, req: FullTextRequest) -> bool:
        return True

    def fetch(self, req: FullTextRequest) -> FullTextResult:  # pragma: no cover - interface
        raise NotImplementedError

    # ---- helpers
    def _fail(self, exc: HttpError, what: str = "") -> FullTextResult:
        if exc.status in (401, 403):
            return FullTextResult(NOT_PERMITTED, self.name, detail=f"HTTP {exc.status}{what}")
        if exc.status in (404, 410):
            return FullTextResult(NOT_AVAILABLE, self.name, detail=f"HTTP {exc.status}{what}")
        return FullTextResult(ERROR, self.name, detail=redact(str(exc))[:200])

    def _text_result(self, req: FullTextRequest, text: str, fmt: str, url: str, licence_url: str | None = None, abstract: str | None = None) -> FullTextResult:
        lic = licence_key(licence_url) or _req_licence(req)
        if fmt != "jats_xml" and not text_matches_paper(text, req.title, req.doi):
            return FullTextResult(NOT_AVAILABLE, self.name, detail="the text does not appear to be this paper (its DOI and title are not in the first pages): wrong link from an index")
        found, kind = (abstract, "jats") if abstract else ((None, None) if fmt == "jats_xml" else (extract_abstract(text) or (None, None)))
        res = FullTextResult(OK, self.name, text=text, format=fmt, source_url=url, licence_url=licence_url, licence=lic, rights=_rights_for(lic, self.default_rights),
                             abstract=found, abstract_kind=kind)
        if res.words < MIN_WORDS:
            return FullTextResult(NOT_AVAILABLE, self.name, detail=f"only {res.words} words: abstract or stub, not the paper")
        return res


class EuropePmcFullText(BaseProvider):
    """PubMed Central full text through Europe PMC (JATS XML). Cleanest source; the licence travels inside the XML."""

    name = "europepmc_fulltext"
    priority = 10
    capabilities = frozenset({Cap.FULLTEXT_JATS})
    default_rights = Rights.UNKNOWN

    def supports(self, req):
        return bool(req.pmcid)

    def fetch(self, req):
        url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{req.pmcid}/fullTextXML"
        try:
            xml = self.http.get_text(url, host="europepmc", min_interval=0.15)
        except HttpError as exc:
            return self._fail(exc)
        doc = jats_to_text(xml)
        if doc is None or not doc.has_body:
            return FullTextResult(NOT_AVAILABLE, self.name, detail="record has no body text")
        return self._text_result(req, doc.text, "jats_xml", url, doc.licence_url, doc.abstract)


class SpringerOpenAccess(BaseProvider):
    """Springer Nature Open Access API: JATS full text with the licence, for Springer, BMC and Nature papers that are open access."""

    name = "springer_open_access"
    priority = 20
    capabilities = frozenset({Cap.FULLTEXT_JATS})
    needs = "SPRINGER_API_KEY"
    upgrade = "a licensed Springer Nature key can be added as a second provider for subscription content"
    PREFIXES = ("10.1007", "10.1186", "10.1038", "10.1140", "10.1245", "10.1365")

    def __init__(self, http: HttpClient, api_key: str | None):
        super().__init__(http)
        self.api_key = api_key

    def active(self):
        return bool(self.api_key)

    def supports(self, req):
        return bool(req.doi) and req.doi_prefix in self.PREFIXES

    def fetch(self, req):
        try:
            xml = self.http.get_text("https://api.springernature.com/openaccess/jats", {"q": f"doi:{req.doi}", "api_key": self.api_key}, host="springer", min_interval=0.7)
        except HttpError as exc:
            return self._fail(exc)
        doc = jats_to_text(xml)
        if doc is None or not doc.has_body:
            return FullTextResult(NOT_AVAILABLE, self.name, detail="not in the open access collection")
        return self._text_result(req, doc.text, "jats_xml", f"https://api.springernature.com/openaccess/jats?q=doi:{req.doi}", doc.licence_url, doc.abstract)


class OpenAccessPdf(BaseProvider):
    """Free PDF copies found by OpenAlex or Europe PMC, converted to text. Publisher sites that block scripts simply fail through."""

    name = "open_access_pdf"
    priority = 50
    capabilities = frozenset({Cap.FULLTEXT_PDF})
    needs = "the pdftotext program (poppler)"

    def active(self):
        return pdf_text.available()

    def supports(self, req):
        return bool(req.free_pdf_links())

    def fetch(self, req):
        last = FullTextResult(NOT_AVAILABLE, self.name, detail="no PDF could be read")
        for link in req.free_pdf_links()[:3]:
            host = link["url"].split("/")[2] if "//" in link["url"] else "pdf"
            try:
                data = self.http.get_bytes(link["url"], host=f"pdf:{host}", min_interval=0.5)
            except HttpError as exc:
                last = self._fail(exc, f" from {host}")
                continue
            text = pdf_text.pdf_to_text(data)
            if not text:
                last = FullTextResult(NOT_AVAILABLE, self.name, detail=f"{host} did not return a readable PDF")
                continue
            res = self._text_result(req, text, "pdf_text", link["url"], link.get("licence"))
            if res.outcome == OK:
                return res
            last = res
        return last


class ElsevierFullText(BaseProvider):
    """Elsevier ScienceDirect article retrieval as plain text. Full text for paywalled papers needs an institutional entitlement on the key."""

    name = "elsevier_sciencedirect"
    tier = Tier.PREMIUM
    priority = 30
    capabilities = frozenset({Cap.FULLTEXT_TEXT})
    default_rights = Rights.ENTITLED
    needs = "ELSEVIER_API_KEY (plus ELSEVIER_INST_TOKEN or an institutional network for paywalled papers)"
    PREFIXES = ("10.1016",)

    def __init__(self, http: HttpClient, api_key: str | None, inst_token: str | None = None):
        super().__init__(http)
        self.api_key, self.inst_token = api_key, inst_token

    def active(self):
        return bool(self.api_key)

    def supports(self, req):
        return bool(req.doi) and req.doi_prefix in self.PREFIXES

    def fetch(self, req):
        headers = {"X-ELS-APIKey": self.api_key, "Accept": "text/plain"}
        if self.inst_token:
            headers["X-ELS-Insttoken"] = self.inst_token
        url = f"https://api.elsevier.com/content/article/doi/{quote(req.doi, safe='/')}"
        try:
            text = self.http.get_text(url, host="elsevier", min_interval=0.2, headers=headers)
        except HttpError as exc:
            return self._fail(exc)
        return self._text_result(req, text, "plain_text", url)


class WileyTdmFullText(BaseProvider):
    """Wiley text-and-data-mining API: returns the PDF for papers the token's agreement covers."""

    name = "wiley_tdm"
    tier = Tier.PREMIUM
    priority = 40
    capabilities = frozenset({Cap.FULLTEXT_PDF})
    default_rights = Rights.ENTITLED
    needs = "WILEY_TDM_TOKEN and the pdftotext program"
    PREFIXES = ("10.1002", "10.1111")

    def __init__(self, http: HttpClient, token: str | None):
        super().__init__(http)
        self.token = token

    def active(self):
        return bool(self.token) and pdf_text.available()

    def inactive_reason(self):
        return "needs WILEY_TDM_TOKEN" if not self.token else "needs the pdftotext program"

    def supports(self, req):
        return bool(req.doi) and req.doi_prefix in self.PREFIXES

    def fetch(self, req):
        url = f"https://api.wiley.com/onlinelibrary/tdm/v1/articles/{quote(req.doi, safe='')}"
        try:
            data = self.http.get_bytes(url, host="wiley", min_interval=0.5, headers={"Wiley-TDM-Client-Token": self.token})
        except HttpError as exc:
            return self._fail(exc)
        text = pdf_text.pdf_to_text(data)
        if not text:
            return FullTextResult(NOT_AVAILABLE, self.name, detail="response was not a readable PDF")
        return self._text_result(req, text, "pdf_text", url)
