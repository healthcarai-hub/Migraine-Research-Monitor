import pytest

from research_agent.domain.identity import normalise_doi, normalise_issn, normalise_pmcid, normalise_pmid, normalise_title, title_fingerprint


@pytest.mark.parametrize("raw,expected", [
    ("10.1016/J.Foo.2020.1", "10.1016/j.foo.2020.1"),
    ("https://doi.org/10.1000/ABC.", "10.1000/abc"),
    ("http://dx.doi.org/10.1000/abc", "10.1000/abc"),
    ("doi: 10.1000/abc", "10.1000/abc"),
    ("  10.1000/abc);", "10.1000/abc"),
    ("not-a-doi", None), ("", None), (None, None), ("10.1000", None),
])
def test_normalise_doi(raw, expected):
    assert normalise_doi(raw) == expected


def test_other_identifiers():
    assert normalise_pmid(" 123 ") == "123" and normalise_pmid("abc") is None
    assert normalise_pmcid("pmc123") == "PMC123" and normalise_pmcid("123") == "PMC123" and normalise_pmcid("x") is None
    assert normalise_issn("14682982") == "1468-2982" and normalise_issn("1526-632x") == "1526-632X" and normalise_issn("12") is None


def test_title_fingerprint_ignores_case_markup_accents_and_punctuation():
    a = "Migraine <i>prevention</i>: a randomised trial of café-derived extract"
    b = "MIGRAINE PREVENTION - A randomised trial of cafe derived extract"
    assert title_fingerprint(a) == title_fingerprint(b)
    assert title_fingerprint("Short title") is None  # too short to identify anything
    assert normalise_title(None) == ""
