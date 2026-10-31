"""Reading and changing what the newsletter is built from (search topics, journals, researchers, discovery sources).

This is the only thing the application can write. It stores switches in `newsletter_selections`; the newsletter build reads them (see domain/selection.py
for exactly how). It never touches the profile file, the collection plan, or any paper.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy.orm import Session, sessionmaker

from ..domain.field_config import FieldConfig, ProfileConfig, selected_subjects
from ..domain.selection import KINDS, SOURCES, Selection
from ..infrastructure.db.repository import Repository
from ..infrastructure.db.session import session_scope

APPLIES_TO = "newsletter editions built from now on. The daily collection, the database and the search are not affected."


class SelectionService:
    def __init__(self, factory: sessionmaker[Session], profile: ProfileConfig, field: FieldConfig, *, now=lambda: datetime.now(timezone.utc)):
        self.factory, self.profile, self.field, self.now = factory, profile, field, now

    def catalogue(self) -> dict[str, dict[str, dict[str, Any]]]:
        """Everything that can be switched, by kind, in display order."""
        return {
            "subjects": {s.code: {"name": s.name, "track": s.track} for s in selected_subjects(self.profile, self.field)},
            "journals": {j.key: {"name": j.name, "track": j.track, "tier": j.tier} for j in self.profile.journals},
            "researchers": {r.key: {"name": r.name, "track": r.track} for r in self.profile.researchers},
            "sources": {k: {"name": v} for k, v in SOURCES.items()},
        }

    # ------------------------------------------------------------------ read
    def view(self) -> dict[str, Any]:
        with session_scope(self.factory) as s:
            repo = Repository(s)
            sel, rows = repo.get_selection(self.profile.id), repo.selection_rows(self.profile.id)
            changed = max((r.updated_at for r in rows), default=None)
        return self._render(sel, changed)

    def selection(self) -> Selection:
        with session_scope(self.factory) as s:
            return Repository(s).get_selection(self.profile.id)

    def _render(self, sel: Selection, changed: datetime | None) -> dict[str, Any]:
        if changed is not None and changed.tzinfo is None:
            changed = changed.replace(tzinfo=timezone.utc)
        out: dict[str, Any] = {"profile": self.profile.id, "applies_to": APPLIES_TO, "changed_at": changed.isoformat() if changed else None}
        for kind, items in self.catalogue().items():
            out[kind] = [{"key": key, **info, "enabled": sel.is_on(kind, key)} for key, info in items.items()]
        out["switched_off"] = sel.summary()
        return out

    # ------------------------------------------------------------------ write
    def apply(self, changes: Mapping[str, Mapping[str, bool]]) -> dict[str, Any]:
        """Switch things on or off. All or nothing: an unknown name, or a result that would leave the newsletter with nothing to select from, changes nothing."""
        catalogue = self.catalogue()
        for kind, items in changes.items():
            if kind not in KINDS:
                raise ValueError(f"unknown kind '{kind}' (use {', '.join(KINDS)})")
            for key, value in items.items():
                if key not in catalogue[kind]:
                    raise ValueError(f"unknown {kind.rstrip('s')} '{key}' (see GET /v1/newsletter/config for the valid keys)")
                if not isinstance(value, bool):
                    raise ValueError(f"{kind}.{key} must be true or false")
        with session_scope(self.factory) as s:
            repo = Repository(s)
            current = repo.get_selection(self.profile.id)
            off = {k: set(current.off.get(k, ())) for k in KINDS}
            for kind, items in changes.items():
                for key, value in items.items():
                    (off[kind].discard if value else off[kind].add)(key)
            self._check(catalogue, off)
            now = self.now()
            for kind, items in changes.items():
                for key, value in items.items():
                    repo.set_selection(self.profile.id, kind, key, value, now)
            s.flush()
            sel, rows = repo.get_selection(self.profile.id), repo.selection_rows(self.profile.id)
            changed = max((r.updated_at for r in rows), default=None)
        return self._render(sel, changed)

    def reset(self) -> dict[str, Any]:
        """Back to the profile file: everything on."""
        with session_scope(self.factory) as s:
            Repository(s).clear_selection(self.profile.id)
        return self._render(Selection(), None)

    @staticmethod
    def _check(catalogue: dict, off: dict[str, set[str]]) -> None:
        if not set(catalogue["sources"]) - off["sources"]:
            raise ValueError("at least one source must stay on, otherwise the newsletter has nothing to select from")
        routes = sum(len(set(catalogue[k]) - off[k]) for k in ("subjects", "journals", "researchers"))
        if routes == 0:
            raise ValueError("at least one topic, journal or researcher must stay on, otherwise the newsletter has nothing to select from")
