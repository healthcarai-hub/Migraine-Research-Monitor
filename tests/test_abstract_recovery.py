"""Recovering an abstract from the paper's own text when no source supplies one (rare), and labelling full text in the edition."""
import httpx
import pytest
from sqlalchemy import select

from research_agent.application.fulltext import FullTextService
from research_agent.domain.capabilities import Rights, Tier
from research_agent.domain.fulltext import NOT_AVAILABLE, OK, FullTextResult, extract_abstract, jats_to_text
from research_agent.infrastructure.db import models as m
from research_agent.infrastructure.db.repository import Repository
from research_agent.infrastructure.db.session import session_scope
from conftest import rec
from test_fulltext_providers import LONG_ARTICLE, MIN_PDF, client
from test_fulltext_service import FakeProvider, rows, svc as ft_svc

BODY = ("This study examined how often migraine occurs among adults who work night shifts and whether sleep timing explains the association, using a cohort of "
        "1,200 nurses followed for two years with daily headache diaries.")
MORE = "We found a strong link and discuss the implications for scheduling and prevention in hospitals across several regions."


# ---------------------------------------------------------------- the extraction rules
def test_abstract_heading_then_text_until_keywords():
    got = extract_abstract(f"Journal of Things\nA title\nJ Smith, A Jones\nAbstract\n{BODY}\n{MORE}\nKeywords: migraine; shift work\n1 Introduction\nMigraine is common.")
    assert got and got[1] == "pdf_abstract" and got[0].startswith("This study examined") and got[0].endswith("regions.") and "Keywords" not in got[0] and "Introduction" not in got[0]


def test_abstract_on_the_same_line_as_the_heading_and_the_run_in_form():
    assert extract_abstract(f"Title\nAbstract: {BODY} {MORE}\nKey words: a")[1] == "pdf_abstract"
    assert extract_abstract(f"Title\nAbstract {BODY} {MORE}\nIntroduction\nx")[1] == "pdf_abstract"
    assert extract_abstract(f"Title\nABSTRACT\n{BODY} {MORE}\n\nKEYWORDS\nx")[1] == "pdf_abstract"


def test_summary_heading():
    got = extract_abstract(f"Title\nAuthors\nSummary: {BODY} {MORE}\nKey words: a, b\nIntroduction\ntext")
    assert got and got[1] == "pdf_summary"
    assert extract_abstract(f"Title\nSummary of the findings is given below as a sentence. {BODY} {MORE}\nIntroduction\nx") is None   # running prose, not a heading


def test_structured_abstract_without_a_heading():
    t = f"Title\nBackground: {BODY}\nMethods: We recruited nurses from six hospitals and followed them.\nResults: Risk was higher among night workers in every model we fitted.\nConclusions: Shift timing matters for headache burden among healthcare staff.\nIntroduction\nBlah"
    got = extract_abstract(t)
    assert got and got[1] == "pdf_structured" and got[0].startswith("Background:") and got[0].endswith("healthcare staff.") and "Blah" not in got[0]


def test_introduction_is_the_last_resort_and_limited_to_two_paragraphs():
    t = f"Title\nAuthors\nIntroduction\n{BODY}\n\n{MORE} {BODY}\n\nA third paragraph that must not be included in the recovered text at all.\n\nMethods\nx"
    got = extract_abstract(t)
    assert got and got[1] == "pdf_intro" and "third paragraph" not in got[0] and got[0].startswith("This study examined")


def test_nothing_found_or_too_short_returns_none():
    assert extract_abstract("Title\nJust a few lines\nand nothing like an abstract at all") is None
    assert extract_abstract("Title\nAbstract\nToo short to be an abstract.\nKeywords: a") is None
    assert extract_abstract("") is None


def test_only_the_first_pages_are_searched_and_hyphenated_words_are_joined():
    deep = "filler line of text\n" * 1200 + f"Abstract\n{BODY} {MORE}\nKeywords: a"
    assert extract_abstract(deep) is None
    hyph = f"Title\nAbstract\nThis study examined migraine among night-shift work-\ners and whether sleep timing explains the association in a cohort of 1,200 nurses followed for two years with daily diaries. {MORE}\nKeywords: a"
    assert "workers and" in extract_abstract(hyph)[0]


