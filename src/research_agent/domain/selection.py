"""What the newsletter is built from, as chosen in the application: which search topics, journals, researchers and discovery sources are switched on.

This is a layer on top of the profile file. It changes which stored papers the newsletter picks; it never changes what the daily job collects, what the
database keeps, or what the search shows. Everything is on until someone switches it off.

A paper qualifies for the newsletter when
  * it has at least one *route in* that is still on: one of its matched topics, a watched journal it came from, or a watched researcher it came from
    (a paper with no route at all, such as one that only passed the gate, is not touched by these switches), and
  * at least one of the sources that found it is on (when any source is off).
Retraction alerts are not filtered: a retraction of something already sent is always announced.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

KINDS = ("subjects", "journals", "researchers", "sources")
SOURCES = {"pubmed": "PubMed", "europepmc": "Europe PMC", "crossref": "Crossref", "openalex": "OpenAlex", "ctgov": "ClinicalTrials.gov"}


@dataclass(frozen=True)
class Selection:
    off: Mapping[str, frozenset[str]] = field(default_factory=dict)   # kind -> keys switched off

    def _off(self, kind: str) -> frozenset[str]:
        return self.off.get(kind, frozenset())

    @property
    def is_default(self) -> bool:
        return not any(self.off.values())

    def is_on(self, kind: str, key: str) -> bool:
        return key not in self._off(kind)

    def routes(self, subjects: list[dict] | None, entities: list[dict] | None) -> list[tuple[str, str]]:
        out = [("subjects", s["code"]) for s in subjects or []]
        out += [("journals" if e["type"] == "journal" else "researchers", e["key"]) for e in entities or []
                if e.get("type") in ("journal", "researcher") and e.get("confidence") != "name_ambiguous"]
        return out

    def route_open(self, subjects: list[dict] | None, entities: list[dict] | None) -> bool:
        routes = self.routes(subjects, entities)
        return not routes or any(self.is_on(k, key) for k, key in routes)

    def source_open(self, sources: Iterable[str]) -> bool:
        off, found = self._off("sources"), set(sources)
        return not off or not found or bool(found - off)

    def allows(self, subjects: list[dict] | None, entities: list[dict] | None, sources: Iterable[str]) -> bool:
        return self.route_open(subjects, entities) and self.source_open(sources)

    def active_sources(self) -> list[str]:
        return [k for k in SOURCES if self.is_on("sources", k)]

    def summary(self) -> dict[str, list[str]]:
        """Only what is switched off, for the edition's record."""
        return {k: sorted(v) for k, v in self.off.items() if v}
