"""The application API. It serves what the daily and weekly jobs have already stored and searches it; it never searches the sources.

The data is read-only. The one thing it can change is the newsletter's own switches (which topics, journals, researchers and sources the newsletter
is built from), and only with an admin token, over a separate database connection that the data routes never use.

Safety: every route except /health needs a bearer token; the app refuses to be created without one; the data connection is read-only; responses are never cached.
"""

import hmac
import math
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, StrictBool, StringConstraints
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from ... import __version__
from ...application.queries import MAX_PAGE, QueryService
from ...application.selection import SelectionService
from ...domain.field_config import FieldConfig, ProfileConfig
from ...infrastructure.db.session import session_scope
from ...settings import Settings
from .ratelimit import SlidingWindow


class ApiNotConfigured(RuntimeError):
    pass


class ConfigChanges(BaseModel):
    """Several switches at once, by kind: {"journals": {"cephalalgia": false}, "sources": {"openalex": true}}. All or nothing."""
    model_config = ConfigDict(extra="forbid")
    subjects: dict[str, StrictBool] = {}
    journals: dict[str, StrictBool] = {}
    researchers: dict[str, StrictBool] = {}
    sources: dict[str, StrictBool] = {}


class OneSwitch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


Word = Annotated[str, StringConstraints(max_length=120)]
WEB = Path(__file__).resolve().parents[1] / "web"
# The page may load only its own script, run no inline script, call only this server, and cannot be framed.
WEB_CSP = "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"


