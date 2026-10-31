"""SQLAlchemy schema. Portable across SQLite (tests) and Postgres (production / Supabase).

Layers: configuration (fields, profiles, journals, researchers, subjects) -> search_specs -> runs/hits -> papers -> profile_matches.
`papers` is one shared store; `profile_matches` says which profile a paper matters to and why, so a second profile or field
reuses everything already collected.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import false as sa_false
from sqlalchemy import JSON, BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, validates

from ...domain.names import author_names, entity_tags

JsonType = JSON().with_variant(JSONB(), "postgresql")
PkType = BigInteger().with_variant(Integer(), "sqlite")  # SQLite only autoincrements INTEGER primary keys


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


def pk() -> Mapped[int]:
    return mapped_column(PkType, primary_key=True, autoincrement=True)


def fk(target: str, **kw) -> Mapped[int]:
    return mapped_column(PkType, ForeignKey(target, ondelete=kw.pop("ondelete", "CASCADE")), **kw)


# ---------------------------------------------------------------- configuration

class Field(Base):
    __tablename__ = "fields"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    config: Mapped[dict] = mapped_column(JsonType)
    config_hash: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Profile(Base):
    __tablename__ = "profiles"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    field_id: Mapped[str] = mapped_column(ForeignKey("fields.id"))
    name: Mapped[str] = mapped_column(String(200))
    config: Mapped[dict] = mapped_column(JsonType)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Journal(Base):
    __tablename__ = "journals"
    __table_args__ = (UniqueConstraint("field_id", "key"),)
    id: Mapped[int] = pk()
    field_id: Mapped[str] = mapped_column(ForeignKey("fields.id"))
    key: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(300))
    track: Mapped[str] = mapped_column(String(2))
    tier: Mapped[str] = mapped_column(String(20))
    issns: Mapped[list] = mapped_column(JsonType)
    aliases: Mapped[list] = mapped_column(JsonType, default=list)


class Researcher(Base):
    __tablename__ = "researchers"
    __table_args__ = (UniqueConstraint("field_id", "key"),)
    id: Mapped[int] = pk()
    field_id: Mapped[str] = mapped_column(ForeignKey("fields.id"))
    key: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(300))
    track: Mapped[str] = mapped_column(String(2))
    pubmed_names: Mapped[list] = mapped_column(JsonType, default=list)
    pubmed_name_ambiguous: Mapped[bool] = mapped_column(Boolean, default=False)
    openalex_ids: Mapped[list] = mapped_column(JsonType, default=list)
    orcid: Mapped[str | None] = mapped_column(String(25))
    aliases: Mapped[list] = mapped_column(JsonType, default=list)


class Subject(Base):
    __tablename__ = "subjects"
    __table_args__ = (UniqueConstraint("field_id", "code"),)
    id: Mapped[int] = pk()
    field_id: Mapped[str] = mapped_column(ForeignKey("fields.id"))
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(200))
    track: Mapped[str] = mapped_column(String(2))
    terms: Mapped[list] = mapped_column(JsonType)
    generic_terms: Mapped[list] = mapped_column(JsonType, default=list)


class BridgeTerm(Base):
    __tablename__ = "bridge_terms"
    __table_args__ = (UniqueConstraint("field_id", "term"),)
    id: Mapped[int] = pk()
    field_id: Mapped[str] = mapped_column(ForeignKey("fields.id"))
    term: Mapped[str] = mapped_column(String(200))
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text)


class ProfileJournal(Base):
    __tablename__ = "profile_journals"
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True)
    journal_id: Mapped[int] = mapped_column(PkType, ForeignKey("journals.id", ondelete="CASCADE"), primary_key=True)


class ProfileResearcher(Base):
    __tablename__ = "profile_researchers"
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True)
    researcher_id: Mapped[int] = mapped_column(PkType, ForeignKey("researchers.id", ondelete="CASCADE"), primary_key=True)


class ProfileSubject(Base):
    __tablename__ = "profile_subjects"
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True)
    subject_id: Mapped[int] = mapped_column(PkType, ForeignKey("subjects.id", ondelete="CASCADE"), primary_key=True)


# ---------------------------------------------------------------- searches and runs

class SearchSpecRow(Base):
    __tablename__ = "search_specs"
    id: Mapped[int] = pk()
    spec_key: Mapped[str] = mapped_column(String(40), unique=True)
    path: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(20))
    label: Mapped[str] = mapped_column(String(300))
    params: Mapped[dict] = mapped_column(JsonType)
    meta: Mapped[dict] = mapped_column(JsonType, default=dict)
    gated: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProfileSearchSpec(Base):
    __tablename__ = "profile_search_specs"
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True)
    spec_id: Mapped[int] = mapped_column(PkType, ForeignKey("search_specs.id", ondelete="CASCADE"), primary_key=True)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = pk()
    kind: Mapped[str] = mapped_column(String(20), default="collect")
    profile_id: Mapped[str | None] = mapped_column(ForeignKey("profiles.id"))
    trigger: Mapped[str] = mapped_column(String(20), default="manual")
    window_start: Mapped[date] = mapped_column(Date)
    window_end: Mapped[date] = mapped_column(Date)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | ok | partial | failed
    stats: Mapped[dict] = mapped_column(JsonType, default=dict)


class SourceRunLog(Base):
    __tablename__ = "source_run_log"
    __table_args__ = (Index("ix_srl_spec_run", "spec_id", "run_id"),)
    id: Mapped[int] = pk()
    run_id: Mapped[int] = fk("runs.id")
    spec_id: Mapped[int | None] = fk("search_specs.id", nullable=True, ondelete="SET NULL")
    source: Mapped[str] = mapped_column(String(20))
    path: Mapped[str] = mapped_column(String(20))
    label: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20))  # ok | error | skipped | suspect_zero | truncated
    n_records: Mapped[int] = mapped_column(Integer, default=0)
    n_new_papers: Mapped[int] = mapped_column(Integer, default=0)
    total_reported: Mapped[int | None] = mapped_column(Integer)
    requests: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    logged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- papers

class Paper(Base):
    __tablename__ = "papers"
    __table_args__ = (Index("ix_papers_first_seen", "first_seen_at"), Index("ix_papers_entry", "first_entry_date"))
    id: Mapped[int] = pk()
    kind: Mapped[str] = mapped_column(String(10), default="article")  # article | preprint | trial
    content_type: Mapped[str] = mapped_column(String(24), default="journal_article", server_default="journal_article", index=True)
    doi: Mapped[str | None] = mapped_column(String(300), unique=True)
    pmid: Mapped[str | None] = mapped_column(String(20), unique=True)
    pmcid: Mapped[str | None] = mapped_column(String(20), unique=True)
    pmcid_found_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # when our database first learned the PMC ID (lag = this minus first_seen_at)
    nct_id: Mapped[str | None] = mapped_column(String(20), unique=True)
    title_fp: Mapped[str | None] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(Text, default="")
    abstract: Mapped[str] = mapped_column(Text, default="")
    abstract_source: Mapped[str] = mapped_column(String(20), default="source", server_default="source")  # source | jats | pdf_abstract | pdf_summary | pdf_structured | pdf_intro
    journal: Mapped[str | None] = mapped_column(String(400))
    issns: Mapped[list] = mapped_column(JsonType, default=list)
    languages: Mapped[list] = mapped_column(JsonType, default=list)
    pub_types: Mapped[list] = mapped_column(JsonType, default=list)
    authors: Mapped[list] = mapped_column(JsonType, default=list)
    author_names: Mapped[str] = mapped_column(Text, default="", server_default="")  # folded 'family given initials; ...' for author search; set whenever authors is
    published_date: Mapped[date | None] = mapped_column(Date)
    published_label: Mapped[str | None] = mapped_column(String(30))  # what to show readers: an ISO date, or "2026 Oct" / "2026" when the source gives no day
    first_entry_date: Mapped[date | None] = mapped_column(Date)
    url: Mapped[str | None] = mapped_column(Text)
    is_retracted: Mapped[bool] = mapped_column(Boolean, default=False)
    retraction_sources: Mapped[list] = mapped_column(JsonType, default=list)
    is_retraction_notice: Mapped[bool] = mapped_column(Boolean, default=False)
    retraction_target_doi: Mapped[str | None] = mapped_column(String(300))
    retraction_target_pmid: Mapped[str | None] = mapped_column(String(20))
    retraction_target_id: Mapped[int | None] = mapped_column(PkType, ForeignKey("papers.id", ondelete="SET NULL"))
    oa_status: Mapped[str | None] = mapped_column(String(20))
    licences: Mapped[list] = mapped_column(JsonType, default=list)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    hits: Mapped[list["PaperHit"]] = relationship(back_populates="paper", cascade="all, delete-orphan")
    links: Mapped[list["PaperLink"]] = relationship(back_populates="paper", cascade="all, delete-orphan")

    @validates("authors")
    def _index_authors(self, _key, value):
        self.author_names = author_names(value)
        return value


class PaperHit(Base):
    """Every time a search found a paper: which query, which source, and the source's own dates."""

    __tablename__ = "paper_hits"
    __table_args__ = (UniqueConstraint("paper_id", "spec_id", "run_id", "source_record_id"),)
    id: Mapped[int] = pk()
    paper_id: Mapped[int] = fk("papers.id")
    run_id: Mapped[int] = fk("runs.id")
    spec_id: Mapped[int | None] = fk("search_specs.id", nullable=True, ondelete="SET NULL")
    source: Mapped[str] = mapped_column(String(20))
    path: Mapped[str] = mapped_column(String(20))
    label: Mapped[str] = mapped_column(String(300))
    gated: Mapped[bool] = mapped_column(Boolean, default=True)
    confidence: Mapped[str] = mapped_column(String(20), default="query")
    source_record_id: Mapped[str] = mapped_column(String(120), default="")
    source_entry_date: Mapped[date | None] = mapped_column(Date)
    source_published_date: Mapped[date | None] = mapped_column(Date)
    found_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    paper: Mapped[Paper] = relationship(back_populates="hits")


