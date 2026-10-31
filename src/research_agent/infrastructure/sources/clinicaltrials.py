"""ClinicalTrials.gov v2 adapter. Freshness uses StudyFirstPostDate. StudyFirstSubmitDate silently returns 0, so it is never used."""
from __future__ import annotations

from ...domain.models import DateWindow, Link, PaperRecord, SearchSpec, SourceResult
from ..http import HttpClient, HttpError, redact
from .common import parse_date, ymd

URL = "https://clinicaltrials.gov/api/v2/studies"


def parse_study(s: dict) -> PaperRecord | None:
    proto = s.get("protocolSection") or {}
    ident, status = proto.get("identificationModule") or {}, proto.get("statusModule") or {}
    nct = ident.get("nctId")
    if not nct:
        return None
    first_post = parse_date((status.get("studyFirstPostDateStruct") or {}).get("date"))
    return PaperRecord(
        source="ctgov", source_id=nct, nct_id=nct, title=ident.get("officialTitle") or ident.get("briefTitle") or "",
        abstract=(proto.get("descriptionModule") or {}).get("briefSummary") or "", journal="ClinicalTrials.gov",
        languages=["eng"], pub_types=["Clinical Trial Registration"], published_date=first_post, entry_date=first_post,
        url=f"https://clinicaltrials.gov/study/{nct}", is_trial=True,
        links=[Link("trial", f"https://clinicaltrials.gov/study/{nct}", "ctgov", is_free=True)],
        extra={"status": status.get("overallStatus"), "brief_title": ident.get("briefTitle"),
               "sponsor": ((proto.get("sponsorCollaboratorsModule") or {}).get("leadSponsor") or {}).get("name")},
    )


class ClinicalTrialsSource:
    name = "ctgov"

    def __init__(self, http: HttpClient):
        self.http = http

    def run(self, spec: SearchSpec, window: DateWindow) -> SourceResult:
        res = SourceResult(spec)
        before = self.http.requests["ctgov"]
        token = None
        try:
            while True:
                params = {"query.term": spec.params["term"], "filter.advanced": f"AREA[StudyFirstPostDate]RANGE[{ymd(window.start)},{ymd(window.end)}]",
                          "pageSize": 100, "countTotal": "true"}
                if token:
                    params["pageToken"] = token
                data = self.http.get_json(URL, params, host="ctgov", min_interval=0.2)
                if "studies" not in data or "totalCount" not in data:
                    raise HttpError(200, f"unexpected response keys {sorted(data)[:5]}")
                res.total_reported = data["totalCount"]
                res.records.extend(r for r in (parse_study(s) for s in data["studies"]) if r)
                token = data.get("nextPageToken")
                if not token:
                    break
        except (HttpError, ValueError, KeyError, TypeError) as exc:
            res.error = redact(f"{type(exc).__name__}: {exc}")
        res.requests = self.http.requests["ctgov"] - before
        return res
