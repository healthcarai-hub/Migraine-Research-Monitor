"""OpenAlex adapter. Free keys cannot filter by created/updated date, so this is a supplement: publication-date window with a wider
lookback, deduplicated by DOI downstream. A credit guard stops requests before the daily allowance runs out."""
from __future__ import annotations

from ...domain.identity import normalise_doi, normalise_issn, normalise_pmcid, normalise_pmid
from ...domain.models import Author, DateWindow, Link, PaperRecord, SearchSpec, SourceResult
from ..http import HttpClient, HttpError, redact
from .common import clean_text, parse_date, strip_tags, ymd

BASE = "https://api.openalex.org"
SELECT = ("id,doi,title,abstract_inverted_index,publication_date,created_date,type,language,ids,primary_location,best_oa_location,"
          "open_access,is_retracted,authorships")
PER_PAGE = 200
MAX_RECORDS = 5000
PREPRINT_TYPES = {"preprint"}


def rebuild_abstract(inv: dict | None) -> str:
    if not inv:
        return ""
    slots: dict[int, str] = {}
    for word, positions in inv.items():
        for p in positions:
            slots[p] = word
    return " ".join(slots[i] for i in sorted(slots))


def _tail(url: str | None) -> str | None:
    return url.rstrip("/").rsplit("/", 1)[-1] if url else None


def parse_work(w: dict) -> PaperRecord:
    ids = w.get("ids") or {}
    doi = normalise_doi(w.get("doi") or ids.get("doi"))
    src = ((w.get("primary_location") or {}).get("source") or {})
    best = w.get("best_oa_location") or {}
    oa = w.get("open_access") or {}
    wtype = (w.get("type") or "").lower()
    links = [Link("doi", f"https://doi.org/{doi}", "openalex")] if doi else []
    if best.get("pdf_url"):
        links.append(Link("pdf", best["pdf_url"], "openalex", is_free=True, licence=best.get("license")))
    if best.get("landing_page_url") and best.get("landing_page_url") != best.get("pdf_url"):
        links.append(Link("article", best["landing_page_url"], "openalex", is_free=True, licence=best.get("license")))
    authors = []
    for a in w.get("authorships", []):
        au = a.get("author") or {}
        name = au.get("display_name") or ""
        family = name.rsplit(" ", 1)[-1]
        given = name.rsplit(" ", 1)[0] if " " in name else ""
        authors.append(Author(family=family, given=given, orcid=_tail(au.get("orcid"))))
    return PaperRecord(
        source="openalex", source_id=_tail(w.get("id")) or "", title=strip_tags(w.get("title")), abstract=rebuild_abstract(w.get("abstract_inverted_index")),
        doi=doi, pmid=normalise_pmid(_tail(ids.get("pmid"))), pmcid=normalise_pmcid(_tail(ids.get("pmcid"))), journal=clean_text(src.get("display_name")),
        issns=[i for i in (normalise_issn(x) for x in (src.get("issn") or [])) if i],
        languages=[w["language"]] if w.get("language") else [], pub_types=[f"openalex:{wtype}"] if wtype else [], authors=authors,
        published_date=parse_date(w.get("publication_date")), entry_date=parse_date(w.get("created_date")),
        url=(w.get("primary_location") or {}).get("landing_page_url") or (f"https://doi.org/{doi}" if doi else None),
        is_preprint=wtype in PREPRINT_TYPES, retracted=bool(w.get("is_retracted")), oa_status=oa.get("oa_status"), links=links,
        extra={"oa_url": oa.get("oa_url"), "openalex_created": w.get("created_date")},
    )


def _plan_refused(exc: HttpError) -> bool:
    return any(w in str(exc).lower() for w in ("plan", "upgrade", "premium", "subscription"))


