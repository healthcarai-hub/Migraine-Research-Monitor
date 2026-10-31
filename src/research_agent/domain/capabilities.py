"""What a data provider can do and what it takes to switch it on.

Every external service (discovery index, lookup service, full-text source) is a *provider*. A provider has a tier (free or premium), declares the
capabilities it offers at that tier, and is active only when its credentials are present. Routing code asks for a capability and gets the best
active provider; adding a premium key changes the routing without touching the pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Tier(str, Enum):
    FREE = "free"
    PREMIUM = "premium"


class Rights(str, Enum):
    """What the agent may do with text obtained from a provider. Paid *access* is not a licence to republish."""

    OPEN = "open"          # an open licence travels with the text (for example CC BY)
    ENTITLED = "entitled"  # readable through a paid entitlement; reuse limited by the publisher's terms
    UNKNOWN = "unknown"


class Cap:
    """Capability names. Plain strings so a new provider can add its own without editing this file."""

    DISCOVER_ENTRY_DATE = "discover.entry_date"              # filter by the date a record first entered the index
    DISCOVER_PUBLICATION_DATE = "discover.publication_date"  # filter by publication date only (late-indexed records can be missed)
    LOOKUP_RETRACTION = "lookup.retraction"
    LOOKUP_ABSTRACT = "lookup.abstract"
    FULLTEXT_JATS = "fulltext.jats"
    FULLTEXT_TEXT = "fulltext.text"
    FULLTEXT_PDF = "fulltext.pdf"


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    kind: str  # discovery | lookup | fulltext
    tier: Tier
    active: bool
    capabilities: frozenset[str] = field(default_factory=frozenset)
    detail: str = ""  # why it is inactive, or what it needs to be upgraded
    upgrade: str = ""  # what changes if a premium credential is added
