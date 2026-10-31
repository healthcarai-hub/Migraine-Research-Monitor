"""Runtime settings read from the environment (and a local .env). Secrets are never logged or printed."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _secret() -> str | None:
    return field(default=None, repr=False)


_EMAIL = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")


def parse_recipients(raw: str) -> tuple[str, ...]:
    """'a@x.org, b@y.com' -> ('a@x.org', 'b@y.com'). Only plain addresses are accepted, so nothing odd can end up in a mail header."""
    out = tuple(x.strip() for x in raw.split(",") if x.strip())
    bad = [x for x in out if not _EMAIL.match(x)]
    if bad:
        raise ValueError(f"NEWSLETTER_RECIPIENTS has an entry that is not a plain email address: {bad[0]!r}")
    return out


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    contact_email: str | None = None
    config_dir: Path = PROJECT_ROOT / "config"
    overlap_days: int = 3
    # ---- credentials (all optional). Each one that is present switches on a provider or a higher tier of one.
    ncbi_api_key: str | None = _secret()             # PubMed: 10 requests/s instead of 3
    openalex_api_key: str | None = _secret()
    openalex_plan: str = "free"                      # "premium" = created/updated-date filters are available on the key
    openalex_lookback_days: int = 14                 # free plan only: publication-date lookback that stands in for the entry date
    openalex_min_credits: int = 500
    crossref_plus_token: str | None = _secret()      # Crossref Plus: higher rate limits
    crossref_min_interval: float | None = None       # seconds between Crossref requests; default depends on the token
    springer_api_key: str | None = _secret()         # Springer Nature Open Access API (free key) or a licensed key
    elsevier_api_key: str | None = _secret()         # ScienceDirect article retrieval; needs an institutional entitlement to return full text
    elsevier_inst_token: str | None = _secret()
    wiley_tdm_token: str | None = _secret()          # Wiley text-and-data-mining token (PDF full text)
    # ---- summaries: any OpenAI-compatible chat endpoint; the earlier newsletter agent used Groq through the same three settings
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_api_key: str | None = _secret()
    llm_model: str | None = None                     # no model = no LLM: summaries fall back to extracts from the abstract
    llm_request_interval: float = 2.1                # seconds between requests (Groq free tier); lower on a paid plan
    llm_max_retries: int = 6
    llm_reasoning_effort: str | None = None          # optional: low / medium / high for reasoning models such as gpt-oss
    llm_max_summaries_per_run: int = 300
    google_client_id: str | None = None
    google_client_secret: str | None = _secret()
    google_refresh_token: str | None = _secret()   # Gmail draft publishing (drafts only)
    outputs_dir: Path = PROJECT_ROOT / "outputs"
    # ---- application API (read-only). It refuses to start without a token.
    api_tokens: tuple[str, ...] = field(default=(), repr=False)   # comma separated in API_TOKENS; clients send 'Authorization: Bearer <token>'
    api_admin_tokens: tuple[str, ...] = field(default=(), repr=False)   # API_ADMIN_TOKENS: these may also change the newsletter settings; empty = nobody can
    api_database_url: str | None = None                            # API_DATABASE_URL: the connection for all data routes (a read-only role); default DATABASE_URL
    api_rate_limit_per_minute: int = 120                           # requests per client address per minute; 0 = off
    api_auth_failures_per_minute: int = 10                         # wrong-token attempts per client address per minute before it is blocked; 0 = off
    api_client_ip_header: str = ""                                 # API_CLIENT_IP_HEADER: header the host's proxy sets to the visitor's address (e.g. X-Real-IP); empty = use the connecting address
    newsletter_recipients: tuple[str, ...] = ()                    # NEWSLETTER_RECIPIENTS: comma separated 'To' addresses for the Gmail draft; overrides newsletter.recipients in the profile
    job_overlap_days: int = 3                                      # JOB_OVERLAP_DAYS: how far back the daily collection looks
    job_fulltext_limit: int = 500                                  # JOB_FULLTEXT_LIMIT: papers the weekly full-text pass handles per run
    api_admin_database_url: str | None = None                     # API_ADMIN_DATABASE_URL: the connection used for those changes (a role limited to newsletter_selections); default DATABASE_URL
    api_profile: str = "curt"                                     # the profile the API serves (one in v1)
    api_cors_origins: tuple[str, ...] = ()                        # browser front ends allowed to call the API
    api_docs: bool = False                                        # serve /docs and /openapi.json (off by default)
    # ---- routing overrides
    fulltext_order: tuple[str, ...] = ()             # provider names, first is tried first; empty = built-in order

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        e = os.environ.get
        default_db = f"sqlite:///{PROJECT_ROOT / 'data' / 'research_agent.sqlite'}"
        return cls(
            database_url=normalise_db_url(e("DATABASE_URL") or default_db),
            contact_email=e("CONTACT_EMAIL") or None,
            config_dir=Path(e("CONFIG_DIR") or PROJECT_ROOT / "config"),
            overlap_days=int(e("OVERLAP_DAYS", "3")),
            ncbi_api_key=e("NCBI_API_KEY") or None,
            openalex_api_key=e("OPENALEX_API_KEY") or None,
            openalex_plan=(e("OPENALEX_PLAN") or "free").lower(),
            openalex_lookback_days=int(e("OPENALEX_LOOKBACK_DAYS", "14")),
            openalex_min_credits=int(e("OPENALEX_MIN_CREDITS", "500")),
            crossref_plus_token=e("CROSSREF_PLUS_TOKEN") or None,
            crossref_min_interval=float(e("CROSSREF_MIN_INTERVAL")) if e("CROSSREF_MIN_INTERVAL") else None,
            springer_api_key=e("SPRINGER_API_KEY") or None,
            elsevier_api_key=e("ELSEVIER_API_KEY") or None,
            elsevier_inst_token=e("ELSEVIER_INST_TOKEN") or None,
            wiley_tdm_token=e("WILEY_TDM_TOKEN") or None,
            llm_base_url=(e("LLM_BASE_URL") or "https://api.groq.com/openai/v1").rstrip("/"),
            llm_api_key=e("LLM_API_KEY") or None,
            llm_model=e("LLM_MODEL") or None,
            llm_request_interval=float(e("LLM_REQUEST_INTERVAL_SECONDS", "2.1")),
            llm_max_retries=int(e("LLM_MAX_RETRIES", "6")),
            llm_reasoning_effort=e("LLM_REASONING_EFFORT") or None,
            llm_max_summaries_per_run=int(e("LLM_MAX_SUMMARIES_PER_RUN", "300")),
            google_client_id=e("GOOGLE_CLIENT_ID") or None,
            google_client_secret=e("GOOGLE_CLIENT_SECRET") or None,
            google_refresh_token=e("GOOGLE_REFRESH_TOKEN") or None,
            outputs_dir=Path(e("OUTPUTS_DIR") or PROJECT_ROOT / "outputs"),
            api_tokens=tuple(x.strip() for x in (e("API_TOKENS") or "").split(",") if x.strip()),
            api_admin_tokens=tuple(x.strip() for x in (e("API_ADMIN_TOKENS") or "").split(",") if x.strip()),
            api_admin_database_url=e("API_ADMIN_DATABASE_URL") or None,
            api_database_url=e("API_DATABASE_URL") or None,
            api_rate_limit_per_minute=int(e("API_RATE_LIMIT_PER_MINUTE") or 120),
            api_auth_failures_per_minute=int(e("API_AUTH_FAILURES_PER_MINUTE") or 10),
            api_client_ip_header=(e("API_CLIENT_IP_HEADER") or "").strip(),
            newsletter_recipients=parse_recipients(e("NEWSLETTER_RECIPIENTS") or ""),
            job_overlap_days=int(e("JOB_OVERLAP_DAYS") or 3),
            job_fulltext_limit=int(e("JOB_FULLTEXT_LIMIT") or 500),
            api_profile=e("API_PROFILE") or "curt",
            api_cors_origins=tuple(x.strip() for x in (e("API_CORS_ORIGINS") or "").split(",") if x.strip()),
            api_docs=(e("API_DOCS") or "").lower() in ("1", "true", "yes"),
            fulltext_order=tuple(x.strip() for x in (e("FULLTEXT_ORDER") or "").split(",") if x.strip()),
        )


def normalise_db_url(url: str) -> str:
    """Accept the postgres:// and postgresql:// forms that hosts hand out; use the psycopg 3 driver."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url