def create_app(settings: Settings, factory: sessionmaker[Session], profile: ProfileConfig, field: FieldConfig,
               write_factory: sessionmaker[Session] | None = None) -> FastAPI:
    if not settings.api_tokens:
        raise ApiNotConfigured("API_TOKENS is empty: the API will not start without at least one token (generate one with `research-agent api token`)")
    svc = QueryService(factory, profile, field)
    config = SelectionService(write_factory or factory, profile, field)
    tokens = [t.encode() for t in settings.api_tokens]
    admin_tokens = [t.encode() for t in settings.api_admin_tokens]
    can_change = write_factory is not None and bool(admin_tokens)
    bearer = HTTPBearer(auto_error=False)

    app = FastAPI(title="Research agent API", version=__version__, docs_url="/docs" if settings.api_docs else None,
                  redoc_url=None, openapi_url="/openapi.json" if settings.api_docs else None)
    if settings.api_cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.api_cors_origins), allow_methods=["GET", "PUT", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"])

    limiter = SlidingWindow(settings.api_rate_limit_per_minute)
    bad_tokens = SlidingWindow(settings.api_auth_failures_per_minute)

    def client_key(request: Request) -> str:
        """Who is asking, for the limits. Only the header named in API_CLIENT_IP_HEADER is believed (the host's own proxy sets it); a comma list uses its last entry.
        With no header configured, or none present, it is the connecting address, which behind a proxy is the proxy itself."""
        if settings.api_client_ip_header:
            seen = request.headers.get(settings.api_client_ip_header, "").split(",")[-1].strip()
            if seen:
                return seen
        return request.client.host if request.client else "unknown"

    def too_many(wait: float, what: str) -> JSONResponse:
        return JSONResponse({"detail": what}, status_code=429, headers={"Retry-After": str(max(1, math.ceil(wait)))})

    @app.middleware("http")
    async def rate_limit(request: Request, call_next):
        if request.url.path not in ("/health", "/ready"):
            wait = limiter.check(client_key(request))
            if wait:
                return too_many(wait, "too many requests; slow down and retry later")
        return await call_next(request)

    @app.middleware("http")
    async def safe_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("Cache-Control", "no-store")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        return resp

    def given_token(creds: HTTPAuthorizationCredentials | None) -> bytes:
        return (creds.credentials if creds and creds.scheme.lower() == "bearer" else "").encode()

    def matches(given: bytes, pool: list[bytes]) -> bool:
        ok = False
        for t in pool:                          # compare against every token without stopping early
            ok |= hmac.compare_digest(given, t)
        return ok

    def refuse(request: Request) -> HTTPException:
        bad_tokens.record(client_key(request))
        return HTTPException(status_code=401, detail="missing or invalid bearer token", headers={"WWW-Authenticate": "Bearer"})

    def locked_out(request: Request) -> None:
        """After too many wrong tokens from one address, further wrong or missing tokens from it get 429 for a while, which slows guessing to a crawl.
        A valid token is never refused because of this, so no one can lock real users out by failing on purpose (behind a proxy that hides the visitor's address,
        everyone looks like one address)."""
        wait = bad_tokens.wait(client_key(request))
        if wait:
            raise HTTPException(status_code=429, detail="too many wrong tokens from this address; try again later", headers={"Retry-After": str(max(1, math.ceil(wait)))})

    def require_token(request: Request, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> None:
        given = given_token(creds)
        if matches(given, tokens) | matches(given, admin_tokens):           # an admin token can read too
            return
        locked_out(request)
        raise refuse(request)

    def require_admin(request: Request, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> None:
        given = given_token(creds)
        if can_change and matches(given, admin_tokens):
            return
        if matches(given, tokens) | matches(given, admin_tokens):
            raise HTTPException(status_code=403, detail="this token cannot change the newsletter settings (an admin token is needed; see API_ADMIN_TOKENS)")
        locked_out(request)
        raise refuse(request)

    auth = [Depends(require_token)]
    admin = [Depends(require_admin)]
    Limit = Annotated[int, Query(ge=1, le=MAX_PAGE)]
    Offset = Annotated[int, Query(ge=0)]

    page, script = (WEB / "index.html").read_text(encoding="utf8"), (WEB / "app.js").read_text(encoding="utf8")

    @app.get("/", include_in_schema=False)
    def home() -> RedirectResponse:
        return RedirectResponse("/app")

    @app.get("/app", include_in_schema=False, response_class=HTMLResponse)
    def web_page() -> HTMLResponse:
        """The web page. It holds no data: it asks for a token and then calls the routes below."""
        return HTMLResponse(page, headers={"Content-Security-Policy": WEB_CSP})

    @app.get("/app/app.js", include_in_schema=False)
    def web_script() -> Response:
        return Response(script, media_type="text/javascript", headers={"Content-Security-Policy": WEB_CSP})

    @app.get("/health")
    def health() -> dict:
        """Liveness only: no data, no token needed."""
        return {"status": "ok", "version": __version__}

    @app.get("/ready")
    def ready() -> JSONResponse:
        """Can the API reach its database? (no data returned)"""
        try:
            with session_scope(factory) as s:
                s.execute(text("select 1"))
            return JSONResponse({"status": "ready"})
        except Exception:
            return JSONResponse({"status": "database unavailable"}, status_code=503)

    @app.get("/v1/whoami", dependencies=auth)
    def whoami(request: Request) -> dict:
        """The address the limits see for this request, so the setting API_CLIENT_IP_HEADER can be checked after deploying behind a proxy."""
        header = settings.api_client_ip_header
        return {"client": client_key(request), "connecting_address": request.client.host if request.client else None,
                "header_setting": header or None, "header_value": request.headers.get(header) if header else None}

    @app.get("/v1/profile", dependencies=auth)
    def get_profile() -> dict:
        return svc.profile_summary()

    @app.get("/v1/stats", dependencies=auth)
    def get_stats() -> dict:
        return svc.stats()

    @app.get("/v1/runs", dependencies=auth)
    def get_runs(limit: Annotated[int, Query(ge=1, le=100)] = 20) -> list[dict]:
        return svc.runs(limit)

    @app.get("/v1/papers", dependencies=auth)
    def list_papers(published_from: Annotated[date | None, Query(description="published on or after this date (YYYY-MM-DD); papers with no exact date are left out")] = None,
                    published_to: Annotated[date | None, Query(description="published on or before this date (YYYY-MM-DD)")] = None,
                    journal: Annotated[list[Word] | None, Query(max_length=20, description="a followed journal's key, or part of any journal name; repeat for several (OR)")] = None,
                    author: Annotated[list[Word] | None, Query(max_length=20, description="author name words, e.g. 'goadsby p'; accents and case ignored; repeat for several (OR)")] = None,
                    researcher: Annotated[list[Word] | None, Query(max_length=30, description="a followed researcher's key or name (see /v1/papers/filters); repeat for several (OR)")] = None,
                    q: Annotated[str | None, Query(max_length=200, description="words that must all appear in the title or abstract")] = None,
                    has_full_text: Annotated[bool | None, Query(description="true: only papers whose full text we hold; false: only those without")] = None,
                    sort: Annotated[Literal["newest", "published", "relevance", "fulltext"], Query(description="newest: when we stored it; published: publication date; relevance; fulltext: full text held first")] = "newest",
                    direction: Literal["desc", "asc"] = "desc",
                    days: Annotated[int | None, Query(ge=1, le=3650, description="first stored by us within the last N days")] = None,
                    since: Annotated[date | None, Query(description="first stored on or after this date (YYYY-MM-DD)")] = None,
                    track: Literal["A", "B"] | None = None, kind: Literal["article", "preprint", "trial"] | None = None,
                    content_type: Annotated[str | None, Query(max_length=40)] = None, watched: bool | None = None,
                    retracted: bool | None = None,
                    include_hidden: Annotated[bool, Query(description="include repository deposits, datasets, book chapters, letters and corrections")] = False,
                    limit: Limit = 50, offset: Offset = 0) -> dict:
        page = svc.papers(days=days, since=since, published_from=published_from, published_to=published_to, q=q, track=track, kind=kind, content_type=content_type,
                          watched=watched, has_full_text=has_full_text, retracted=retracted, journal=journal, author=author, researcher=researcher,
                          include_hidden=include_hidden, sort=sort, direction=direction, limit=limit, offset=offset)
        return {"total": page.total, "limit": page.limit, "offset": page.offset, "items": page.items}

    @app.get("/v1/papers/filters", dependencies=auth)
    def get_filters() -> dict:
        """What a search form can offer: journals in the database, the journals and researchers we follow, date ranges and the sort choices."""
        return svc.filters()

    @app.get("/v1/papers/{paper_id}", dependencies=auth)
    def get_paper(paper_id: int) -> dict:
        found = svc.paper(paper_id)
        if found is None:
            raise HTTPException(status_code=404, detail="no such paper for this profile")
        return found

    @app.get("/v1/papers/{paper_id}/fulltext", dependencies=auth, response_model=None)
    def get_fulltext(paper_id: int, format: Literal["json", "text"] = "json"):
        found = svc.fulltext(paper_id)
        if found is None:
            raise HTTPException(status_code=404, detail="no full text is held for this paper")
        if format == "text":
            return PlainTextResponse(found["text"], headers={"X-Full-Text-Provider": str(found["provider"]), "X-Full-Text-Licence": str(found["licence"])})
        return found

    @app.get("/v1/retractions", dependencies=auth)
    def get_retractions(limit: Limit = 50, offset: Offset = 0) -> dict:
        page = svc.retractions(limit=limit, offset=offset)
        return {"total": page.total, "limit": page.limit, "offset": page.offset, "items": page.items}

    @app.get("/v1/issues", dependencies=auth)
    def list_issues(limit: Annotated[int, Query(ge=1, le=100)] = 20) -> list[dict]:
        return svc.issues(limit)

    @app.get("/v1/issues/{key}", dependencies=auth)
    def get_issue(key: str) -> dict:
        found = svc.issue(key)
        if found is None:
            raise HTTPException(status_code=404, detail="no such issue")
        return found

    @app.get("/v1/issues/{key}/html", dependencies=auth, response_class=HTMLResponse)
    def get_issue_html(key: str) -> HTMLResponse:
        body = svc.issue_body(key, "html")
        if body is None:
            raise HTTPException(status_code=404, detail="no such issue")
        return HTMLResponse(body, headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:"})

    @app.get("/v1/issues/{key}/text", dependencies=auth, response_class=PlainTextResponse)
    def get_issue_text(key: str) -> PlainTextResponse:
        body = svc.issue_body(key, "text")
        if body is None:
            raise HTTPException(status_code=404, detail="no such issue")
        return PlainTextResponse(body)

    # ---------------------------------------------------------------- newsletter settings (the only writable thing)
    @app.get("/v1/newsletter/config", dependencies=auth)
    def get_config() -> dict:
        """Which search topics, journals, researchers and sources the newsletter is built from, each with enabled true/false."""
        return config.view()

    @app.patch("/v1/newsletter/config", dependencies=admin)
    def change_config(body: ConfigChanges) -> dict:
        changes = {k: v for k, v in body.model_dump().items() if v}
        if not changes:
            raise ValueError("no changes given: send e.g. {\"journals\": {\"cephalalgia\": false}}")
        return config.apply(changes)

    @app.put("/v1/newsletter/config/{kind}/{key}", dependencies=admin)
    def change_one(kind: Literal["subjects", "journals", "researchers", "sources"], key: str, body: OneSwitch) -> dict:
        return config.apply({kind: {key: body.enabled}})

    @app.delete("/v1/newsletter/config", dependencies=admin)
    def reset_config() -> dict:
        """Everything back on, as in the profile file."""
        return config.reset()

    @app.exception_handler(ValueError)
    async def bad_value(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    return app
