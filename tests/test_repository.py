from datetime import date

from sqlalchemy import select

from research_agent.domain.models import Link, SearchSpec
from research_agent.infrastructure.db import models as m
from research_agent.infrastructure.db.repository import Repository
from research_agent.infrastructure.db.session import session_scope
from conftest import rec


def merge(repo, **kw):
    return repo.merge_record(rec(**kw))


def test_same_paper_from_two_sources_is_one_row(factory):
    with session_scope(factory) as s:
        repo = Repository(s)
        a = merge(repo, source="pubmed", sid="111", doi="10.1/x", pmid="111", abstract="Short migraine abstract.", issns=["1234-5678"], languages=["eng"])
        b = merge(repo, source="crossref", sid="10.1/x", doi="10.1/x", abstract="A much longer migraine abstract than the first.", issns=["8765-4321"], languages=[])
        assert a.created and not b.created and a.paper.id == b.paper.id
        p = b.paper
        assert p.pmid == "111" and p.abstract.startswith("A much longer") and set(p.issns) == {"1234-5678", "8765-4321"}


def test_matching_order_doi_pmid_nct_pmcid_then_title(factory):
    with session_scope(factory) as s:
        repo = Repository(s)
        first = merge(repo, doi="10.1/a", pmid="1").paper
        assert merge(repo, sid="2", pmid="1", doi=None).paper.id == first.id          # by PMID
        assert merge(repo, sid="3", pmcid="PMC9", doi="10.1/a").paper.id == first.id   # by DOI, learns the PMCID
        assert first.pmcid == "PMC9"
        title = "A distinctive title about migraine genetics that is long enough"
        t1 = merge(repo, sid="4", title=title, doi=None, pmid=None).paper
        assert merge(repo, sid="5", title=title.upper(), doi="10.9/z", pmid=None).paper.id == t1.id  # DOI-less record adopts the DOI
        assert t1.doi == "10.9/z"


def test_preprint_and_published_version_with_different_dois_stay_separate(factory):
    title = "Same title for the preprint and the journal version of this migraine study"
    with session_scope(factory) as s:
        repo = Repository(s)
        pre = merge(repo, source="europepmc", sid="PPR:1", title=title, doi="10.1101/pre", is_preprint=True).paper
        pub = merge(repo, source="pubmed", sid="2", title=title, doi="10.1002/pub", pmid="2").paper
        assert pre.id != pub.id and pre.kind == "preprint" and pub.kind == "article"


def test_preprint_becomes_article_when_a_source_reports_it_published(factory):
    with session_scope(factory) as s:
        repo = Repository(s)
        p = merge(repo, source="europepmc", sid="PPR:1", doi="10.1/p", is_preprint=True).paper
        assert p.kind == "preprint"
        merge(repo, source="pubmed", sid="9", doi="10.1/p", pmid="9")
        assert p.kind == "article"
        merge(repo, source="europepmc", sid="PPR:1", doi="10.1/p", is_preprint=True)
        assert p.kind == "article"  # never reverts


def test_trials_never_merge_by_title(factory):
    title = "Efficacy of rimegepant for the acute treatment of migraine in adults"
    with session_scope(factory) as s:
        repo = Repository(s)
        t = merge(repo, source="ctgov", sid="NCT1", nct_id="NCT1", title=title, doi=None, is_trial=True).paper
        a = merge(repo, source="pubmed", sid="5", title=title, doi="10.1/a", pmid="5").paper
        assert t.id != a.id and t.kind == "trial"


def test_conflicting_identifier_is_not_overwritten(factory):
    with session_scope(factory) as s:
        repo = Repository(s)
        a = merge(repo, doi="10.1/a", pmid="1").paper
        b = merge(repo, sid="2", doi="10.1/b", pmid="2", title="A completely different migraine paper with its own long title").paper
        merge(repo, sid="3", doi="10.1/a", pmid="2")  # claims a's DOI and b's PMID: must not steal b's PMID
        assert a.pmid == "1" and b.pmid == "2"


def test_flags_are_sticky_and_dates_keep_the_earliest_entry(factory):
    with session_scope(factory) as s:
        repo = Repository(s)
        p = merge(repo, source="pubmed", doi="10.1/a", retracted=True, entry_date=date(2026, 10, 30)).paper
        merge(repo, source="crossref", sid="10.1/a", doi="10.1/a", retracted=False, entry_date=date(2026, 10, 28))
        assert p.is_retracted and p.retraction_sources == ["pubmed"] and p.first_entry_date == date(2026, 10, 28)


def test_links_are_deduplicated(factory):
    with session_scope(factory) as s:
        repo = Repository(s)
        link = Link("doi", "https://doi.org/10.1/a", "pubmed")
        merge(repo, doi="10.1/a", links=[link])
        merge(repo, source="crossref", doi="10.1/a", links=[link, Link("pdf", "https://x/pdf", "crossref")])
        assert s.query(m.PaperLink).count() == 2


