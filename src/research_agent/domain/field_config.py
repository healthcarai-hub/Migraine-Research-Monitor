"""Typed field and profile configuration, loaded from YAML. This is where everything field-specific lives."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from .content_type import ALL_TYPES, DEFAULT_DIGEST_EXCLUDED
from .newsletter import DEFAULT_WEIGHTS, SECTION_KEYS

Track = Literal["A", "B", "AB"]


class GateTerm(BaseModel):
    text: str
    match: Literal["prefix", "exact"] = "exact"
    plain: str | None = None  # word handed to stemming engines (OpenAlex, Crossref, ClinicalTrials.gov)

    @property
    def search_word(self) -> str:
        return self.plain or self.text


class Gate(BaseModel):
    terms: list[GateTerm] = Field(min_length=1)


class BridgeTerms(BaseModel):
    approved: bool = False  # nothing is admitted through the bridge until the client approves the list
    terms: list[str] = Field(default_factory=list)


class Subject(BaseModel):
    code: str
    name: str
    track: Track
    terms: list[str] = Field(min_length=1)
    generic_terms: list[str] = Field(default_factory=list)  # matches that say nothing about the track (the gate word itself)


class FieldConfig(BaseModel):
    id: str
    name: str
    language: str = "en"
    gate: Gate
    tracks: dict[str, str] = Field(default_factory=dict)
    bridge: BridgeTerms = BridgeTerms()
    subjects: list[Subject] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_subject_codes(self) -> "FieldConfig":
        codes = [s.code for s in self.subjects]
        if len(codes) != len(set(codes)):
            raise ValueError("duplicate subject code in field %s" % self.id)
        return self


class JournalEntry(BaseModel):
    key: str
    name: str
    track: Track
    tier: Literal["core", "targeted"] = "targeted"
    issns: list[str] = Field(min_length=1)  # every ISSN: Crossref indexes print and online separately
    aliases: list[str] = Field(default_factory=list)


class ResearcherEntry(BaseModel):
    key: str
    name: str
    track: Track
    pubmed_names: list[str] = Field(default_factory=list)  # "Surname Initials" forms
    pubmed_name_ambiguous: bool = False  # name search can hit other people; hits are labelled lower confidence
    openalex_ids: list[str] = Field(default_factory=list)
    orcid: str | None = None
    aliases: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _has_a_handle(self) -> "ResearcherEntry":
        self.pubmed_names = list(dict.fromkeys(self.pubmed_names))
        self.openalex_ids = list(dict.fromkeys(self.openalex_ids))
        if not (self.pubmed_names or self.openalex_ids):
            raise ValueError("researcher %s has no PubMed name or OpenAlex ID" % self.key)
        return self


class DigestConfig(BaseModel):
    """What reaches digests and the app by default. Excluded items stay in the database."""
    exclude_content_types: list[str] = Field(default_factory=lambda: list(DEFAULT_DIGEST_EXCLUDED))

    @field_validator("exclude_content_types")
    @classmethod
    def _known(cls, v):
        bad = set(v) - set(ALL_TYPES)
        if bad:
            raise ValueError("unknown content types %s" % sorted(bad))
        return v


class FullTextConfig(BaseModel):
    """Full-text rules for one profile."""
    print_text_regardless_of_licence: bool = True   # owner decision 2026-10-31 for Curt: licences do not limit printing. false = use the two settings below
    print_licences: list[str] = Field(default_factory=lambda: ["cc-by", "cc0"])   # only when the switch above is false
    allow_entitled_in_print: bool = False                                          # only when the switch above is false
    store_entitled_text: bool = True
    retry_days: int = 7  # ask a provider again about a paper it could not supply after this many days
    recheck_days: int = 90  # keep re-checking for a PMC ID / free copy this long after a paper first entered the index (PMC lags and embargoes lift)


class CapsConfig(BaseModel):
    """How much one edition may carry. Items held back are not marked as sent: they may come back once, in the next edition, ranked lower (`newsletter.carryover_penalty`)."""
    per_section: dict[str, int | None] = Field(default_factory=lambda: {"direct": 30, "mechanisms": 15, "preprints": 10, "trials": 10})  # missing or null = no cap
    total: int | None = None            # optional cap across all sections except retractions
    priority_to_watched: bool = True    # papers from a watched journal or researcher are chosen first when a cap bites (owner decision 2026-10-31)
    max_html_kb: int | None = 95        # safety net: trim the lowest-ranked items until the HTML is under this size; Gmail clips near 100 KB. null = off

    @field_validator("per_section")
    @classmethod
    def _known_sections(cls, v):
        bad = set(v) - set(SECTION_KEYS)
        if bad:
            raise ValueError("unknown newsletter sections %s (use %s)" % (sorted(bad), list(SECTION_KEYS)))
        if any(x is not None and x < 1 for x in v.values()):
            raise ValueError("a cap must be 1 or more; use null for no cap")
        return v

    @field_validator("total", "max_html_kb")
    @classmethod
    def _positive(cls, v):
        if v is not None and v < 1:
            raise ValueError("must be 1 or more; use null to switch it off")
        return v


class FreshnessConfig(BaseModel):
    """Leave out papers that were published long before we found them (late-indexed or picked up by a lookback). They stay in the database and the app."""
    max_publication_age_days: int | None = 7   # days from publication to the day we first stored the paper; null = no limit

    @field_validator("max_publication_age_days")
    @classmethod
    def _non_negative(cls, v):
        if v is not None and v < 0:
            raise ValueError("must be 0 or more; use null for no limit")
        return v


class RankingConfig(BaseModel):
    """Points that decide which papers fill a capped section. Raise a weight to favour that signal; 0 switches it off."""
    weights: dict[str, int] = Field(default_factory=lambda: dict(DEFAULT_WEIGHTS))

    @field_validator("weights")
    @classmethod
    def _known(cls, v):
        bad = set(v) - set(DEFAULT_WEIGHTS)
        if bad:
            raise ValueError("unknown ranking signals %s (use %s)" % (sorted(bad), list(DEFAULT_WEIGHTS)))
        if any(x < 0 for x in v.values()):
            raise ValueError("a weight must be 0 or more")
        return {**DEFAULT_WEIGHTS, **v}


class SignatureConfig(BaseModel):
    """Who the edition is from, shown under the closing line. Everything except the name is optional."""
    name: str
    title: str | None = None
    email: str | None = None
    phone: str | None = None
    disclaimer: str | None = None


class NewsletterConfig(BaseModel):
    """Digital edition settings. Print-edition settings are deliberately absent: the print template has not been supplied yet."""
    title: str = "Research digest"
    subtitle: str = ""
    recipients: list[str] = Field(default_factory=list)
    accent_color: str = "#1f5f8b"     # header band, links and section rules in the HTML edition; a #rrggbb colour
    greeting: str = "Hello,"
    introduction: str = "Here is this week's round-up of new research, grouped by topic. Papers whose full text we hold are marked."
    results_lead: str = "This week's papers:"
    closing: str = "That is everything for this week. Have a good weekend."
    signature: SignatureConfig | None = None
    footer: str = ""                  # a line at the very end of the edition, e.g. who prepared it
    lookback_days: int = 7            # normal window length: papers first stored in the last 7 days
    max_catchup_days: int = 14        # how far back a later edition looks: this week plus one more, so a paper held back once may return in the next edition and then drops out
    carryover_penalty: int = 3        # points taken off the ranking score of a paper first stored before the normal window (a carried-over paper), so only old papers with strong signals get in
    freshness: FreshnessConfig = FreshnessConfig()
    ranking: RankingConfig = RankingConfig()
    caps: CapsConfig = CapsConfig()
    recheck_retraction_weeks: int = 8  # re-check papers sent in the last N weeks for a retraction before building
    summary_sentences: int = 2
    summary_words: int = 60          # target length of a model-written summary
    use_llm: bool = True             # still needs LLM_MODEL and LLM_API_KEY in the environment; otherwise the abstract extract is used
    summary_max_chars: int = 420
    font_px: int = 17                 # base size of the HTML edition
    section_titles: dict[str, str] = Field(default_factory=lambda: {
        "retractions": "Retractions and withdrawals", "direct": "Direct research", "mechanisms": "Related mechanisms (exploratory)",
        "preprints": "Preprints (not peer reviewed)", "trials": "Clinical trials registered"})

    @model_validator(mode="after")
    def _window_and_penalty(self) -> "NewsletterConfig":
        if self.max_catchup_days < self.lookback_days:
            raise ValueError("max_catchup_days must be at least lookback_days")
        if self.carryover_penalty < 0:
            raise ValueError("carryover_penalty must be 0 or more")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", self.accent_color):
            raise ValueError("accent_color must look like #1f5f8b")
        return self


class ProfileConfig(BaseModel):
    id: str
    name: str
    field: str
    language: str = "en"
    subjects: list[str] | Literal["all"] = "all"
    digest: DigestConfig = DigestConfig()
    fulltext: FullTextConfig = FullTextConfig()
    newsletter: NewsletterConfig = NewsletterConfig()
    journals: list[JournalEntry] = Field(default_factory=list)
    researchers: list[ResearcherEntry] = Field(default_factory=list)

    @field_validator("journals", "researchers")
    @classmethod
    def _unique_keys(cls, v):
        keys = [x.key for x in v]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate key in %s" % [k for k in keys if keys.count(k) > 1])
        return v


def _load(path: Path) -> dict:
    with open(path, encoding="utf8") as fh:
        return yaml.safe_load(fh)


def load_field(config_dir: Path, field_id: str) -> FieldConfig:
    return FieldConfig.model_validate(_load(config_dir / "fields" / f"{field_id}.yaml"))


def load_profile(config_dir: Path, profile_id: str) -> tuple[ProfileConfig, FieldConfig]:
    profile = ProfileConfig.model_validate(_load(config_dir / "profiles" / f"{profile_id}.yaml"))
    field = load_field(config_dir, profile.field)
    if profile.subjects != "all":
        unknown = set(profile.subjects) - {s.code for s in field.subjects}
        if unknown:
            raise ValueError("profile %s selects unknown subjects %s" % (profile.id, sorted(unknown)))
    return profile, field


def selected_subjects(profile: ProfileConfig, field: FieldConfig) -> list[Subject]:
    if profile.subjects == "all":
        return list(field.subjects)
    chosen = set(profile.subjects)
    return [s for s in field.subjects if s.code in chosen]