def test_a_real_pdf_through_pdftotext():
    from research_agent.infrastructure.fulltext import pdf_text
    if not pdf_text.available():
        pytest.skip("pdftotext not installed")
    words = f"{BODY} {MORE}".split()
    lines = [" ".join(words[i:i + 9]) for i in range(0, len(words), 9)]
    stream = "BT /F1 10 Tf 72 740 Td 12 TL (A study of shift work) Tj T* (Abstract) Tj T* " + " ".join(f"({ln.replace('(', '').replace(')', '')}) Tj T*" for ln in lines) + " (Keywords: migraine) Tj T* (1 Introduction) Tj T* (Migraine is common.) Tj ET"
    pdf = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
           b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
           + f"4 0 obj<</Length {len(stream)}>>stream\n{stream}\nendstream endobj\n".encode() + b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\ntrailer<</Root 1 0 R/Size 6>>\n%%EOF")
    got = extract_abstract(pdf_text.pdf_to_text(pdf))
    assert got and got[1] == "pdf_abstract" and "night shifts" in got[0] and "Keywords" not in got[0]


def test_jats_abstract_is_read_with_its_section_labels():
    xml = ("<article><front><article-meta><abstract><sec><title>Background</title><p>Migraine is common.</p></sec><sec><title>Results</title><p>Days fell.</p></sec></abstract>"
           "<abstract abstract-type=\"graphical\"><p>ignore me</p></abstract></article-meta></front><body><sec><title>I</title><p>Body text.</p></sec></body></article>")
    assert jats_to_text(xml).abstract == "BACKGROUND: Migraine is common.\nRESULTS: Days fell."
    assert jats_to_text("<article><front><article-meta/></front><body><sec><p>x</p></sec></body></article>").abstract == ""


# ---------------------------------------------------------------- providers attach what they find
def test_pdf_and_text_providers_return_the_abstract_they_find(monkeypatch):
    from research_agent.infrastructure.fulltext import pdf_text, providers as P
    from research_agent.domain.fulltext import FullTextRequest
    text = f"Title\nAbstract\n{BODY} {MORE}\nKeywords: a\nIntroduction\n" + "word " * 400
    monkeypatch.setattr(pdf_text, "available", lambda: True)
    monkeypatch.setattr(pdf_text, "pdf_to_text", lambda data: text)
    http, _ = client(lambda r: httpx.Response(200, content=MIN_PDF))
    res = P.WileyTdmFullText(http, "TOK").fetch(FullTextRequest(paper_id=1, doi="10.1002/x"))
    assert res.outcome == OK and res.abstract.startswith("This study examined") and res.abstract_kind == "pdf_abstract"
    http2, _ = client(lambda r: httpx.Response(200, text=text))
    res2 = P.ElsevierFullText(http2, "K").fetch(FullTextRequest(paper_id=1, doi="10.1016/x"))
    assert res2.abstract_kind == "pdf_abstract"
    jats = LONG_ARTICLE.replace("<body>", "<abstract><p>Short JATS abstract that the article carries itself.</p></abstract><body>", 1).replace("</article-meta>", "</article-meta>")
    http3, _ = client(lambda r: httpx.Response(200, text=jats.replace("<permissions>", "<abstract><p>Short JATS abstract carried by the article.</p></abstract><permissions>", 1)))
    res3 = P.EuropePmcFullText(http3).fetch(FullTextRequest(paper_id=1, pmcid="PMC1"))
    assert res3.outcome == OK and res3.abstract == "Short JATS abstract carried by the article." and res3.abstract_kind == "jats"