def test_retraction_notice_is_linked_and_flags_the_original(factory):
    with session_scope(factory) as s:
        repo = Repository(s)
        original = merge(repo, doi="10.1/orig", pmid="10").paper
        notice = merge(repo, sid="11", pmid="11", doi="10.1/notice", title="Retraction notice to a migraine study that was retracted later",
                       retraction_notice=True, retraction_target_pmid="10").paper
        assert not original.is_retracted
        assert repo.resolve_retraction_targets() == 1
        assert notice.retraction_target_id == original.id and original.is_retracted and "notice" in original.retraction_sources
        assert repo.resolve_retraction_targets() == 0  # idempotent


def test_notice_whose_original_we_never_stored_stays_unlinked(factory):
    with session_scope(factory) as s:
        repo = Repository(s)
        n = merge(repo, doi="10.1/n", retraction_notice=True, retraction_target_doi="10.1/never-seen").paper
        assert repo.resolve_retraction_targets() == 0 and n.retraction_target_id is None


def test_hits_are_unique_per_paper_spec_run(factory, curt):
    profile, field = curt
    with session_scope(factory) as s:
        repo = Repository(s)
        repo.sync_config(profile, field)
        spec = SearchSpec("topic", "pubmed", "t", {"term": "x"})
        row = repo.upsert_spec(profile.id, spec)
        run = repo.start_run(profile.id, date(2026, 10, 27), date(2026, 10, 30), "test")
        paper = merge(repo, doi="10.1/a", pmid="1").paper
        r = rec(doi="10.1/a", pmid="1")
        assert repo.add_hit(paper, run, row, spec, r) is True
        assert repo.add_hit(paper, run, row, spec, r) is False


def test_sync_config_is_idempotent_and_membership_follows_the_profile(factory, curt):
    profile, field = curt
    with session_scope(factory) as s:
        repo = Repository(s)
        repo.sync_config(profile, field)
        repo.sync_config(profile, field)
        assert s.query(m.Journal).count() == 10 and s.query(m.Researcher).count() == 30 and s.query(m.Subject).count() == 12
        assert s.query(m.ProfileJournal).count() == 10
        smaller = profile.model_copy(update={"journals": profile.journals[:3], "subjects": ["1", "2"]})
        repo.sync_config(smaller, field)
        assert s.query(m.ProfileJournal).count() == 3 and s.query(m.ProfileSubject).count() == 2
        assert s.query(m.Journal).count() == 10  # the catalogue keeps its rows; only the profile's choice shrinks


def test_retiring_specs_only_deactivates_searches_nobody_else_uses(factory, curt):
    profile, field = curt
    with session_scope(factory) as s:
        repo = Repository(s)
        repo.sync_config(profile, field)
        keep, drop = SearchSpec("topic", "pubmed", "keep", {"term": "k"}), SearchSpec("topic", "pubmed", "drop", {"term": "d"})
        repo.upsert_spec(profile.id, keep); drop_row = repo.upsert_spec(profile.id, drop)
        assert repo.retire_specs_not_in(profile.id, {keep.key}) == 1
        assert drop_row.active is False


def test_stale_runs_are_closed_but_fresh_ones_are_not(factory, curt):
    from datetime import datetime, timedelta, timezone
    profile, field = curt
    with session_scope(factory) as s:
        repo = Repository(s)
        repo.sync_config(profile, field)
        old = repo.start_run(profile.id, date(2026, 1, 1), date(2026, 1, 4), "t"); old.started_at = datetime.now(timezone.utc) - timedelta(hours=9)
        fresh = repo.start_run(profile.id, date(2026, 1, 1), date(2026, 1, 4), "t")
        assert repo.close_stale_runs(hours=3) == 1 and old.status == "failed" and fresh.status == "running"


def test_content_type_rules():
    from research_agent.domain import content_type as ct
    c = ct.classify
    assert c("article", ["Journal Article"], "Cephalalgia", "10.1177/1") == ct.JOURNAL_ARTICLE
    assert c("article", ["openalex:dataset"], "Zenodo", "10.5281/zenodo.1") == ct.DATASET
    assert c("article", ["openalex:article"], "Zenodo (CERN)", "10.5281/zenodo.2") == ct.REPOSITORY_DEPOSIT
    assert c("article", [], None, "10.17605/OSF.IO/ABC") == ct.REPOSITORY_DEPOSIT       # by DOI prefix alone
    assert c("article", ["openalex:article"], "Open Science Framework", None) == ct.REPOSITORY_DEPOSIT
    assert c("article", ["openalex:article"], "Strathprints: The University of Strathclyde institutional repository", "10.1/x") == ct.REPOSITORY_DEPOSIT
    assert c("article", ["Journal Article"], "Frontiers in Neurology", "10.3389/x") == ct.JOURNAL_ARTICLE
    assert c("article", ["crossref:book-chapter"], "Elsevier eBooks", "10.1016/b978") == ct.BOOK_CHAPTER
    assert c("preprint", ["openalex:preprint"], "medRxiv", "10.1101/2026.1") == ct.PREPRINT  # preprint servers are wanted
    assert c("trial", ["Clinical Trial Registration"], "ClinicalTrials.gov", None) == ct.TRIAL


