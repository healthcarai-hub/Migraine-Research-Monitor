"""Name folding and the small derived search strings kept beside authors and watched entities. Pure functions, no I/O."""
from __future__ import annotations

import re
import unicodedata


def fold(text: str | None) -> str:
    """Lower-case and drop accents, so 'Müller' is found by 'muller' and 'MULLER'."""
    s = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in s if not unicodedata.combining(c)).casefold()


def words(query: str | None) -> list[str]:
    """The folded words of a search box: 'Goadsby, P.J.' -> ['goadsby', 'p', 'j']."""
    return [w for w in re.split(r"\W+", fold(query)) if w]


def author_names(authors: list[dict] | None) -> str:
    """One searchable string for a paper's authors: 'goadsby peter pj; hay deborah d'. Kept in `papers.author_names`."""
    out = []
    for a in authors or []:
        name = " ".join(x for x in (a.get("family"), a.get("given"), a.get("initials")) if x)
        if name.strip():
            out.append(fold(name))
    return "; ".join(out)


def entity_tags(entities: list[dict] | None) -> str:
    """'|journal:cephalalgia|researcher:goadsby|' for the watched journals and the researchers whose identity is not ambiguous.
    Kept in `profile_matches.entity_tags` so a search can filter by them in SQL."""
    tags = sorted({f"{e['type']}:{e['key']}" for e in entities or [] if e.get("type") in ("journal", "researcher") and e.get("confidence") != "name_ambiguous"})
    return "|" + "|".join(tags) + "|" if tags else ""