class ProfileMatch(Base):
    __tablename__ = "profile_matches"
    __table_args__ = (UniqueConstraint("profile_id", "paper_id"), Index("ix_pm_profile_status", "profile_id", "gate_status"))
    id: Mapped[int] = pk()
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    paper_id: Mapped[int] = fk("papers.id")
    gate_status: Mapped[str] = mapped_column(String(10))  # passed | failed | unknown
    gate_reason: Mapped[str] = mapped_column(String(200), default="")
    language_status: Mapped[str] = mapped_column(String(10), default="unknown")  # ok | unknown | other
    track: Mapped[str] = mapped_column(String(2), default="A")
    exploratory: Mapped[bool] = mapped_column(Boolean, default=False)
    watched: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa_false())  # from a watched journal or a confirmed watched researcher
    subjects: Mapped[list] = mapped_column(JsonType, default=list)
    entities: Mapped[list] = mapped_column(JsonType, default=list)
    entity_tags: Mapped[str] = mapped_column(Text, default="", server_default="")  # '|journal:key|researcher:key|' for SQL filtering; set whenever entities is
    first_matched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    @validates("entities")
    def _tag_entities(self, _key, value):
        self.entity_tags = entity_tags(value)
        return value


class PaperLink(Base):
    __tablename__ = "paper_links"
    __table_args__ = (UniqueConstraint("paper_id", "url"),)
    id: Mapped[int] = pk()
    paper_id: Mapped[int] = fk("papers.id")
    kind: Mapped[str] = mapped_column(String(20))
    url: Mapped[str] = mapped_column(String(1000))
    source: Mapped[str] = mapped_column(String(20))
    is_free: Mapped[bool | None] = mapped_column(Boolean)
    licence: Mapped[str | None] = mapped_column(String(300))
    paper: Mapped[Paper] = relationship(back_populates="links")


