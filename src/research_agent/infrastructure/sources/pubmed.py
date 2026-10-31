"""PubMed E-utilities adapter. Freshness uses `edat` (Entrez date); publication-date filters miss most new records."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date

from ...domain.identity import normalise_doi, normalise_issn, normalise_pmcid, normalise_pmid
from ...domain.models import Author, DateWindow, Link, PaperRecord, SearchSpec, SourceResult
from ..http import HttpClient, HttpError, redact
from .common import ymd

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
RETRACTED_TYPES = {"retracted publication"}
NOTICE_TYPES = {"retraction notice", "retraction of publication"}  # PubMed's name for the notice type, both spellings accepted
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _text(el: ET.Element | None) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def _ymd(el: ET.Element | None) -> date | None:
    """Full dates only; a month-only PubDate returns None so it is never mistaken for an exact day."""
    if el is None:
        return None
    try:
        y, m, d = el.findtext("Year"), el.findtext("Month"), el.findtext("Day")
        if not (y and m and d):
            return None
        mm = int(m) if m.isdigit() else _MONTHS[m[:3].lower()]
        return date(int(y), mm, int(d))
    except (ValueError, KeyError):
        return None


def _parse_book(art: ET.Element) -> PaperRecord | None:
    """NCBI Bookshelf chapters (StatPearls and similar) appear in PubMed as PubmedBookArticle, with no journal and no DOI."""
    doc = art.find("BookDocument")
    pmid = normalise_pmid(doc.findtext("PMID")) if doc is not None else None
    if doc is None or not pmid:
        return None
    accession = next((_text(e) for e in doc.findall("ArticleIdList/ArticleId") if e.get("IdType") == "bookaccession"), None)
    book = doc.find("Book")
    parts = []
    for at in doc.findall("Abstract/AbstractText"):
        label = at.get("Label")
        parts.append(f"{label}: {_text(at)}" if label and label.upper() != "UNLABELLED" else _text(at))
    authors = [Author(family=a.findtext("LastName") or a.findtext("CollectiveName") or "", given=a.findtext("ForeName") or "", initials=a.findtext("Initials") or "")
               for a in doc.findall("AuthorList/Author")]
    entrez = next((_ymd(p) for p in art.findall("PubmedBookData/History/PubMedPubDate") if p.get("PubStatus") == "entrez"), None)
    url = f"https://www.ncbi.nlm.nih.gov/books/{accession}/" if accession else f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    return PaperRecord(
        source="pubmed", source_id=pmid, title=_text(doc.find("ArticleTitle")) or _text(book.find("BookTitle") if book is not None else None), abstract="\n".join(p for p in parts if p),
        pmid=pmid, journal=_text(book.find("BookTitle")) if book is not None else None, languages=[_text(l) for l in doc.findall("Language")],
        pub_types=["Book Chapter"], authors=authors, entry_date=entrez, url=url,
        links=[Link("article", url, "pubmed"), Link("article", f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", "pubmed")])


def _pubdate_label(el: ET.Element | None) -> str | None:
    """Reader-facing journal date: '2026-10-05', '2026 Oct', '2026', or the free-text MedlineDate."""
    if el is None:
        return None
    full = _ymd(el)
    if full:
        return full.isoformat()
    y, m = el.findtext("Year"), el.findtext("Month")
    if y and m:
        return f"{y} {m[:3].title() if not m.isdigit() else m}"
    return y or (el.findtext("MedlineDate") or None)


def parse_efetch(xml_text: str) -> list[PaperRecord]:
    root = ET.fromstring(xml_text)
    out: list[PaperRecord] = [r for r in (_parse_book(b) for b in root.findall("PubmedBookArticle")) if r]
    for art in root.findall("PubmedArticle"):
        mc = art.find("MedlineCitation")
        article = mc.find("Article") if mc is not None else None
        if mc is None or article is None:
            continue
        pmid = normalise_pmid(mc.findtext("PMID"))
        if not pmid:
            continue
        ids = {e.get("IdType"): (e.text or "").strip() for e in art.findall("PubmedData/ArticleIdList/ArticleId")}
        doi = normalise_doi(ids.get("doi")) or next(
            (normalise_doi(e.text) for e in article.findall("ELocationID") if e.get("EIdType") == "doi"), None
        )
        pmcid = normalise_pmcid(ids.get("pmc"))
        title = _text(article.find("ArticleTitle"))
        if title.startswith("[") and title.endswith("]"):
            title = title[1:-1]
        parts = []
        for at in article.findall("Abstract/AbstractText"):
            label = at.get("Label")
            body = _text(at)
            parts.append(f"{label}: {body}" if label and label.upper() != "UNLABELLED" else body)
        ptypes = [_text(p) for p in article.findall("PublicationTypeList/PublicationType")]
        lowered = {p.lower() for p in ptypes}
        refs = mc.findall("CommentsCorrectionsList/CommentsCorrections")
        retracted = bool(lowered & RETRACTED_TYPES) or any(r.get("RefType") == "RetractionIn" for r in refs)
        notice = bool(lowered & NOTICE_TYPES)
        target = next((normalise_pmid(r.findtext("PMID")) for r in refs if r.get("RefType") == "RetractionOf"), None)
        issns = [i for i in (normalise_issn(e.text) for e in article.findall("Journal/ISSN")) if i]
        link_issn = normalise_issn(mc.findtext("MedlineJournalInfo/ISSNLinking"))
        if link_issn and link_issn not in issns:
            issns.append(link_issn)
        authors = []
        for a in article.findall("AuthorList/Author"):
            orcid = next((_text(i) for i in a.findall("Identifier") if i.get("Source") == "ORCID"), None)
            if a.findtext("CollectiveName"):
                authors.append(Author(family=a.findtext("CollectiveName") or ""))
            else:
                authors.append(Author(family=a.findtext("LastName") or "", given=a.findtext("ForeName") or "",
                                      initials=a.findtext("Initials") or "", orcid=orcid))
        entrez = next((_ymd(p) for p in art.findall("PubmedData/History/PubMedPubDate") if p.get("PubStatus") == "entrez"), None)
        epub = next((_ymd(a) for a in article.findall("ArticleDate") if a.get("DateType") == "Electronic"), None)
        published = epub or _ymd(article.find("Journal/JournalIssue/PubDate"))
        links = [Link("article", f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", "pubmed")]
        if doi:
            links.append(Link("doi", f"https://doi.org/{doi}", "pubmed"))
        if pmcid:
            links.append(Link("pmc", f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/", "pubmed", is_free=True))
        out.append(PaperRecord(
            source="pubmed", source_id=pmid, title=title, abstract="\n".join(p for p in parts if p), doi=doi, pmid=pmid, pmcid=pmcid,
            journal=article.findtext("Journal/Title"), issns=issns, languages=[_text(l) for l in article.findall("Language")],
            pub_types=ptypes, authors=authors, published_date=published, entry_date=entrez, url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            is_preprint="preprint" in lowered, retracted=retracted, retraction_notice=notice, retraction_target_pmid=target, links=links,
            extra={"published_exact": bool(epub or _ymd(article.find("Journal/JournalIssue/PubDate"))),
                   "published_label": epub.isoformat() if epub else _pubdate_label(article.find("Journal/JournalIssue/PubDate"))},
        ))
    return out


class PubMedSource:
    name = "pubmed"

    def __init__(self, http: HttpClient, *, api_key: str | None, email: str | None, tool: str = "research-agent", batch: int = 150):
        self.http, self.api_key, self.email, self.tool, self.batch = http, api_key, email, tool, batch
        self.interval = 0.11 if api_key else 0.35  # 10 req/s with a key, 3 without

    def _params(self, **p) -> dict:
        p["tool"] = self.tool
        if self.email:
            p["email"] = self.email
        if self.api_key:
            p["api_key"] = self.api_key
        return p

    def run(self, spec: SearchSpec, window: DateWindow) -> SourceResult:
        res = SourceResult(spec)
        before = self.http.requests["pubmed"]
        try:
            data = self.http.get_json(f"{EUTILS}/esearch.fcgi", self._params(
                db="pubmed", term=spec.params["term"], datetype="edat", mindate=ymd(window.start, "/"), maxdate=ymd(window.end, "/"),
                retmax=10000, retmode="json"), host="pubmed", min_interval=self.interval)
            es = data.get("esearchresult", {})
            # `errorlist` is always present (usually empty lists). A bad field tag is a real error; unknown phrases just mean no hits.
            if es.get("ERROR") or (es.get("errorlist") or {}).get("fieldsnotfound"):
                raise HttpError(200, f"esearch error: {es.get('ERROR') or es.get('errorlist')}")
            ids = es.get("idlist", [])
            res.total_reported = int(es.get("count", len(ids)))
            for i in range(0, len(ids), self.batch):
                xml_text = self.http.get_text(f"{EUTILS}/efetch.fcgi", self._params(
                    db="pubmed", id=",".join(ids[i:i + self.batch]), retmode="xml"), host="pubmed", min_interval=self.interval)
                res.records.extend(parse_efetch(xml_text))
        except (HttpError, ET.ParseError, KeyError, ValueError) as exc:
            res.error = redact(f"{type(exc).__name__}: {exc}")
        res.requests = self.http.requests["pubmed"] - before
        return res
