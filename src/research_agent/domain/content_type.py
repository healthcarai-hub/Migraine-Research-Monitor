"""What kind of item a record is, independent of the field. Used to keep repository uploads, datasets and book chapters out of digests
(they stay in the database). Preprints from preprint servers are NOT excluded: the owner wants them, labelled non-peer-reviewed."""
from __future__ import annotations

import re

JOURNAL_ARTICLE = "journal_article"
PREPRINT = "preprint"
TRIAL = "trial"
REPOSITORY_DEPOSIT = "repository_deposit"
DATASET = "dataset"
BOOK_CHAPTER = "book_chapter"
ERRATUM = "erratum"      # corrections, errata, corrigenda
LETTER = "letter"        # letters to the editor, reader responses, replies, comments, editors' notes

ALL_TYPES = (JOURNAL_ARTICLE, PREPRINT, TRIAL, REPOSITORY_DEPOSIT, DATASET, BOOK_CHAPTER, ERRATUM, LETTER)
DEFAULT_DIGEST_EXCLUDED = (REPOSITORY_DEPOSIT, DATASET, BOOK_CHAPTER, ERRATUM, LETTER)

# DOI registrant prefixes of general-purpose repositories (self-deposit, no editorial review)
REPOSITORY_DOI_PREFIXES = ("10.5281/", "10.6084/", "10.17605/", "10.5061/", "10.17632/", "10.7910/")
REPOSITORY_NAMES = ("zenodo", "figshare", "open science framework", "dryad", "mendeley data", "dataverse", "repository", "eprints", "dspace",
                    "strathprints", "researchgate", "archive ouverte")
DATASET_TYPES = {"openalex:dataset", "crossref:dataset", "crossref:database", "crossref:component"}
CHAPTER_TYPES = {"book chapter", "openalex:book-chapter", "crossref:book-chapter", "crossref:reference-entry", "openalex:reference-entry", "crossref:book-part",
                 "crossref:book-section", "crossref:book", "openalex:book"}


ERRATUM_TYPES = {"published erratum", "erratum", "openalex:erratum", "crossref:correction", "retraction of publication"}
LETTER_TYPES = {"letter", "openalex:letter", "comment"}
# Titles that are unambiguous. 'Response to ...' is deliberately absent: 'Response to erenumab in ...' is a research title.
_ERRATUM_TITLE = re.compile(r"^\s*(?:(?:correction|addendum)\s*(?:to\b|[:\u2014\u2013-])|erratum\b|errata\b|corrigendum\b|publisher correction\b|author correction\b)", re.I)
_LETTER_TITLE = re.compile(r"^\s*(?:reader response|author response|reply to|reply:|comment on|comments on|editors?['\u2019] note|letter to the editor|in reply)\b", re.I)
SHORT_ABSTRACT_CHARS = 300   # a paper typed 'Letter' that has a real abstract is treated as research (research letters exist)


def classify(kind: str, pub_types: list[str], journal: str | None, doi: str | None, title: str = "", abstract: str = "") -> str:
    types = {t.lower() for t in pub_types or []}
    name = (journal or "").lower()
    if kind == "trial":
        return TRIAL
    if types & ERRATUM_TYPES or _ERRATUM_TITLE.match(title or ""):
        return ERRATUM
    if _LETTER_TITLE.match(title or "") or (types & LETTER_TYPES and len((abstract or "").strip()) < SHORT_ABSTRACT_CHARS):
        return LETTER
    if types & DATASET_TYPES:
        return DATASET
    if doi and doi.lower().startswith(REPOSITORY_DOI_PREFIXES):
        return REPOSITORY_DEPOSIT
    if any(n in name for n in REPOSITORY_NAMES) and not (types & {"journal article"}):
        return REPOSITORY_DEPOSIT
    if types & CHAPTER_TYPES:
        return BOOK_CHAPTER
    if kind == "preprint":
        return PREPRINT
    return JOURNAL_ARTICLE
