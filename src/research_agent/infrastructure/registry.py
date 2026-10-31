"""Builds every provider from the settings and reports which ones are active.

Adding a credential to the environment is the whole upgrade path: the matching provider switches on, a higher tier of an existing one is used,
and the router puts it in the right place. Nothing in the pipeline names a specific provider.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.capabilities import Cap, ProviderInfo, Tier
from ..ports import DiscoverySource, FullTextProvider, RecordEnricher
from ..settings import Settings
from .fulltext.providers import ElsevierFullText, EuropePmcFullText, OpenAccessPdf, SpringerOpenAccess, WileyTdmFullText
from .http import HttpClient
from .sources.clinicaltrials import ClinicalTrialsSource
from .sources.crossref import CrossrefSource
from .sources.europepmc import EuropePmcSource
from .sources.openalex import OpenAlexSource
from .sources.pubmed import PubMedSource


@dataclass
class Providers:
    discovery: dict[str, DiscoverySource]
    enrichers: dict[str, RecordEnricher]
    fulltext_all: list[FullTextProvider]
    info: list[ProviderInfo] = field(default_factory=list)

    @property
    def fulltext(self) -> list[FullTextProvider]:
        """Active full-text providers in routing order."""
        return [p for p in self.fulltext_all if p.active()]

    def describe(self) -> list[ProviderInfo]:
        return self.info + [p.info() for p in self.fulltext_all]


def order_fulltext(providers: list[FullTextProvider], override: tuple[str, ...]) -> list[FullTextProvider]:
    """Built-in order is by priority (open structured text first, then entitled text, then PDFs). FULLTEXT_ORDER puts named providers first."""
    rank = {name: i for i, name in enumerate(override)}
    return sorted(providers, key=lambda p: (rank.get(p.name, len(rank)), p.priority))


def build_providers(settings: Settings, http: HttpClient) -> Providers:
    crossref = CrossrefSource(http, mailto=settings.contact_email, plus_token=settings.crossref_plus_token, min_interval=settings.crossref_min_interval)
    openalex = OpenAlexSource(http, api_key=settings.openalex_api_key, min_credits=settings.openalex_min_credits,
                              lookback_days=settings.openalex_lookback_days, plan=settings.openalex_plan)
    europepmc = EuropePmcSource(http)
    discovery = {
        "pubmed": PubMedSource(http, api_key=settings.ncbi_api_key, email=settings.contact_email),
        "europepmc": europepmc,
        "crossref": crossref,
        "openalex": openalex,
        "ctgov": ClinicalTrialsSource(http),
    }
    fulltext = order_fulltext([
        EuropePmcFullText(http),
        SpringerOpenAccess(http, settings.springer_api_key),
        ElsevierFullText(http, settings.elsevier_api_key, settings.elsevier_inst_token),
        WileyTdmFullText(http, settings.wiley_tdm_token),
        OpenAccessPdf(http),
    ], settings.fulltext_order)
    entry, pub = Cap.DISCOVER_ENTRY_DATE, Cap.DISCOVER_PUBLICATION_DATE
    info = [
        ProviderInfo("pubmed", "discovery", Tier.FREE, True, frozenset({entry}),
                     "10 requests/s with NCBI_API_KEY, 3 without" if settings.ncbi_api_key else "no NCBI_API_KEY: 3 requests/s", "NCBI_API_KEY raises the rate"),
        ProviderInfo("europepmc", "discovery", Tier.FREE, True, frozenset({entry, Cap.LOOKUP_ABSTRACT}), "no key needed"),
        ProviderInfo("crossref", "discovery", Tier.PREMIUM if settings.crossref_plus_token else Tier.FREE, True,
                     frozenset({entry, Cap.LOOKUP_RETRACTION, Cap.LOOKUP_ABSTRACT}),
                     "Crossref Plus token configured" if settings.crossref_plus_token else "public pool, 1 request/s", "CROSSREF_PLUS_TOKEN raises the rate (Plus terms unverified)"),
        ProviderInfo("openalex", "discovery", Tier.PREMIUM if settings.openalex_plan == "premium" else Tier.FREE, True,
                     frozenset({entry if settings.openalex_plan == "premium" else pub, Cap.LOOKUP_RETRACTION, Cap.LOOKUP_ABSTRACT}),
                     ("premium plan: filters on created date" if settings.openalex_plan == "premium"
                      else "free plan: publication-date lookback, metered at about 10,000 credits a day"),
                     "OPENALEX_PLAN=premium enables exact created-date filtering (falls back by itself if the key is refused)"),
        ProviderInfo("ctgov", "discovery", Tier.FREE, True, frozenset({entry}), "no key needed"),
    ]
    llm_on = bool(settings.llm_model and settings.llm_api_key)
    info.append(ProviderInfo("llm_summaries", "summary", Tier.FREE, llm_on, frozenset({"summary.text"}),
                             f"{settings.llm_model} at {settings.llm_base_url} (one request every {settings.llm_request_interval}s)" if llm_on
                             else "needs LLM_MODEL and LLM_API_KEY (Groq or any OpenAI-compatible endpoint); abstract extracts are used until then",
                             "a paid plan allows LLM_REQUEST_INTERVAL_SECONDS below 2.1"))
    return Providers(discovery=discovery, enrichers={"crossref": crossref, "openalex": openalex, "europepmc": europepmc}, fulltext_all=fulltext, info=info)
