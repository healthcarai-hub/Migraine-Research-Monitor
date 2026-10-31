"""Europe PMC adapter. Freshness uses FIRST_IDATE (first index date). LANG:eng is NOT used: it drops records with no language."""
from __future__ import annotations

from ...domain.identity import normalise_doi, normalise_issn, normalise_pmcid, normalise_pmid
from ...domain.models import Author, DateWindow, Link, PaperRecord, SearchSpec, SourceResult
from ..http import HttpClient, HttpError, redact
from .common import clean_text, parse_date, strip_tags, ymd

SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
PAGE = 1000
MAX_RECORDS = 20000  # safety stop; a daily window is far smaller


def parse_result(r: dict) -> PaperRecord:
    source, rid = r.get("source", ""), str(r.get("id", ""))
    ptypes = list((r.get("pubTypeList") or {}).get("pubType") or [])
    lowered = {p.lower() for p in ptypes}
    doi = normalise_doi(r.get("doi"))
    pmcid = normalise_pmcid(r.get("pmcid"))
    pmid = normalise_pmid(r.get("pmid")) if source == "MED" or r.get("pmid") else None
    journal = ((r.get("journalInfo") or {}).get("journal") or {})
    issns = [i for i in (normalise_issn(journal.get("issn")), normalise_issn(journal.get("essn"))) if i]
    publisher = (r.get("bookOrReportDetails") or {}).get("publisher")
    authors = []
    for a in (r.get("authorList") or {}).get("author", []):
        orcid = (a.get("authorId") or {}).get("value") if (a.get("authorId") or {}).get("type") == "ORCID" else None
        authors.append(Author(family=a.get("lastName") or a.get("collectiveName") or "", given=a.get("firstName") or "",
                              initials=a.get("initials") or "", orcid=orcid))
    links = []
    if doi:
        links.append(Link("doi", f"https://doi.org/{doi}", "europepmc"))
    for u in (r.get("fullTextUrlList") or {}).get("fullTextUrl", []):
        free = u.get("availability") in ("Free", "Open access")
        kind = "pdf" if u.get("documentStyle") == "pdf" else ("pmc" if u.get("site") == "Europe_PMC" else "article")
        links.append(Link(kind, u.get("url", ""), "europepmc", is_free=free if free else None))
    return PaperRecord(
        source="europepmc", source_id=f"{source}:{rid}", title=strip_tags(r.get("title")), abstract=strip_tags(r.get("abstractText")),
        doi=doi, pmid=pmid, pmcid=pmcid, journal=clean_text(journal.get("title") or publisher), issns=issns,
        languages=[r["language"]] if r.get("language") else [], pub_types=ptypes, authors=authors,
        published_date=parse_date(r.get("firstPublicationDate")), entry_date=parse_date(r.get("firstIndexDate")),
        url=f"https://europepmc.org/article/{source}/{rid}", is_preprint=(source == "PPR") or "preprint" in lowered,
        retracted="retracted publication" in lowered, retraction_notice=bool(lowered & {"retraction notice", "retraction of publication"}),
        oa_status="open" if r.get("isOpenAccess") == "Y" else None, links=links,
        extra={"epmc_source": source, "in_epmc": r.get("inEPMC") == "Y", "has_pdf": r.get("hasPDF") == "Y", "license": r.get("license")},
    )


class EuropePmcSource:
    name = "europepmc"

    def __init__(self, http: HttpClient):
        self.http = http

    def run(self, spec: SearchSpec, window: DateWindow) -> SourceResult:
        res = SourceResult(spec)
        before = self.http.requests["europepmc"]
        field = spec.params.get("date_field", "FIRST_IDATE")
        query = f"({spec.params['query']}) AND {field}:[{ymd(window.start)} TO {ymd(window.end)}]"
        cursor = "*"
        try:
            while True:
                data = self.http.get_json(SEARCH, {"query": query, "resultType": "core", "pageSize": PAGE, "cursorMark": cursor, "format": "json"},
                                          host="europepmc", min_interval=0.15)
                if "resultList" not in data:
                    raise HttpError(200, f"unexpected response keys {sorted(data)[:5]}")
                res.total_reported = int(data.get("hitCount", 0))
                batch = data["resultList"].get("result", [])
                res.records.extend(parse_result(r) for r in batch)
                nxt = data.get("nextCursorMark")
                if not batch or not nxt or nxt == cursor or len(res.records) >= min(res.total_reported, MAX_RECORDS):
                    break
                cursor = nxt
        except (HttpError, ValueError, KeyError) as exc:
            res.error = redact(f"{type(exc).__name__}: {exc}")
        res.requests = self.http.requests["europepmc"] - before
        return res

    def lookup(self, dois: list[str]) -> tuple[list[PaperRecord], str | None]:
        """Fetch specific DOIs (abstract, PMC id, free full-text links). Returns (records, error)."""
        out: list[PaperRecord] = []
        try:
            for i in range(0, len(dois), 25):
                chunk = dois[i:i + 25]
                q = " OR ".join(f'DOI:"{d}"' for d in chunk)
                data = self.http.get_json(SEARCH, {"query": q, "resultType": "core", "pageSize": 100, "format": "json"}, host="europepmc", min_interval=0.15)
                out.extend(parse_result(r) for r in data.get("resultList", {}).get("result", []))
        except (HttpError, ValueError, KeyError, TypeError) as exc:
            return out, redact(f"{type(exc).__name__}: {exc}")
        return out, None