def test_digest_candidates_respect_exclusions_language_and_gate(factory, curt):
    from datetime import datetime, timedelta, timezone
    profile, field = curt
    with session_scope(factory) as s:
        repo = Repository(s)
        repo.sync_config(profile, field)
        def add(sid, doi, gate="passed", lang="ok", **kw):
            p = repo.merge_record(rec(sid=sid, doi=doi, pmid=sid, title=f"Migraine paper number {sid} with a long enough title", **kw)).paper
            repo.upsert_match(profile.id, p.id, gate_status=gate, language_status=lang, track="A", subjects=[], entities=[])
            return p
        add("1", "10.1/journal")
        add("2", "10.5281/zenodo.2")
        add("3", "10.1/failed", gate="failed")
        add("4", "10.1/german", lang="other")
        add("5", "10.1/nolang", lang="unknown")
        s.flush()
        got = {p.doi for p, _ in repo.digest_candidates(profile.id, exclude_types=profile.digest.exclude_content_types)}
        assert got == {"10.1/journal", "10.1/nolang"}
        assert {p.doi for p, _ in repo.digest_candidates(profile.id, exclude_types=[])} == {"10.1/journal", "10.1/nolang", "10.5281/zenodo.2"}
        assert {p.doi for p, _ in repo.digest_candidates(profile.id, include_unknown_language=False)} == {"10.1/journal", "10.5281/zenodo.2"}
        assert repo.digest_candidates(profile.id, since=datetime.now(timezone.utc) + timedelta(days=1)) == []


def test_reclassify_updates_rows_when_rules_change(factory):
    with session_scope(factory) as s:
        repo = Repository(s)
        p = merge(repo, doi="10.5281/zenodo.9", pub_types=["openalex:article"]).paper
        p.content_type = "journal_article"
        assert repo.reclassify_all() == 1 and p.content_type == "repository_deposit" and repo.reclassify_all() == 0


def test_profile_digest_config_defaults_and_validation(curt):
    import pytest as _pt
    from pydantic import ValidationError
    from research_agent.domain.field_config import DigestConfig
    profile, _ = curt
    assert profile.digest.exclude_content_types == ["repository_deposit", "dataset", "book_chapter", "erratum", "letter"]
    with _pt.raises(ValidationError):
        DigestConfig(exclude_content_types=["nonsense"])


def test_pmcid_found_at_records_when_we_learned_the_pmc_id(factory):
    from datetime import datetime, timedelta, timezone
    t0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
    with session_scope(factory) as s:
        repo = Repository(s)
        with_id = repo.merge_record(rec(sid="1", pmid="1", doi="10.1/a", pmcid="PMC1"), t0).paper
        without = repo.merge_record(rec(sid="2", pmid="2", doi="10.1/b", title="A second migraine paper with a different long title"), t0).paper
        assert with_id.pmcid_found_at == t0 and without.pmcid_found_at is None
        repo.merge_record(rec(source="europepmc", sid="MED:2", pmid="2", doi="10.1/b", pmcid="PMC2", title=without.title), t0 + timedelta(days=21))
        assert without.pmcid == "PMC2" and without.pmcid_found_at == t0 + timedelta(days=21)
        repo.merge_record(rec(source="pubmed", sid="2", pmid="2", doi="10.1/b", pmcid="PMC2", title=without.title), t0 + timedelta(days=40))
        assert without.pmcid_found_at == t0 + timedelta(days=21)   # never overwritten


