"""Crossref adapter. Freshness uses `created` (from-created-date); `from-index-date` returns re-indexed records and is ~18x too noisy."""
from __future__ import annotations

from ...domain.identity import normalise_doi, normalise_issn
from ...domain.models import Author, DateWindow, Link, PaperRecord, SearchSpec, SourceResult
from ..http import HttpClient, HttpError, redact
from .common import clean_text, parse_date_parts, strip_tags, to_date, ymd

BASE = "https://api.crossref.org"
SELECT = ("DOI,title,abstract,created,published,published-online,published-print,issued,type,container-title,ISSN,"
          "author,license,link,update-to,updated-by,URL")
ROWS = 1000
MAX_RECORDS = 20000
RETRACTION_TYPES = {"retraction", "withdrawal", "removal"}


def parse_item(it: dict) -> PaperRecord:
    doi = normalise_doi(it.get("DOI"))
    title = strip_tags((it.get("title") or [""])[0])
    ctype = it.get("type", "")
    updated_by = it.get("updated-by") or []
    update_to = it.get("update-to") or []
    retracted = any((u.get("type") or "").lower() in RETRACTION_TYPES for u in updated_by)
    # A retracted article's own record may list a retraction that points at itself; only another DOI makes this record a notice.
    notice_target = next((t for t in (normalise_doi(u.get("DOI")) for u in update_to if (u.get("type") or "").lower() in RETRACTION_TYPES)
                         if t and t != doi), None)
    published = None
    for key in ("published-online", "published", "issued", "published-print"):
        published = parse_date_parts(((it.get(key) or {}).get("date-parts") or [[]])[0])
        if published:
            break
    label = published.isoformat() if published else None
    if not published:
        for key in ("published-online", "published", "issued", "published-print"):
            parts = ((it.get(key) or {}).get("date-parts") or [[]])[0]
            if parts and parts[0]:
                label = f"{parts[0]}" if len(parts) == 1 else f"{parts[0]}-{int(parts[1]):02d}"
                break
    licences = [l["URL"] for l in it.get("license", []) if l.get("URL") and l.get("content-version") in (None, "vor")]
    links = [Link("doi", f"https://doi.org/{doi}", "crossref")] if doi else []
    for l in it.get("link", []):
        if l.get("URL") and l.get("content-type") == "application/pdf":
            links.append(Link("pdf", l["URL"], "crossref"))
    return PaperRecord(
        source="crossref", source_id=doi or "", title=title, abstract=strip_tags(it.get("abstract")), doi=doi,
        journal=clean_text((it.get("container-title") or [None])[0]), issns=[i for i in (normalise_issn(x) for x in it.get("ISSN", [])) if i],
        pub_types=[f"crossref:{ctype}"],
        authors=[Author(family=a.get("family") or a.get("name") or "", given=a.get("given") or "", orcid=_orcid(a)) for a in it.get("author", [])],
        published_date=published, entry_date=to_date((it.get("created") or {}).get("date-time")),
        url=it.get("URL"), is_preprint=(ctype == "posted-content"),
        retracted=retracted, retraction_notice=notice_target is not None, retraction_target_doi=notice_target, licences=licences, links=links,
        extra={"published_label": label, "updated_by": [{"doi": u.get("DOI"), "type": u.get("type")} for u in updated_by]},
    )


def _orcid(a: dict) -> str | None:
    o = a.get("ORCID")
    return o.rsplit("/", 1)[-1] if o else None


class CrossrefSource:
    name = "crossref"

    def __init__(self, http: HttpClient, *, mailto: str | None, plus_token: str | None = None, min_interval: float | None = None):
        self.http, self.mailto, self.plus_token = http, mailto, plus_token
        # Crossref Plus (token) allows a higher rate; the public pool is kept at 1 request/s. Plus limits are not verified here: override if needed.
        self.min_interval = min_interval if min_interval is not None else (0.25 if plus_token else 1.0)

    def _headers(self) -> dict[str, str] | None:
        return {"Crossref-Plus-API-Token": f"Bearer {self.plus_token}"} if self.plus_token else None

    def run(self, spec: SearchSpec, window: DateWindow) -> SourceResult:
        res = SourceResult(spec)
        before = self.http.requests["crossref"]
        endpoint = spec.params.get("endpoint", "works")
        flt = f"from-created-date:{ymd(window.start)},until-created-date:{ymd(window.end)}"
        cursor = "*"
        try:
            while True:
                params = {"query": spec.params["query"], "filter": flt, "rows": ROWS, "cursor": cursor, "select": SELECT}
                if self.mailto:
                    params["mailto"] = self.mailto
                data = self.http.get_json(f"{BASE}/{endpoint}", params, host="crossref", min_interval=self.min_interval, headers=self._headers())
                msg = data.get("message")
                if not isinstance(msg, dict) or "items" not in msg:
                    raise HttpError(200, "unexpected response shape")
                res.total_reported = msg.get("total-results")
                items = msg["items"]
                res.records.extend(parse_item(i) for i in items)
                cursor = msg.get("next-cursor")
                if not items or not cursor or len(res.records) >= min(res.total_reported or 0, MAX_RECORDS):
                    break
        except HttpError as exc:
            if exc.status == 404 and endpoint.startswith("journals/"):
                res.skipped_reason = "ISSN not registered at Crossref (404)"  # e.g. Lancet Neurology online, JAMA Neurology online
            else:
                res.error = redact(f"{type(exc).__name__}: {exc}")
        except (ValueError, KeyError, TypeError) as exc:
            res.error = redact(f"{type(exc).__name__}: {exc}")
        res.requests = self.http.requests["crossref"] - before
        return res

    def lookup(self, dois: list[str]) -> tuple[list[PaperRecord], str | None]:
        """Fetch specific DOIs (retraction state, licence, abstract). Returns (records, error)."""
        out: list[PaperRecord] = []
        try:
            for i in range(0, len(dois), 40):
                chunk = dois[i:i + 40]
                params = {"filter": ",".join(f"doi:{d}" for d in chunk), "rows": len(chunk), "select": SELECT}
                if self.mailto:
                    params["mailto"] = self.mailto
                data = self.http.get_json(f"{BASE}/works", params, host="crossref", min_interval=self.min_interval, headers=self._headers())
                out.extend(parse_item(it) for it in data["message"]["items"])
        except (HttpError, KeyError, TypeError, ValueError) as exc:
            return out, redact(f"{type(exc).__name__}: {exc}")
        return out, None