class PaperFulltext(Base):
    """The best full text found for a paper, with where it came from and what may be done with it."""

    __tablename__ = "paper_fulltext"
    paper_id: Mapped[int] = mapped_column(PkType, ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(30), default="unknown")  # unknown | full_text | abstract_and_links_only
    format: Mapped[str | None] = mapped_column(String(20))  # jats_xml | plain_text | pdf_text
    provider: Mapped[str | None] = mapped_column(String(40))
    tier: Mapped[str | None] = mapped_column(String(10))  # free | premium: which kind of access produced the text
    rights: Mapped[str | None] = mapped_column(String(10))  # open | entitled | unknown
    licence: Mapped[str | None] = mapped_column(String(300))  # licence URL as found
    licence_key: Mapped[str | None] = mapped_column(String(20))  # normalised: cc-by, cc-by-nc, ...
    print_allowed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa_false())  # may the print edition reproduce this text under the profile's policy
    words: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    source_url: Mapped[str | None] = mapped_column(String(1000))
    text: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))    # last time any provider was asked
    refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # last time identifiers / free copies were looked up again


class FulltextAttempt(Base):
    """Every try, successful or not, so a provider is not re-asked needlessly and a newly added provider can be tried on old papers."""

    __tablename__ = "fulltext_attempts"
    __table_args__ = (Index("ix_fta_paper_provider", "paper_id", "provider"),)
    id: Mapped[int] = pk()
    paper_id: Mapped[int] = fk("papers.id")
    provider: Mapped[str] = mapped_column(String(40))
    outcome: Mapped[str] = mapped_column(String(20))  # ok | not_available | not_permitted | error
    detail: Mapped[str | None] = mapped_column(Text)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RetractionCheck(Base):
    __tablename__ = "retraction_checks"
    id: Mapped[int] = pk()
    paper_id: Mapped[int] = fk("papers.id")
    source: Mapped[str] = mapped_column(String(20))
    result: Mapped[str] = mapped_column(String(10))  # retracted | clean | error
    detail: Mapped[dict] = mapped_column(JsonType, default=dict)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- newsletter