# ---------------------------------------------------------------- the service uses it
class TextProvider(FakeProvider):
    def __init__(self, text, kind="pdf_abstract", abstract=None, **kw):
        super().__init__("pdftext", 1, **kw)
        self.abstract, self.kind = abstract, kind

    def fetch(self, req):
        self.calls.append(req.paper_id)
        return FullTextResult(OK, self.name, text="word " * 500, format="pdf_text", licence=None, rights=Rights.UNKNOWN, abstract=self.abstract, abstract_kind=self.kind)


@pytest.fixture()
def no_abstract(factory, curt):
    profile, field = curt
    with session_scope(factory) as s:
        repo = Repository(s)
        repo.sync_config(profile, field)
        p = repo.merge_record(rec(sid="1", pmid="1", doi="10.1/n", abstract="", title="A study of shift work and headache in hospital nurses over two years")).paper
        repo.upsert_match(profile.id, p.id, gate_status="unknown", language_status="ok", track="A", subjects=[], entities=[])
        return profile, field, p.id


def test_a_paper_with_no_abstract_gets_one_from_its_text_and_is_re_judged(factory, no_abstract):
    profile, field, pid = no_abstract
    recovered = f"{BODY} {MORE}"
    summary = ft_svc(factory, TextProvider("x", abstract=recovered, kind="pdf_abstract")).run(profile, field_cfg=field)
    assert summary.abstracts_recovered == 1 and summary.obtained == 1
    with session_scope(factory) as s:
        p = s.get(m.Paper, pid)
        mt = s.scalar(select(m.ProfileMatch).where(m.ProfileMatch.paper_id == pid))
        assert p.abstract == recovered and p.abstract_source == "pdf_abstract"
        assert mt.gate_status == "passed" and any(x["code"] == "1" for x in mt.subjects)       # "migraine" is in the recovered text: now relevant, with subjects


def test_a_recovered_abstract_that_lacks_the_field_fails_the_gate(factory, no_abstract):
    profile, field, pid = no_abstract
    other = "This study examined sleep timing among adults who work night shifts using a cohort of nurses followed for two years with daily diaries of how they felt each morning and evening."
    ft_svc(factory, TextProvider("x", abstract=other, kind="pdf_abstract")).run(profile, field_cfg=field)
    with session_scope(factory) as s:
        assert s.scalar(select(m.ProfileMatch.gate_status).where(m.ProfileMatch.paper_id == pid)) == "failed"




def test_papers_that_already_have_an_abstract_keep_it(factory, curt):
    profile, field = curt
    with session_scope(factory) as s:
        repo = Repository(s)
        repo.sync_config(profile, field)
        p = repo.merge_record(rec(sid="1", pmid="1", doi="10.1/a", abstract="The original abstract from PubMed mentioning migraine.")).paper
        repo.upsert_match(profile.id, p.id, gate_status="passed", language_status="ok", track="A", subjects=[], entities=[])
        pid = p.id
    summary = ft_svc(factory, TextProvider("x", abstract=f"{BODY} {MORE}")).run(profile, field_cfg=field)
    assert summary.abstracts_recovered == 0
    with session_scope(factory) as s:
        p = s.get(m.Paper, pid)
        assert p.abstract == "The original abstract from PubMed mentioning migraine." and p.abstract_source == "source"


def test_papers_with_undecided_relevance_are_fetched_so_their_abstract_can_be_found(factory, no_abstract):
    profile, field, pid = no_abstract
    with session_scope(factory) as s:
        assert [p.id for p in Repository(s).fulltext_candidates(profile.id, exclude_types=[])] == [pid]


def test_backfill_recovers_abstracts_from_texts_already_stored(factory, no_abstract):
    profile, field, pid = no_abstract
    text = f"Title\nAbstract\n{BODY} {MORE}\nKeywords: a\nIntroduction\n" + "word " * 300
    with session_scope(factory) as s:
        s.add(m.PaperFulltext(paper_id=pid, status="full_text", format="pdf_text", provider="open_access_pdf", words=400, text=text))
    service = ft_svc(factory)
    assert service.recover_abstracts(profile, field) == 1
    with session_scope(factory) as s:
        assert s.get(m.Paper, pid).abstract_source == "pdf_abstract" and s.scalar(select(m.ProfileMatch.gate_status)) == "passed"
    assert service.recover_abstracts(profile, field) == 0          # nothing left to recover


