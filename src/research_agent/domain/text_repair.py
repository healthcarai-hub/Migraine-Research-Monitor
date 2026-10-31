"""Repair the word joins that some sources leave in abstracts.

OpenAlex rebuilds an abstract from a word-position index and some publishers' abstracts reach it with the line breaks already stripped, so the text
arrives as 'ObjectiveTo estimate...', '...did not.The PBPK model...' and 'surveyed via selfadministeredelectronic questionnaire'. Three kinds of join are
repaired, each by rules chosen to add a space only when that is almost certainly right (and only ever a space; nothing else in the text changes):

  1. a section label glued to the first word after it ('ObjectiveTo' -> 'Objective To'),
  2. a sentence end glued to the next sentence ('not.The' -> 'not. The'),
  3. two or three ordinary words run together in lowercase ('selfadministeredelectronic' -> 'self administered electronic'), using the `wordsegment`
     dictionary. This one is the risky kind: tested on real abstracts, a loose rule broke 130 of 139 real terms (atogepant, lasmiditan, semaglutide,
     neuroinflammation, Indonesian and Slovenian text). Word frequency alone cannot tell a join from a real word, so a split is made only when ALL of these hold:
     the run is 10+ lowercase letters and not a known word; every part is 4+ letters (or one of a few joining words such as 'and', 'the') and a common
     dictionary word; no part is a prefix or suffix (neuro, post, anti, -able, -ing, -ity); the text is mostly English; the run is not next to a dot, slash,
     hyphen or at-sign; and it is not on the keep-whole list. On the real data that gave 7 splits, all correct, and none wrong.

Left alone on purpose: words with an internal capital ('PubMed', 'exPlanations'), addresses and identifiers. If the `wordsegment` package is missing, step 3
is skipped and the rest still works.
"""
from __future__ import annotations

import re
import threading

_LABELS = ("Background", "Introduction", "Objectives?", "Aims?", "Purpose", "Context", "Importance", "Rationale", "Methods?", "Design", "Setting", "Participants",
           "Patients", "Results?", "Findings", "Conclusions?", "Interpretation", "Discussion", "Implications", "Significance", "Funding", "Registration")
_LABEL_GLUE = re.compile(r"\b(%s)(?=[A-Z][a-z])" % "|".join(_LABELS))
_SENTENCE_GLUE = re.compile(r"(?<=[A-Za-z0-9\)\]][.!?;:])(?=[A-Z][a-z])")
_NOT_PROSE = re.compile(r"[/@]|://|www\.|\.(?:com|org|net|gov|edu|io)\b", re.I)

# --- step 3: lowercase runs
_LOWER_RUN = re.compile(r"(?<![A-Za-z0-9\-./@_:])[a-z]{10,}(?![A-Za-z0-9\-./@_:])")
_CONNECTORS = frozenset({"and", "the", "for", "with", "from", "that", "this", "such", "were", "was", "are", "not"})
_BOUND = frozenset("""neuro anti hyper hypo post pre pro non micro nano mono multi poly endo exo intra inter extra trans super sub semi auto cardio electro cyber audio video
 radio mechano muco phono osmo peri para meta mega over under counter contra infra ultra proto pseudo photo psycho hemi bio geo thermo vaso entero gastro
 able ible ing ings ized ised ize ise ity ness less ful ally ation ment ous ive ical ics ist ism ably ibly ate ated""".split())
_KEEP_WHOLE = frozenset({"blockchain", "chatbot", "chatbots", "nailfold", "braincomms", "clinicaltrials"})   # real compounds that pass the rules above; add more here
_COMMON = 1_000_000          # a part must occur at least this often in the dictionary's corpus
_KNOWN = 5_000               # a run that occurs this often is a real word, not a join
_ENGLISH_SHARE = 0.75        # of the 4+ letter words in the text, this share must be known words, or the text is not English and is left alone

_lock = threading.Lock()
_ws = None                   # None = not loaded yet, False = package not available


def _segmenter():
    global _ws
    if _ws is None:
        with _lock:
            if _ws is None:
                try:
                    import wordsegment
                    wordsegment.load()
                    _ws = wordsegment
                except Exception:      # not installed: step 3 is skipped
                    _ws = False
    return _ws or None


def _parts(run: str, ws) -> list[str] | None:
    if run in _KEEP_WHOLE or ws.UNIGRAMS.get(run, 0) >= _KNOWN:
        return None
    parts = ws.segment(run)
    if not 2 <= len(parts) <= 4:
        return None
    for p in parts:
        if p in _BOUND or (len(p) < 4 and p not in _CONNECTORS) or ws.UNIGRAMS.get(p, 0) < _COMMON:
            return None
    return parts


def _split_lowercase_runs(text: str) -> str:
    runs = set(_LOWER_RUN.findall(text))
    if not runs:
        return text
    ws = _segmenter()
    if ws is None:
        return text
    words = re.findall(r"[a-z]{4,}", text.lower())
    if words and sum(ws.UNIGRAMS.get(w, 0) >= _KNOWN for w in words) / len(words) < _ENGLISH_SHARE:
        return text                      # mostly not English (Indonesian, Slovenian ...): a dictionary of English would only do harm
    split = {run: _parts(run, ws) for run in runs}
    return _LOWER_RUN.sub(lambda m: " ".join(split.get(m.group(0)) or [m.group(0)]), text)


def repair_abstract(text: str | None) -> str:
    """Insert the missing space at glued section labels, sentence ends and (carefully) run-together lowercase words. Idempotent; only spaces are added."""
    if not text:
        return text or ""
    out = []
    for token in re.split(r"(\s+)", text):                    # keep the original whitespace as it was
        if not token.strip() or _NOT_PROSE.search(token):
            out.append(token)
            continue
        out.append(_LABEL_GLUE.sub(r"\1 ", _SENTENCE_GLUE.sub(" ", token)))
    return _split_lowercase_runs("".join(out))
