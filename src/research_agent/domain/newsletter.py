"""Newsletter view models and the small text rules behind them (summaries, author lines, dates, ranking). Pure functions, no I/O."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

SECTION_KEYS = ("retractions", "direct", "mechanisms", "preprints", "trials")
_ABBREV = ("e.g.", "i.e.", "vs.", "et al.", "Fig.", "fig.", "approx.", "no.", "No.", "Dr.", "St.", "ca.", "cf.", "Ltd.", "Inc.")
_LABEL = re.compile(r"^\s*([A-Z][A-Za-z /&-]{2,40}?):\s+(.*)$")
_CONCLUDING = {"conclusion", "conclusions", "interpretation", "findings", "discussion", "implications", "significance", "summary", "main conclusion"}


def split_sentences(text: str) -> list[str]:
    guarded = text
    for i, a in enumerate(_ABBREV):
        guarded = guarded.replace(a, a.replace(".", f"\u0000{i}\u0000"))
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(\[\"'])", guarded.strip())
    out = []
    for part in parts:
        for i, a in enumerate(_ABBREV):
            part = part.replace(a.replace(".", f"\u0000{i}\u0000"), a)
        if part.strip():
            out.append(part.strip())
    return out


_HEADERS = ("Background", "Introduction", "Objectives?", "Aims?", "Purpose", "Context", "Importance", "Rationale", "Methods?", "Design", "Setting",
            "Participants", "Patients", "Results?", "Findings", "Conclusions?", "Interpretation", "Discussion", "Implications", "Significance")
# a section word at the start of the text or of a sentence, optionally 'Objective/Background', followed by a capitalised sentence
_HEAD = re.compile(r"(?:^|(?<=[.!?] )|(?<=\n))(?:Abstract\s+)?((?:%s)(?:/(?:%s))?)\s*:?\s+(?=[A-Z(\[0-9])" % ("|".join(_HEADERS), "|".join(_HEADERS)))


def abstract_sections(abstract: str) -> list[tuple[str, str]]:
    """Split an abstract into (label, text). Handles 'LABEL: text' lines and the unpunctuated 'Background Text. Methods Text.' style."""
    out: list[tuple[str, str]] = []
    for line in abstract.splitlines():
        if not line.strip():
            continue
        m = _LABEL.match(line)
        if m and re.match(r"(%s)\b" % "|".join(_HEADERS), m.group(1).strip(), re.I):
            out.append((m.group(1).strip().casefold(), m.group(2).strip()))
            continue
        marks = list(_HEAD.finditer(line))
        if not marks:
            out.append(("", re.sub(r"^Abstract\s+", "", line.strip())))
            continue
        if marks[0].start() > 0 and line[:marks[0].start()].strip():
            out.append(("", re.sub(r"^Abstract\s+", "", line[:marks[0].start()].strip())))
        for i, mk in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(line)
            out.append((mk.group(1).split("/")[0].casefold(), line[mk.end():end].strip()))
    return out


def summarise(abstract: str, *, max_sentences: int = 2, max_chars: int = 420) -> str:
    """Extractive summary from the abstract: the concluding section if there is one, else the results, else the opening sentences. No model involved."""
    if not abstract or not abstract.strip():
        return ""
    parts = abstract_sections(abstract)
    pick = (next((t for l, t in parts if l in _CONCLUDING), None) or next((t for l, t in parts if l in ("results", "result") and len(t) > 40), None)
            or " ".join(t for _, t in parts))
    sentences = split_sentences(pick)
    text = " ".join(sentences[:max_sentences]) if sentences else pick
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0].rstrip(",;:") + "\u2026"
    return text


def authors_short(authors: list[dict], limit: int = 3) -> str:
    names = []
    for a in authors or []:
        family = (a.get("family") or "").strip()
        initials = (a.get("initials") or "").strip()
        if not family:
            continue
        names.append(f"{family} {initials}".strip() if initials else (f"{(a.get('given') or '')[:1]} {family}".strip()))
    if not names:
        return ""
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[:limit]) + ", et al."


def tidy_title(title: str) -> str:
    """Drop the full stop some indexes add to the end of a title ('...a cohort study.'); keep ellipses, abbreviations and initials ('U.S.', 'vs.')."""
    t = (title or "").strip()
    if len(t.split()) >= 4 and t.endswith(".") and not t.endswith("..") and not re.search(r"(?:\b[A-Za-z]|\bvs|\bet al|\bcf|\bno|\bFig)\.$", t):
        return t[:-1].rstrip()
    return t


def format_pub(label: str | None) -> str:
    """'2026-10-21' -> '21 Oct 2026'; '2026 Oct' and '2026' are shown as they are; empty -> ''."""
    if not label:
        return ""
    try:
        d = date.fromisoformat(label)
        return f"{d.day} {d.strftime('%b %Y')}"
    except ValueError:
        return label


DEFAULT_WEIGHTS = {"watched_journal": 3, "watched_researcher": 3, "printable_full_text": 3, "title_match": 2, "subject_breadth": 1, "has_abstract": 1, "core_journal": 1}
SUBJECT_BREADTH_CAP = 3


def score(*, watched_journal: bool, watched_researcher: bool, specific_subjects: int, has_abstract: bool, printable_full_text: bool, core_journal: bool,
          title_match: bool = False, weights: dict[str, int] | None = None) -> int:
    """Deterministic ordering inside a section. Weights are a profile setting (`newsletter.ranking`); the defaults put watched sources and papers whose
    full text we hold first, then papers with the field's word in the title, then breadth of topic match and readability."""
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    return (w["watched_journal"] * watched_journal + w["watched_researcher"] * watched_researcher + w["printable_full_text"] * printable_full_text
            + w["title_match"] * title_match + w["subject_breadth"] * min(specific_subjects, SUBJECT_BREADTH_CAP) + w["has_abstract"] * has_abstract
            + w["core_journal"] * core_journal)