class PaperSummary(Base):
    """A generated summary, cached so rebuilding an edition never calls the model again. Keyed by what produced it: a changed abstract, model or prompt gets a new row."""

    __tablename__ = "paper_summaries"
    __table_args__ = (UniqueConstraint("paper_id", "model", "prompt_version", "abstract_hash"),)
    id: Mapped[int] = pk()
    paper_id: Mapped[int] = fk("papers.id")
    model: Mapped[str] = mapped_column(String(120))
    prompt_version: Mapped[str] = mapped_column(String(20))
    abstract_hash: Mapped[str] = mapped_column(String(40))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Issue(Base):
    """One weekly edition for one profile. The key makes building idempotent; items are what was selected, frozen at build time."""

    __tablename__ = "issues"
    __table_args__ = (UniqueConstraint("profile_id", "issue_key"),)
    id: Mapped[int] = pk()
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    issue_key: Mapped[str] = mapped_column(String(80))
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # papers first stored after this ...
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))    # ... up to and including this
    status: Mapped[str] = mapped_column(String(12), default="built")  # built | published (draft created) | sent (marked by a person)
    subject: Mapped[str] = mapped_column(String(300), default="")
    html: Mapped[str] = mapped_column(Text, default="")
    text: Mapped[str] = mapped_column(Text, default="")
    stats: Mapped[dict] = mapped_column(JsonType, default=dict)
    draft_ref: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    items: Mapped[list["IssueItem"]] = relationship(back_populates="issue", cascade="all, delete-orphan", order_by="IssueItem.position")


class IssueItem(Base):
    """A paper in an edition. `profile_id` is repeated here so the database itself refuses to send a paper twice in the same role."""

    __tablename__ = "issue_items"
    __table_args__ = (UniqueConstraint("profile_id", "paper_id", "role"),)
    id: Mapped[int] = pk()
    issue_id: Mapped[int] = fk("issues.id")
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    paper_id: Mapped[int] = fk("papers.id")
    role: Mapped[str] = mapped_column(String(12))  # digest | retraction (a retraction alert about an already-sent paper is allowed once)
    section: Mapped[str] = mapped_column(String(40))
    position: Mapped[int] = mapped_column(Integer, default=0)
    snapshot: Mapped[dict] = mapped_column(JsonType, default=dict)  # full-text status, track, summary and flags as they were when sent
    issue: Mapped[Issue] = relationship(back_populates="items")


class NewsletterSelection(Base):
    """A topic, journal, researcher or discovery source switched on or off for the newsletter (see domain/selection.py). No row means on.
    Only the newsletter build reads this; collection and search ignore it."""

    __tablename__ = "newsletter_selections"
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(12), primary_key=True)   # subjects | journals | researchers | sources
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