# ---------------------------------------------------------------- the edition
def test_edition_labels_full_text_and_pdf_derived_abstracts(factory, curt):
    from test_newsletter import WEEK_END, add, svc as nl_svc, seeded  # noqa: F401
    profile, field = curt
    with session_scope(factory) as s:
        Repository(s).sync_config(profile, field)
    full = add(factory, profile, 1)
    none = add(factory, profile, 2)
    blocked = add(factory, profile, 3)
    pdf_abs = add(factory, profile, 4)
    intro = add(factory, profile, 5)
    with session_scope(factory) as s:
        s.add(m.PaperFulltext(paper_id=full, status="full_text", rights="entitled", print_allowed=True, words=5000, text="x"))
        s.add(m.PaperFulltext(paper_id=blocked, status="full_text", rights="open", print_allowed=False, words=5000, text="x"))
        s.get(m.Paper, pdf_abs).abstract_source = "pdf_abstract"
        s.get(m.Paper, intro).abstract_source = "pdf_intro"
    nl_svc(factory).build(profile, field, WEEK_END)
    with session_scope(factory) as s:
        text = s.scalar(select(m.Issue.text))
        html = s.scalar(select(m.Issue.html))
    blocks = {pid: b for pid, b in zip([full, none, blocked, pdf_abs, intro], ["" for _ in range(5)])}
    def block_for(pid):
        title = f"Migraine study number {pid} with a sufficiently long title"
        i = text.index(title)
        return text[i:i + 520]
    assert "Full text available" in block_for(full) and "Full text available" not in block_for(none) and "Full text available" not in block_for(blocked)
    assert "Abstract recovered from the PDF" in block_for(pdf_abs) and "No abstract: summary from the introduction" in block_for(intro)
    assert 'class="badge">Full text available' in html


# ---------------------------------------------------------------- fixes from the real-PDF blind test
def test_a_sentence_starting_with_published_does_not_end_the_abstract():
    t = f"Title\nAbstract\n{BODY}\nPublished examples include aphasia rehabilitation and migraine prevention among several others in digital care.\n{MORE}\nKeywords: a"
    got = extract_abstract(t)
    assert got and "Published examples include" in got[0] and got[0].endswith("regions.")
    assert "Published: 12 October 2026" not in extract_abstract(f"Title\nAbstract\n{BODY} {MORE}\nPublished: 12 October 2026\nmore words here\nKeywords: a")[0]


def test_abstract_placed_just_before_the_keywords_line_with_no_heading():
    t = ("Type Original Research\nEDITED BY\nSomeone\nTitle of the paper that goes on for a while\nA Smith1, B Jones2\n1 Department of Neurology, Some University, Rome, Italy\n"
         "2 School of Health, Another University, Berlin, Germany\n\n" + f"{BODY}\n{MORE}\nKEYWORDS\nmigraine, shift work\n\n1 Introduction\nMigraine is common.")
    got = extract_abstract(t)
    assert got and got[1] == "pdf_abstract" and got[0].startswith("This study examined") and got[0].endswith("regions.") and "University" not in got[0] and "KEYWORDS" not in got[0]


def test_footnote_markers_and_address_lines_do_not_leak_into_the_recovered_abstract():
    t = f"Title here that is long\nA Smith1\n1 Department of Neurology, X Hospital, Zhengzhou, China\nUniversity Hospital, Zhengzhou, China\n1\n\n{BODY}\n\n{MORE}\nKeywords: a, b"
    got = extract_abstract(t)
    assert got and got[0].startswith("This study examined") and "Zhengzhou" not in got[0]


