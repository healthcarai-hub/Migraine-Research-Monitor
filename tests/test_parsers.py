"""Parsers run against real API responses captured on 2026-10-30 (tests/fixtures)."""
from datetime import date

from research_agent.domain.retraction import apply_title_signals
from research_agent.infrastructure.sources import clinicaltrials, crossref, europepmc, openalex, pubmed
from conftest import fixture_json, fixture_text


def test_pubmed_efetch():
    recs = {r.pmid: r for r in pubmed.parse_efetch(fixture_text("pubmed_efetch.xml"))}
    assert len(recs) == 3
    normal = recs["42961170"]
    assert normal.doi == "10.1002/brb3.71881" and normal.entry_date == date(2026, 10, 30) and normal.languages == ["eng"]
    assert normal.issns == ["2162-3279"] and len(normal.authors) == 16 and len(normal.abstract) > 1000
    assert not normal.retracted and not normal.retraction_notice
    assert any(l.kind == "doi" for l in normal.links) and normal.url.endswith("/42961170/")
    retracted = recs["40258728"]
    assert retracted.retracted and "Retracted Publication" in retracted.pub_types
    notice = recs["41890548"]
    assert notice.retraction_notice and notice.retraction_target_pmid == "39035702" and not notice.retracted


def test_pubmed_abstract_sections_are_labelled():
    xml = """<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>1</PMID><Article><Journal><Title>J</Title></Journal>
    <ArticleTitle>[Translated title of reasonable length]</ArticleTitle><Abstract>
    <AbstractText Label="BACKGROUND">Bg <i>text</i>.</AbstractText><AbstractText Label="UNLABELLED">Free.</AbstractText></Abstract>
    <AuthorList><Author><CollectiveName>The Consortium</CollectiveName></Author></AuthorList></Article></MedlineCitation>
    <PubmedData><History><PubMedPubDate PubStatus="entrez"><Year>2026</Year><Month>10</Month><Day>5</Day></PubMedPubDate></History></PubmedData>
    </PubmedArticle></PubmedArticleSet>"""
    r = pubmed.parse_efetch(xml)[0]
    assert r.title == "Translated title of reasonable length" and r.abstract == "BACKGROUND: Bg text.\nFree."
    assert r.authors[0].family == "The Consortium" and r.entry_date == date(2026, 10, 5) and r.doi is None


def test_pubmed_month_only_dates_are_never_treated_as_exact():
    xml = """<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>2</PMID><Article><Journal><JournalIssue><PubDate><Year>2026</Year><Month>Oct</Month></PubDate></JournalIssue></Journal>
    <ArticleTitle>T</ArticleTitle></Article></MedlineCitation><PubmedData/></PubmedArticle></PubmedArticleSet>"""
    assert pubmed.parse_efetch(xml)[0].published_date is None


def test_europepmc_results():
    recs = [europepmc.parse_result(r) for r in fixture_json("europepmc_search.json")["resultList"]["result"]]
    med, ppr = recs[0], recs[2]
    assert med.source_id.startswith("MED:") and med.pmid and med.doi and med.entry_date == date(2026, 10, 30) and not med.is_preprint
    assert "<" not in med.abstract  # markup stripped
    assert ppr.is_preprint and ppr.pmid is None and ppr.source_id.startswith("PPR:")
    assert ppr.journal  # preprint server name stands in for the journal


def test_crossref_items_and_retraction_semantics():
    items = [crossref.parse_item(i) for i in fixture_json("crossref_works.json")["message"]["items"]]
    prep, art, withdrawn, retracted, notice = items
    assert prep.is_preprint and prep.pub_types == ["crossref:posted-content"] and prep.entry_date == date(2026, 10, 29)
    assert art.entry_date == date(2026, 10, 30) and "<" not in art.abstract and art.abstract
    # a retracted article lists a retraction pointing at itself: that makes it retracted, NOT a notice
    for r in (withdrawn, retracted):
        assert r.retracted and not r.retraction_notice and r.retraction_target_doi is None
    # Crossref types this real retraction notice as an erratum, so metadata alone cannot identify it; the title signal does
    assert not notice.retraction_notice
    assert apply_title_signals(notice).retraction_notice
    assert retracted.licences == ["http://creativecommons.org/licenses/by-nc/4.0/"]