@dataclass
class ItemView:
    paper_id: int
    title: str
    journal: str
    published: str                      # display text, see format_pub
    authors: str
    url: str                            # primary link: DOI if there is one
    doi: str | None
    summary: str
    section: str
    group: str                          # subject heading inside the section
    kind: str
    track: str
    score: int
    badges: list[str] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)   # (label, url) beyond the primary link
    watched: list[str] = field(default_factory=list)
    retracted: bool = False
    notice_url: str | None = None
    previously_sent_in: str | None = None
    date_label: str = "Published"       # trials are "Registered"
    summary_source: str = "extractive"  # llm | extractive
    fulltext_status: str = "unknown"    # full_text | abstract_and_links_only | unknown (kept for the print edition later)
    first_seen: str = ""


@dataclass
class SectionView:
    key: str
    title: str
    groups: list[tuple[str, list[ItemView]]]

    @property
    def count(self) -> int:
        return sum(len(items) for _, items in self.groups)


def apply_caps(items: list[ItemView], per_section: dict[str, int | None], total: int | None, watched_first: bool = True) -> tuple[list[ItemView], list[tuple[ItemView, str]]]:
    """Keep the best-ranked items inside each section's cap, then inside the overall cap. Returns (kept, [(held back item, reason)]).
    With `watched_first`, papers from a watched journal or researcher are chosen before any other paper whatever the scores, so the full-text and title
    signals cannot push a watched source out. Retractions are only capped if a cap is set for them; the overall cap never removes a retraction."""
    rank = lambda i: (0 if (watched_first and i.watched) else 1, -i.score, -i.paper_id)    # best first; among equals the most recently stored first
    kept: list[ItemView] = []
    held: list[tuple[ItemView, str]] = []
    for key in SECTION_KEYS:
        mine = sorted((i for i in items if i.section == key), key=rank)
        limit = per_section.get(key)
        kept += mine[:limit] if limit else mine
        held += [(i, f"section cap ({key})") for i in (mine[limit:] if limit else [])]
    if total:
        capped = sorted((i for i in kept if i.section != "retractions"), key=rank)
        over = capped[max(total - sum(1 for i in kept if i.section == "retractions"), 0):]
        if over:
            drop = {i.paper_id for i in over}
            kept = [i for i in kept if i.paper_id not in drop]
            held += [(i, "overall cap") for i in over]
    return kept, held


_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def latest_possible_publication(published_date: date | None, label: str | None) -> date | None:
    """The latest day a paper could have been published, given what the source told us. An exact date is itself; '2026 Oct' or '2026-10' means the
    end of that month; '2026' means unknown (None). Used so a paper is dropped as stale only when it is *certainly* old."""
    if published_date:
        return published_date
    m = re.match(r"^\s*(\d{4})(?:[ -](\d{1,2}|[A-Za-z]{3})[A-Za-z]*)?", label or "")
    if not m or not m.group(2):
        return None
    mon = int(m.group(2)) if m.group(2).isdigit() else _MONTHS.get(m.group(2)[:3].lower())
    if not mon or not 1 <= mon <= 12:
        return None
    year = int(m.group(1))
    nxt = date(year + (mon == 12), mon % 12 + 1, 1)
    return nxt - timedelta(days=1)


def is_stale(published_date: date | None, label: str | None, first_seen: date, max_age_days: int | None) -> bool:
    """True when the paper was published more than `max_age_days` before we first stored it. Unknown dates are never stale; a month-only date
    counts as its last day, so '2026 Oct' is not stale in late October but '2026 Jul' is."""
    if max_age_days is None:
        return False
    latest = latest_possible_publication(published_date, label)
    return latest is not None and (first_seen - latest).days > max_age_days