def test_pmc_lag_report_buckets(factory, curt):
    from datetime import datetime, timedelta, timezone
    profile, field = curt
    now = datetime(2026, 11, 30, tzinfo=timezone.utc)
    with session_scope(factory) as s:
        repo = Repository(s)
        repo.sync_config(profile, field)

        def add(n, seen_days_ago, pmc_after_days=None):
            seen = now - timedelta(days=seen_days_ago)
            p = repo.merge_record(rec(sid=str(n), pmid=str(n), doi=f"10.1/p{n}", title=f"Migraine report number {n} with a long enough title here",
                                      pmcid=f"PMC{n}" if pmc_after_days == 0 else None), seen).paper
            if pmc_after_days:
                p.pmcid, p.pmcid_found_at = f"PMC{n}", seen + timedelta(days=pmc_after_days)
            p.first_seen_at = seen
            repo.upsert_match(profile.id, p.id, gate_status="passed", language_status="ok", track="A", subjects=[], entities=[])

        add(1, 60, 0); add(2, 60, 5); add(3, 60, 20); add(4, 60, 70); add(5, 3); add(6, 20); add(7, 100)
        z = repo.merge_record(rec(sid="9", pmid="9", doi="10.5281/zenodo.9", title="Deposited migraine slides long enough title here"), now).paper
        repo.upsert_match(profile.id, z.id, gate_status="passed", language_status="ok", track="A", subjects=[], entities=[])
        s.flush()
        r = repo.pmc_lag_report(profile.id, exclude_types=profile.digest.exclude_content_types, now=now)
    assert r["papers"] == 7 and r["with_pmcid"] == 4        # the repository deposit is excluded
    assert r["found_after"] == {"same day": 1, "1 to 7 days": 1, "8 to 30 days": 1, "31 to 90 days": 1}
    assert r["still_without_by_age"] == {"1 to 7 days": 1, "8 to 30 days": 1, "over 90 days": 1}


def test_errata_letters_and_replies_are_classified_and_research_is_not():
    from research_agent.domain import content_type as ct
    c = ct.classify
    assert c("article", ["Journal Article", "Published Erratum"], "Neurology", "10.1/x", "Magnitude and determinants of placebo response", "") == ct.ERRATUM
    assert c("article", ["openalex:erratum"], "Eur Arch", "10.1/x", "Correction: Flunarizine and cinnarizine in prophylaxis", "") == ct.ERRATUM
    assert c("article", [], "J", "10.1/x", "Erratum to: a migraine study", "") == ct.ERRATUM and c("article", [], "J", "10.1/x", "Corrigendum: a study", "") == ct.ERRATUM
    assert c("article", ["Letter", "openalex:letter"], "Neurology", "10.1/x", "Reader Response: CGRP inhibitors and cardiovascular risk", "") == ct.LETTER
    assert c("article", [], "Neurology", "10.1/x", "Editors' Note: CGRP inhibitors and cardiovascular risk", "") == ct.LETTER
    assert c("article", [], "J", "10.1/x", "Reply to: a disease-specific perspective on headache", "short") == ct.LETTER
    assert c("article", ["Comment"], "J", "10.1/x", "A perspective on triptans", "") == ct.LETTER
    # research must survive
    assert c("article", ["Letter"], "JAMA Neurol", "10.1/x", "Erenumab in adolescents: a research letter", "x" * 600) == ct.JOURNAL_ARTICLE   # a 'Letter' with a real abstract
    assert c("article", ["Journal Article"], "J", "10.1/x", "Response to erenumab in chronic migraine: a cohort study", "x" * 600) == ct.JOURNAL_ARTICLE
    assert c("article", ["Journal Article"], "J", "10.1/x", "Correction of medication overuse in migraine: a trial", "x" * 600) == ct.JOURNAL_ARTICLE
    assert c("article", [], "J", "10.1/x", "Correction to: a migraine study", "") == ct.ERRATUM
    assert ct.ERRATUM in ct.DEFAULT_DIGEST_EXCLUDED and ct.LETTER in ct.DEFAULT_DIGEST_EXCLUDED


def test_errata_and_letters_stay_out_of_the_digest_and_the_full_text_queue(factory, curt):
    profile, field = curt
    with session_scope(factory) as s:
        repo = Repository(s)
        repo.sync_config(profile, field)
        res = repo.merge_record(rec(sid="1", pmid="1", doi="10.1/r", title="Migraine prevention in adults with a long descriptive title here")).paper
        err = repo.merge_record(rec(sid="2", pmid="2", doi="10.1/e", title="Correction: Migraine prevention in adults with a long descriptive title", abstract="")).paper
        let = repo.merge_record(rec(sid="3", pmid="3", doi="10.1/l", title="Reader Response: Migraine prevention in adults with a long descriptive title", abstract="")).paper
        for p in (res, err, let):
            repo.upsert_match(profile.id, p.id, gate_status="passed", language_status="ok", track="A", subjects=[], entities=[])
        s.flush()
        assert (err.content_type, let.content_type, res.content_type) == ("erratum", "letter", "journal_article")
        ex = profile.digest.exclude_content_types
        assert [p.id for p, _ in repo.digest_candidates(profile.id, exclude_types=ex)] == [res.id]
        assert [p.id for p in repo.fulltext_candidates(profile.id, exclude_types=ex)] == [res.id]