class OpenAlexSource:
    name = "openalex"

    def __init__(self, http: HttpClient, *, api_key: str | None, min_credits: int = 500, lookback_days: int = 14, max_requests: int = 300,
                 plan: str = "free"):
        self.http, self.api_key, self.min_credits, self.lookback_days, self.max_requests = http, api_key, min_credits, lookback_days, max_requests
        self.plan = plan
        self.degraded = False  # set when a premium-only filter is refused, so the rest of the run uses the free route
        self.credits_remaining: int | None = None
        self._used = 0

    @property
    def entry_date_mode(self) -> bool:
        """Premium plans can filter on the date OpenAlex created the record (exact, like the other sources); free plans cannot."""
        return self.plan == "premium" and not self.degraded

    def _guard(self) -> str | None:
        if self.credits_remaining is not None and self.credits_remaining < self.min_credits:
            return f"credit guard: {self.credits_remaining} credits left (< {self.min_credits})"
        if self._used >= self.max_requests:
            return f"request cap {self.max_requests} reached for this run"
        return None

    def _get(self, path: str, params: dict) -> dict:
        if self.api_key:
            params = {**params, "api_key": self.api_key}
        resp = self.http.get(f"{BASE}/{path}", params, host="openalex", min_interval=0.12)
        self._used += 1
        v = resp.headers.get("x-ratelimit-remaining")  # credits left today (10 per simple request on the free plan)
        try:
            if v is not None:
                self.credits_remaining = int(float(v))
        except ValueError:
            pass
        return resp.json()

    def run(self, spec: SearchSpec, window: DateWindow) -> SourceResult:
        res = SourceResult(spec)
        stop = self._guard()
        if stop:
            res.skipped_reason = stop
            return res
        before = self.http.requests["openalex"]
        if self.entry_date_mode:
            flt = f"{spec.params['filter']},from_created_date:{ymd(window.start)},to_created_date:{ymd(window.end)}"
        else:
            wide = window.widen_back(max(self.lookback_days - (window.end - window.start).days, 0))
            flt = f"{spec.params['filter']},from_publication_date:{ymd(wide.start)},to_publication_date:{ymd(wide.end)}"
        cursor = "*"
        try:
            while True:
                data = self._get("works", {"filter": flt, "per-page": PER_PAGE, "cursor": cursor, "select": SELECT})
                meta = data.get("meta") or {}
                res.total_reported = meta.get("count")
                batch = data.get("results", [])
                res.records.extend(parse_work(w) for w in batch)
                cursor = meta.get("next_cursor")
                stop = self._guard()
                if not batch or not cursor or len(res.records) >= min(res.total_reported or 0, MAX_RECORDS):
                    break
                if stop:
                    res.error = f"stopped early: {stop}"
                    break
        except HttpError as exc:
            if self.entry_date_mode and exc.status in (402, 403, 429) and _plan_refused(exc):
                self.degraded = True  # the key is not on the premium plan after all: fall back to the free route and say so
                retry = self.run(spec, window)
                retry.notes.append("premium created-date filter refused (plan); used publication-date lookback")
                return retry
            res.error = redact(f"{type(exc).__name__}: {exc}")
        except (ValueError, KeyError, TypeError) as exc:
            res.error = redact(f"{type(exc).__name__}: {exc}")
        res.requests = self.http.requests["openalex"] - before
        return res

    def lookup(self, dois: list[str]) -> tuple[list[PaperRecord], str | None]:
        """Batch DOI lookup (50 per request) for retraction flag, open-access location and abstract."""
        out: list[PaperRecord] = []
        try:
            for i in range(0, len(dois), 50):
                stop = self._guard()
                if stop:
                    return out, stop
                chunk = dois[i:i + 50]
                data = self._get("works", {"filter": "doi:" + "|".join(f"https://doi.org/{d}" for d in chunk), "per-page": 50, "select": SELECT})
                out.extend(parse_work(w) for w in data.get("results", []))
        except (HttpError, ValueError, KeyError, TypeError) as exc:
            return out, redact(f"{type(exc).__name__}: {exc}")
        return out, None
