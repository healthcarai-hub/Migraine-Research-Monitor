"""Composition root: builds the concrete pipeline from settings. The only place that knows every adapter."""
from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from . import __version__
from .application.fulltext import FullTextService
from .application.ingest import IngestPipeline
from .application.newsletter import NewsletterService
from .application.summaries import SummaryService
from .domain.field_config import load_profile
from .infrastructure.db.session import make_engine, make_session_factory
from .infrastructure.http import HttpClient
from .infrastructure.llm import ChatSummariser
from .infrastructure.publishers import FileDraftPublisher, GmailDraftPublisher, build_gmail_service
from .infrastructure.registry import Providers, build_providers
from .infrastructure.rendering import JinjaNewsletterRenderer
from .settings import Settings


def user_agent(settings: Settings) -> str:
    return f"research-agent/{__version__}" + (f" (mailto:{settings.contact_email})" if settings.contact_email else "")


def session_factory(settings: Settings) -> sessionmaker[Session]:
    return make_session_factory(make_engine(settings.database_url))


def providers(settings: Settings, http: HttpClient | None = None) -> Providers:
    return build_providers(settings, http or HttpClient(user_agent(settings)))


def build_pipeline(settings: Settings, factory: sessionmaker[Session] | None = None, http: HttpClient | None = None) -> IngestPipeline:
    p = providers(settings, http)
    return IngestPipeline(factory or session_factory(settings), p.discovery, p.enrichers)


def build_fulltext(settings: Settings, factory: sessionmaker[Session] | None = None, http: HttpClient | None = None) -> FullTextService:
    p = providers(settings, http)
    return FullTextService(factory or session_factory(settings), p.fulltext, p.enrichers)


def build_summary_service(settings: Settings, factory: sessionmaker[Session], field_name: str, max_words: int = 60,
                          llm_http: HttpClient | None = None) -> SummaryService:
    """Model-written summaries when LLM_MODEL and LLM_API_KEY are set, otherwise abstract extracts only."""
    llm = None
    if settings.llm_model and settings.llm_api_key:
        http = llm_http or HttpClient(user_agent(settings), max_tries=settings.llm_max_retries + 1)
        llm = ChatSummariser(http, base_url=settings.llm_base_url, api_key=settings.llm_api_key, model=settings.llm_model, field_name=field_name,
                             max_words=max_words, min_interval=settings.llm_request_interval, reasoning_effort=settings.llm_reasoning_effort)
    return SummaryService(factory, llm, max_per_run=settings.llm_max_summaries_per_run)


def build_newsletter(settings: Settings, factory: sessionmaker[Session] | None = None, http: HttpClient | None = None, *, field_name: str = "research",
                     max_words: int = 60, llm_http: HttpClient | None = None) -> NewsletterService:
    p = providers(settings, http)
    factory = factory or session_factory(settings)
    return NewsletterService(factory, JinjaNewsletterRenderer(), p.enrichers, build_summary_service(settings, factory, field_name, max_words, llm_http))


def build_publisher(settings: Settings, kind: str):
    if kind == "file":
        return FileDraftPublisher(settings.outputs_dir)
    if kind == "gmail":
        return GmailDraftPublisher(build_gmail_service(settings.google_client_id, settings.google_client_secret, settings.google_refresh_token))
    raise ValueError(f"unknown publisher {kind!r}")


def build_api(settings: Settings):
    """The application API for the profile named in API_PROFILE. Data is served over a read-only engine. Only when an admin token exists is a second, writable
    engine made, and the app uses it for nothing except the newsletter switches."""
    from .interfaces.api.app import create_app
    profile, field = load_profile(settings.config_dir, settings.api_profile)
    factory = make_session_factory(make_engine(settings.api_database_url or settings.database_url, read_only=True))
    write = make_session_factory(make_engine(settings.api_admin_database_url or settings.database_url)) if settings.api_admin_tokens else None
    return create_app(settings, factory, profile, field, write)