# ---------------------------------------------------------------- is this text really that paper? (OpenAlex once supplied a link to another article)
def test_text_matches_paper_by_doi_or_title():
    from research_agent.domain.fulltext import text_matches_paper
    title = "Gepants for the Treatment of Migraine in Children and Adolescents: Acute and Preventive Roles"
    right = "Journal\nGepants for the Treatment of Migraine in Children and Adolescents:\nAcute and Preventive Roles\nA Author\nAbstract ..."
    wrong = "Onofri et al. The Journal of Headache and Pain (2023) 24:8 https://doi.org/10.1186/s10194-023-01541-0\nPrimary headache epidemiology in children and adolescents: a systematic review"
    assert text_matches_paper(right, title, "10.62087/hpr.2026.0034")
    assert not text_matches_paper(wrong, title, "10.62087/hpr.2026.0034")
    assert text_matches_paper(wrong + "\nDOI: 10.62087/HPR.2026.0034", title, "10.62087/hpr.2026.0034")        # the DOI alone settles it, whatever the case or prefix
    assert text_matches_paper(wrong, "Short", "10.1/x")                                                          # too little to judge: benefit of the doubt
    assert text_matches_paper(wrong, "", None)


def test_a_provider_rejects_a_pdf_that_is_another_paper_and_the_next_link_is_tried(monkeypatch):
    from research_agent.domain.fulltext import FullTextRequest
    from research_agent.infrastructure.fulltext import pdf_text, providers as P
    title = "Gepants for the Treatment of Migraine in Children and Adolescents Acute and Preventive Roles"
    pages = {"https://wrong.test/a.pdf": "Primary headache epidemiology in children a systematic review " + "word " * 400,
             "https://right.test/b.pdf": title + "\nAbstract " + "word " * 400}
    monkeypatch.setattr(pdf_text, "available", lambda: True)
    monkeypatch.setattr(pdf_text, "pdf_to_text", lambda data: pages[data.decode()])
    http, seen = client(lambda r: httpx.Response(200, content=str(r.url).encode()))
    req = FullTextRequest(paper_id=1, doi="10.62087/hpr.2026.0034", title=title,
                          links=[{"kind": "pdf", "url": u, "is_free": True} for u in pages])
    res = P.OpenAccessPdf(http).fetch(req)
    assert res.outcome == OK and res.source_url == "https://right.test/b.pdf" and len(seen) == 2
    only_wrong = FullTextRequest(paper_id=1, doi="10.62087/hpr.2026.0034", title=title, links=[{"kind": "pdf", "url": "https://wrong.test/a.pdf", "is_free": True}])
    bad = P.OpenAccessPdf(http).fetch(only_wrong)
    assert bad.outcome == NOT_AVAILABLE and "does not appear to be this paper" in bad.detail


def test_verify_stored_removes_texts_that_are_not_their_paper(factory, curt):
    profile, field = curt
    with session_scope(factory) as s:
        repo = Repository(s)
        repo.sync_config(profile, field)
        good = repo.merge_record(rec(sid="1", pmid="1", doi="10.1/good", title="Migraine prevention in adults with a long descriptive title here")).paper
        bad = repo.merge_record(rec(sid="2", pmid="2", doi="10.1/bad", title="Gepants for migraine in children and adolescents acute and preventive")).paper
        s.add(m.PaperFulltext(paper_id=good.id, status="full_text", format="pdf_text", print_allowed=True, words=500, text="Migraine prevention in adults with a long descriptive title here\n" + "w " * 400))
        s.add(m.PaperFulltext(paper_id=bad.id, status="full_text", format="pdf_text", provider="open_access_pdf", print_allowed=True, words=500, text="Totally different paper about epilepsy\n" + "w " * 400))
        bad_id = bad.id
    removed = ft_svc(factory).verify_stored(profile)
    assert [r[0] for r in removed] == [bad_id]
    row = rows(factory)[bad_id]
    assert row.status == "abstract_and_links_only" and row.text is None and row.print_allowed is False
    with session_scope(factory) as s:
        assert s.scalar(select(m.FulltextAttempt.detail).where(m.FulltextAttempt.paper_id == bad_id)).startswith("stored text is not this paper")
    assert ft_svc(factory).verify_stored(profile) == []