def test_crossref_notice_with_a_different_target():
    item = {"DOI": "10.1/notice", "title": ["Retraction: x"], "type": "journal-article", "update-to": [{"DOI": "10.1/Original", "type": "retraction"}]}
    r = crossref.parse_item(item)
    assert r.retraction_notice and r.retraction_target_doi == "10.1/original"


def test_openalex_works():
    works = [openalex.parse_work(w) for w in fixture_json("openalex_works.json")["results"]]
    normal, dataset, retracted = works
    assert normal.abstract and normal.entry_date == date(2026, 10, 30) and normal.oa_status == "hybrid" and normal.pub_types == ["openalex:article"]
    assert dataset.pub_types == ["openalex:dataset"]
    assert retracted.retracted and retracted.doi


def test_openalex_abstract_rebuild():
    assert openalex.rebuild_abstract({"b": [1], "a": [0], "c": [2, 3]}) == "a b c c"
    assert openalex.rebuild_abstract(None) == ""


def test_ctgov_studies():
    recs = [clinicaltrials.parse_study(s) for s in fixture_json("ctgov_studies.json")["studies"]]
    assert recs[0].nct_id.startswith("NCT") and recs[0].is_trial and recs[0].entry_date == recs[0].published_date
    assert recs[0].url.endswith(recs[0].nct_id) and recs[0].languages == ["eng"]
    assert clinicaltrials.parse_study({"protocolSection": {}}) is None


def test_title_signals_do_not_overreach():
    from research_agent.domain.models import PaperRecord
    for t, notice, retracted in [("Retraction notice to \"X\"", True, False), ("RETRACTED: X", False, True), ("WITHDRAWN: X", False, True),
                                 ("Retraction of a claim about migraine: a review", False, False), ("Retractions in migraine research", False, False)]:
        r = apply_title_signals(PaperRecord("x", "1", title=t))
        assert (r.retraction_notice, r.retracted) == (notice, retracted), t


def test_html_entities_in_journal_names_are_unescaped():
    from research_agent.infrastructure.sources.common import clean_text
    assert clean_text("Research &amp; Yoga  Sciences") == "Research & Yoga Sciences" and clean_text(None) is None
    w = {"id": "https://openalex.org/W1", "title": "A &amp; B", "primary_location": {"source": {"display_name": "Yoga &amp; Health"}}}
    r = openalex.parse_work(w)
    assert r.journal == "Yoga & Health" and r.title == "A & B"


BOOK_XML = """<PubmedArticleSet><PubmedBookArticle><BookDocument><PMID Version="1">29763194</PMID>
<ArticleIdList><ArticleId IdType="bookaccession">NBK500018</ArticleId></ArticleIdList>
<Book><Publisher><PublisherName>StatPearls Publishing</PublisherName></Publisher><BookTitle book="statpearls">StatPearls</BookTitle></Book>
<ArticleTitle>Migraine Headache Prophylaxis</ArticleTitle><Abstract><AbstractText>Migraine is a common disorder.</AbstractText></Abstract>
<AuthorList><Author><LastName>Doe</LastName><ForeName>Jane</ForeName><Initials>J</Initials></Author></AuthorList><Language>eng</Language></BookDocument>
<PubmedBookData><History><PubMedPubDate PubStatus="entrez"><Year>2026</Year><Month>8</Month><Day>29</Day></PubMedPubDate></History></PubmedBookData></PubmedBookArticle></PubmedArticleSet>"""


def test_pubmed_bookshelf_chapters_are_parsed_not_dropped():
    from research_agent.domain import content_type as ct
    (r,) = pubmed.parse_efetch(BOOK_XML)
    assert r.pmid == "29763194" and r.title == "Migraine Headache Prophylaxis" and r.journal == "StatPearls" and r.doi is None
    assert r.entry_date == date(2026, 8, 29) and r.languages == ["eng"] and r.url.endswith("/books/NBK500018/") and r.authors[0].family == "Doe"
    assert ct.classify("article", r.pub_types, r.journal, r.doi) == ct.BOOK_CHAPTER   # stored, kept out of digests by default


def test_a_pubmed_response_mixing_articles_and_book_chapters_loses_nothing():
    xml = fixture_text("pubmed_efetch.xml").replace("</PubmedArticleSet>", BOOK_XML.split("<PubmedArticleSet>")[1])
    recs = pubmed.parse_efetch(xml)
    assert len(recs) == 4 and {r.pmid for r in recs} >= {"29763194", "42961170"}
