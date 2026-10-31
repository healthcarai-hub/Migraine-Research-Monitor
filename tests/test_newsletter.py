"""Weekly digital edition: selection by first-stored date, sections, never-twice, retractions, rendering and drafts."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from research_agent.application.newsletter import IssueExists, NewsletterService, cutoff_for
from research_agent.domain.field_config import CapsConfig, SignatureConfig
from research_agent.domain.newsletter import authors_short, format_pub, score, summarise
from research_agent.infrastructure.db import models as m
from research_agent.infrastructure.db.repository import Repository
from research_agent.infrastructure.db.session import session_scope
from research_agent.infrastructure.rendering import JinjaNewsletterRenderer
from conftest import rec

NOW = datetime(2026, 10, 30, 18, 0, tzinfo=timezone.utc)
WEEK_END = date(2026, 10, 30)


# ---------------------------------------------------------------- text rules
def test_summaries_prefer_the_conclusion_whatever_the_label_style():
    colon = "BACKGROUND: Migraine is common.\nRESULTS: Days fell by 2.3 (95% CI 1.1-3.4) in the treated group.\nCONCLUSIONS: X reduced days. Safe. More work needed."
    assert summarise(colon) == "X reduced days. Safe."
    bare = "Aim Rimegepant is effective. Methods We did X. Results Rates were 40%. Conclusion Rimegepant was consistent. Safety was good."
    assert summarise(bare) == "Rimegepant was consistent. Safety was good."
    assert summarise("Abstract Objectives/Background The aim was to test SPG block. Conclusions SPG block is promising.") == "SPG block is promising."
    assert summarise("Chronic migraine is common. Plasma was analysed. Results were significant.") == "Chronic migraine is common. Plasma was analysed."
    assert summarise("") == "" and summarise("   ") == ""


def test_summary_length_sentences_and_abbreviations():
    assert summarise("We compared X vs. placebo in adults (e.g. aged 18-65) with migraine. Second sentence here. Third.", max_sentences=1).endswith("with migraine.")
    long = "Word " * 200 + "end."
    out = summarise(long, max_chars=100)
    assert len(out) <= 101 and out.endswith("…")


def test_authors_dates_and_score():
    a = [{"family": "Lipton", "initials": "RB"}, {"family": "Goadsby", "initials": "PJ"}, {"family": "Ashina", "given": "Messoud"}, {"family": "Dodick", "initials": "D"}]
    assert authors_short(a) == "Lipton RB, Goadsby PJ, M Ashina, et al." and authors_short(a[:2]) == "Lipton RB, Goadsby PJ" and authors_short([]) == ""
    assert format_pub("2026-10-05") == "5 Oct 2026" and format_pub("2026 Oct") == "2026 Oct" and format_pub(None) == ""
    kw = dict(watched_journal=False, watched_researcher=False, specific_subjects=0, has_abstract=False, printable_full_text=False, core_journal=False)
    assert score(**kw) == 0
    assert score(**{**kw, "watched_journal": True, "specific_subjects": 9}) == 6   # topic breadth is capped at 3


# ---------------------------------------------------------------- building an edition
@pytest.fixture()
def seeded(factory, curt):
    profile, field = curt
    with session_scope(factory) as s:
        Repository(s).sync_config(profile, field)
    return profile, field


def add(factory, profile, n, *, days_ago=1, now=NOW, gate="passed", lang="ok", track="A", subjects=(), entities=(), exploratory=False, **kw):
    """One stored paper plus its match for the profile, first stored `days_ago` days before NOW."""
    with session_scope(factory) as s:
        repo = Repository(s)
        seen = now - timedelta(days=days_ago)
        base = dict(sid=str(n), pmid=str(n), doi=f"10.1/p{n}", title=f"Migraine study number {n} with a sufficiently long title", abstract="Conclusions: It worked well. Truly.")
        base.update(kw)
        p = repo.merge_record(rec(**base), seen).paper
        p.first_seen_at = seen
        repo.upsert_match(profile.id, p.id, gate_status=gate, language_status=lang, track=track, exploratory=exploratory,
                          subjects=[{"code": c, "name": nme, "track": t, "terms": [], "specific": True} for c, nme, t in subjects], entities=list(entities))
        s.flush()
        return p.id


def svc(factory, now=NOW, enrichers=None):
    return NewsletterService(factory, JinjaNewsletterRenderer(), enrichers or {}, now=lambda: now)


def item_ids(factory, role=None):
    with session_scope(factory) as s:
        q = select(m.IssueItem.paper_id, m.IssueItem.section, m.IssueItem.role).order_by(m.IssueItem.position)
        return [(r[0], r[1], r[2]) for r in s.execute(q)]


def test_selection_is_by_the_date_we_stored_the_paper(factory, seeded):
    profile, field = seeded
    inside, edge_old, future = add(factory, profile, 1, days_ago=3), add(factory, profile, 2, days_ago=9), add(factory, profile, 3, days_ago=-1)
    r = svc(factory).build(profile, field, WEEK_END)
    assert [i[0] for i in item_ids(factory)] == [inside] and r.counts == {"direct": 1} and r.status == "built"
    assert edge_old not in [i[0] for i in item_ids(factory)] and future not in [i[0] for i in item_ids(factory)]


def test_publication_date_is_shown_to_readers_and_the_window_is_still_our_stored_date(factory, seeded):
    profile, field = seeded
    pid = add(factory, profile, 1, days_ago=2, published_date=date(2026, 10, 25))   # published 3 days before we stored it: fresh enough
    r = svc(factory).build(profile, field, WEEK_END)
    assert r.total == 1
    with session_scope(factory) as s:
        issue = s.scalar(select(m.Issue))
        assert "25 Oct 2026" in issue.html and "Published 25 Oct 2026" in issue.text


def test_what_does_not_qualify_stays_out(factory, seeded):
    profile, field = seeded
    ok = add(factory, profile, 1)
    add(factory, profile, 2, gate="failed"); add(factory, profile, 3, gate="unknown"); add(factory, profile, 4, lang="other")
    add(factory, profile, 5, doi="10.5281/zenodo.5")                                  # repository deposit
    add(factory, profile, 6, pub_types=["crossref:book-chapter"], doi="10.1/ch")      # book chapter
    add(factory, profile, 7, retraction_notice=True)                                  # a notice is attached to its original, never listed
    assert [i[0] for i in item_ids(factory)] == [] and svc(factory).build(profile, field, WEEK_END).total == 1
    assert [i[0] for i in item_ids(factory)] == [ok]


def test_sections_and_subject_groups(factory, seeded):
    profile, field = seeded
    direct = add(factory, profile, 1, subjects=[("4", "Clinical therapeutics", "A")])
    mech = add(factory, profile, 2, track="B", subjects=[("7", "Redox & inflammation", "B")])
    expl = add(factory, profile, 3, exploratory=True)
    pre = add(factory, profile, 4, source="europepmc", sid="PPR:4", is_preprint=True, pmid=None, doi="10.1101/4")
    trial = add(factory, profile, 5, source="ctgov", sid="NCT5", nct_id="NCT5", doi=None, pmid=None, is_trial=True, url="https://clinicaltrials.gov/study/NCT5")
    r = svc(factory).build(profile, field, WEEK_END)
    assert r.counts == {"direct": 1, "mechanisms": 2, "preprints": 1, "trials": 1}
    sec = {pid: s for pid, s, _ in item_ids(factory)}
    assert sec == {direct: "direct", mech: "mechanisms", expl: "mechanisms", pre: "preprints", trial: "trials"}
    with session_scope(factory) as s:
        html = s.scalar(select(m.Issue.html))
    assert "Clinical therapeutics" in html and "Redox &amp; inflammation" in html and "Preprint: not peer reviewed" in html and "https://clinicaltrials.gov/study/NCT5" in html


def test_watched_journal_uses_one_spelling_and_is_labelled(factory, seeded):
    profile, field = seeded
    add(factory, profile, 1, journal="Headache The Journal of Head and Face Pain", entities=[{"type": "journal", "key": "headache", "track": "A", "via": "issn", "confidence": "id"}])
    add(factory, profile, 2, entities=[{"type": "researcher", "key": "peter_j_goadsby", "track": "A", "via": "x", "confidence": "name"},
                                       {"type": "researcher", "key": "jan_hoffmann", "track": "AB", "via": "x", "confidence": "name_ambiguous"}])
    svc(factory).build(profile, field, WEEK_END)
    with session_scope(factory) as s:
        text = s.scalar(select(m.Issue.text))
    assert "Headache: The Journal of Head and Face Pain" in text and "Watched journal: Headache: The Journal of Head and Face Pain" in text
    assert "Watched researcher: Peter J. Goadsby" in text and "Jan Hoffmann" not in text      # ambiguous name matches are never advertised


def test_ordering_puts_watched_sources_first(factory, seeded):
    profile, field = seeded
    plain = add(factory, profile, 1)
    watched = add(factory, profile, 2, entities=[{"type": "journal", "key": "cephalalgia", "track": "A", "via": "issn", "confidence": "id"}])
    svc(factory).build(profile, field, WEEK_END)
    assert [i[0] for i in item_ids(factory)] == [watched, plain]


def test_section_cap_drops_the_lowest_scores_and_they_roll_over(factory, seeded):
    profile, field = seeded
    capped = profile.model_copy(update={"newsletter": profile.newsletter.model_copy(update={"caps": CapsConfig(per_section={"direct": 1}, max_html_kb=None)})})
    low = add(factory, profile, 1, days_ago=3)
    high = add(factory, profile, 2, days_ago=2, entities=[{"type": "journal", "key": "cephalalgia", "track": "A", "via": "issn", "confidence": "id"}])
    svc(factory).build(capped, field, WEEK_END)
    assert [i[0] for i in item_ids(factory)] == [high]
    later = NOW + timedelta(days=7)
    svc(factory, later).build(capped, field, date(2026, 11, 6))
    assert low in [i[0] for i in item_ids(factory)]          # not lost: carried by the next edition


def test_a_paper_is_never_carried_twice_and_the_database_agrees(factory, seeded):
    profile, field = seeded
    pid = add(factory, profile, 1, days_ago=2)
    svc(factory).build(profile, field, WEEK_END)
    svc(factory, NOW + timedelta(days=7)).build(profile, field, date(2026, 11, 6))
    assert [i[0] for i in item_ids(factory)] == [pid]        # the second edition found nothing new
    with session_scope(factory) as s:
        issue = s.scalar(select(m.Issue).where(m.Issue.issue_key == "curt-2026-11-06"))
        s.add(m.IssueItem(issue_id=issue.id, profile_id=profile.id, paper_id=pid, role="digest", section="direct"))
        with pytest.raises(IntegrityError):
            s.flush()
        s.rollback()


def test_building_twice_is_idempotent_and_rebuild_replaces_only_unpublished(factory, seeded):
    profile, field = seeded
    add(factory, profile, 1)
    first = svc(factory).build(profile, field, WEEK_END)
    add(factory, profile, 2)
    again = svc(factory).build(profile, field, WEEK_END)
    assert first.created and not again.created and again.total == 1 and "unchanged" in again.notes[0]
    rebuilt = svc(factory).build(profile, field, WEEK_END, rebuild=True)
    assert rebuilt.created and rebuilt.total == 2
    with session_scope(factory) as s:
        assert s.scalar(select(func.count()).select_from(m.Issue)) == 1 and s.scalar(select(func.count()).select_from(m.IssueItem)) == 2
    svc(factory).mark_published(profile.id, rebuilt.issue_key, "draft-1")
    with pytest.raises(IssueExists):
        svc(factory).build(profile, field, WEEK_END, rebuild=True)


def test_first_edition_uses_the_normal_window_then_catches_up_after_a_missed_week(factory, seeded):
    profile, field = seeded
    old = add(factory, profile, 1, days_ago=20)                  # older than the normal 7 days
    recent = add(factory, profile, 2, days_ago=2)
    svc(factory).build(profile, field, WEEK_END)
    assert [i[0] for i in item_ids(factory)] == [recent]
    missed = add(factory, profile, 3, days_ago=-3)               # stored after the first edition, then a week is skipped
    later = NOW + timedelta(days=14)
    svc(factory, later).build(profile, field, date(2026, 11, 13))
    ids = [i[0] for i in item_ids(factory)]
    assert missed in ids and old not in ids                      # catch-up reaches back 28 days from the new cutoff but never double-sends


def test_empty_edition(factory, seeded):
    profile, field = seeded
    r = svc(factory).build(profile, field, WEEK_END)
    assert r.total == 0
    with session_scope(factory) as s:
        issue = s.scalar(select(m.Issue))
        assert issue.stats["empty"] is True and "No new papers matched this week." in issue.text and "No new papers matched this week." in issue.html


def test_past_week_ending_uses_the_end_of_that_day():
    assert cutoff_for(date(2026, 10, 20), NOW) == datetime(2026, 10, 20, 23, 59, 59, tzinfo=timezone.utc)
    assert cutoff_for(date(2026, 10, 30), NOW) == NOW


# ---------------------------------------------------------------- retractions
def test_new_retracted_paper_goes_only_in_the_retraction_section(factory, seeded):
    profile, field = seeded
    bad = add(factory, profile, 1, retracted=True, source="pubmed")
    r = svc(factory).build(profile, field, WEEK_END)
    assert r.counts == {"retractions": 1} and item_ids(factory) == [(bad, "retractions", "retraction")]
    with session_scope(factory) as s:
        html = s.scalar(select(m.Issue.html))
    assert "RETRACTED" in html and "Retractions and withdrawals" in html


def test_a_sent_paper_that_is_retracted_later_is_announced_once(factory, seeded):
    profile, field = seeded
    pid = add(factory, profile, 1, days_ago=2)
    first = svc(factory).build(profile, field, WEEK_END)
    with session_scope(factory) as s:
        p = s.get(m.Paper, pid)
        p.is_retracted = True
        notice = Repository(s).merge_record(rec(sid="n1", pmid="900", doi="10.1/notice", title="Retraction notice to a migraine study that was retracted"), NOW).paper
        notice.is_retraction_notice, notice.retraction_target_id = True, pid
    later = NOW + timedelta(days=7)
    second = svc(factory, later).build(profile, field, date(2026, 11, 6))
    assert second.counts == {"retractions": 1}
    assert (pid, "retractions", "retraction") in item_ids(factory) and (pid, "direct", "digest") in item_ids(factory)
    with session_scope(factory) as s:
        issue = s.scalar(select(m.Issue).where(m.Issue.issue_key == "curt-2026-11-06"))
        assert f"edition {first.issue_key}" in issue.text and "https://doi.org/10.1/notice" in issue.text and "We sent this paper earlier" in issue.html
    third = svc(factory, later + timedelta(days=7)).build(profile, field, date(2026, 11, 13))
    assert third.total == 0                                                          # announced once, not again


def test_notice_for_a_paper_we_never_stored_is_not_listed(factory, seeded):
    profile, field = seeded
    add(factory, profile, 1, retraction_notice=True, retraction_target_doi="10.1/never")
    assert svc(factory).build(profile, field, WEEK_END).total == 0


def test_weekly_recheck_flags_recently_sent_papers(factory, seeded):
    class Enricher:
        name = "crossref"
        calls = []

        def lookup(self, dois):
            Enricher.calls.append(list(dois))
            return [rec("crossref", d, doi=d, retracted=True) for d in dois], None

    profile, field = seeded
    pid = add(factory, profile, 1, days_ago=2)
    svc(factory).build(profile, field, WEEK_END)
    later = NOW + timedelta(days=7)
    r = svc(factory, later, {"crossref": Enricher()}).build(profile, field, date(2026, 11, 6))
    assert Enricher.calls == [["10.1/p1"]] and any("flagged 1 previously sent" in n for n in r.notes)
    assert r.counts == {"retractions": 1}
    with session_scope(factory) as s:
        assert s.get(m.Paper, pid).is_retracted and s.scalar(select(m.RetractionCheck.result).where(m.RetractionCheck.paper_id == pid)) == "retracted"


def test_recheck_can_be_skipped_and_failures_are_noted(factory, seeded):
    class Failing:
        def lookup(self, dois):
            return [], "HTTP 500"

    profile, field = seeded
    add(factory, profile, 1, days_ago=2)
    svc(factory).build(profile, field, WEEK_END)
    later = NOW + timedelta(days=7)
    r = svc(factory, later, {"crossref": Failing()}).build(profile, field, date(2026, 11, 6))
    assert any("failed" in n for n in r.notes)
    r2 = svc(factory, later, {"crossref": Failing()}).build(profile, field, date(2026, 11, 6), rebuild=True, recheck=False)
    assert r2.notes == []


# ---------------------------------------------------------------- lifecycle, rendering, publishers
def test_lifecycle_built_published_sent(factory, seeded):
    profile, field = seeded
    add(factory, profile, 1)
    s_ = svc(factory)
    r = s_.build(profile, field, WEEK_END)
    with pytest.raises(ValueError):
        s_.mark_sent(profile.id, r.issue_key)             # cannot be sent before a draft exists
    s_.mark_published(profile.id, r.issue_key, "d1")
    s_.mark_sent(profile.id, r.issue_key)
    with session_scope(factory) as s:
        i = s.scalar(select(m.Issue))
        assert (i.status, i.draft_ref) == ("sent", "d1") and i.published_at and i.sent_at


def test_html_is_escaped_and_text_is_not(factory, seeded):
    profile, field = seeded
    add(factory, profile, 1, title="Migraine & the <b>brain</b>: a study of CGRP in adults", abstract="Conclusions: A < B & C.")
    svc(factory).build(profile, field, WEEK_END)
    with session_scope(factory) as s:
        i = s.scalar(select(m.Issue))
    assert "Migraine &amp; the &lt;b&gt;brain&lt;/b&gt;" in i.html and "<b>brain</b>" not in i.html
    assert "Migraine & the <b>brain</b>" in i.text and "&amp;" not in i.text
    assert i.html.startswith("<!doctype html>") and 'lang="en"' in i.html


def test_file_publisher_writes_html_text_and_eml(tmp_path):
    from research_agent.infrastructure.publishers import FileDraftPublisher
    ref = FileDraftPublisher(tmp_path).publish("Subject é", "<p>hi</p>", "hi", "curt-2026-10-30", ["a@x.org"])
    assert (tmp_path / "curt-2026-10-30" / "newsletter.html").read_text() == "<p>hi</p>" and ref.endswith("curt-2026-10-30")
    eml = (tmp_path / "curt-2026-10-30" / "newsletter.eml").read_bytes()
    assert b"X-Research-Agent-Issue-Key: curt-2026-10-30" in eml and b"a@x.org" in eml


class FakeGmail:
    """Just enough of the Gmail API surface the publisher uses."""

    def __init__(self):
        self.store, self.created = {}, 0

    def users(self):
        return self

    def drafts(self):
        outer = self

        class D:
            def list(self, **kw):
                return type("R", (), {"execute": lambda s: {"drafts": [{"id": i} for i in outer.store]}})()

            def get(self, userId, id, format=None):
                def run(s):
                    if id not in outer.store:
                        raise KeyError(id)
                    return {"message": {"payload": {"headers": [{"name": "X-Research-Agent-Issue-Key", "value": outer.store[id]}]}}}
                return type("R", (), {"execute": run})()

            def create(self, userId, body):
                import base64, email
                raw = base64.urlsafe_b64decode(body["message"]["raw"])
                key = email.message_from_bytes(raw)["X-Research-Agent-Issue-Key"]
                outer.created += 1
                outer.store[f"d{outer.created}"] = key
                return type("R", (), {"execute": lambda s: {"id": f"d{outer.created}"}})()
        return D()


def test_gmail_publisher_is_idempotent_and_only_creates_drafts():
    from research_agent.infrastructure.publishers import GmailDraftPublisher, GMAIL_COMPOSE_SCOPE
    fake = FakeGmail()
    pub = GmailDraftPublisher(fake)
    a = pub.publish("S", "<p>x</p>", "x", "curt-1", [])
    assert pub.publish("S", "<p>x</p>", "x", "curt-1", [], existing_ref=a) == a            # known draft: reused
    assert pub.publish("S", "<p>x</p>", "x", "curt-1", [], existing_ref="gone") == a          # lost reference: found again by the issue-key header
    b = pub.publish("S", "<p>x</p>", "x", "curt-2", [])
    assert b != a and fake.created == 2
    assert not hasattr(pub, "send") and GMAIL_COMPOSE_SCOPE.endswith("gmail.compose")          # compose scope cannot read or send the mailbox


def test_gmail_service_needs_credentials():
    from research_agent.infrastructure.publishers import build_gmail_service
    with pytest.raises(RuntimeError, match="GOOGLE_CLIENT_ID"):
        build_gmail_service(None, None, None)


def test_cli_newsletter_round_trip(tmp_path, monkeypatch, capsys):
    from research_agent import cli
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'n.sqlite'}")
    monkeypatch.setenv("OUTPUTS_DIR", str(tmp_path / "out"))
    cli.main(["db", "upgrade"]); cli.main(["seed"])
    assert cli.main(["newsletter", "build", "--week-ending", "2026-10-30"]) == 0
    out = capsys.readouterr().out
    assert "curt-2026-10-30 [built]" in out and "warning: this edition is empty" in out
    assert cli.main(["newsletter", "list"]) == 0 and "curt-2026-10-30" in capsys.readouterr().out
    assert cli.main(["newsletter", "show", "curt-2026-10-30"]) == 0 and "No new papers" in capsys.readouterr().out
    assert cli.main(["newsletter", "publish", "curt-2026-10-30", "--to", "file"]) == 0 and (tmp_path / "out" / "curt-2026-10-30" / "newsletter.html").exists()
    assert cli.main(["newsletter", "build", "--week-ending", "2026-10-30", "--rebuild"]) == 1       # published issues are never rebuilt
    assert cli.main(["newsletter", "mark-sent", "curt-2026-10-30"]) == 0
    assert cli.main(["newsletter", "show", "nope"]) == 1


def test_blank_journal_leaves_no_stray_separator(factory, seeded):
    profile, field = seeded
    add(factory, profile, 1, journal=None, authors=[], published_date=None)
    svc(factory).build(profile, field, WEEK_END)
    with session_scope(factory) as s:
        i = s.scalar(select(m.Issue))
    assert "\n    |" not in i.text and "   | " not in i.text
    assert "&middot; &middot;" not in i.html and 'color:#5b6678;"></div>' not in i.html          # no empty line and no stray separator where journal, date and authors are all missing


def test_large_editions_carry_a_gmail_clipping_warning(factory, seeded):
    profile, field = seeded
    profile = profile.model_copy(update={"newsletter": profile.newsletter.model_copy(update={"caps": CapsConfig(per_section={}, max_html_kb=None)})})   # no caps: the warning is the only safeguard
    for n in range(1, 260):
        add(factory, profile, n, abstract="Conclusions: " + "A fairly long concluding sentence about migraine outcomes. " * 6)
    r = svc(factory).build(profile, field, WEEK_END)
    assert any("Gmail clips" in n for n in r.notes)
    with session_scope(factory) as s:
        assert s.scalar(select(m.Issue.stats))["html_bytes"] > 100_000


# ---------------------------------------------------------------- caps (tunable settings) and the size safety net
def view(pid, section="direct", sc=0):
    from research_agent.domain.newsletter import ItemView
    return ItemView(paper_id=pid, title=f"t{pid}", journal="", published="", authors="", url="u", doi=None, summary="s", section=section, group="", kind="article", track="A", score=sc)


def test_apply_caps_keeps_the_best_and_reports_why_others_were_held():
    from research_agent.domain.newsletter import apply_caps
    items = [view(1, sc=1), view(2, sc=5), view(3, sc=3), view(4, "preprints", 0), view(5, "retractions", 0), view(6, "retractions", 0)]
    kept, held = apply_caps(items, {"direct": 2, "retractions": None}, None)
    assert sorted(i.paper_id for i in kept) == [2, 3, 4, 5, 6] and held == [(items[0], "section cap (direct)")]
    kept, held = apply_caps(items, {}, 3)                      # overall cap of 3 on top of the two retractions that are always kept
    assert {i.paper_id for i in kept if i.section != "retractions"} == {2} and {i.paper_id for i in kept if i.section == "retractions"} == {5, 6}
    assert {i.paper_id for i, why in held} == {1, 3, 4} and {why for _, why in held} == {"overall cap"}
    kept, held = apply_caps(items, {"retractions": 1}, None)   # a retraction cap only applies when someone sets one
    assert len([i for i in kept if i.section == "retractions"]) == 1


def test_caps_config_defaults_and_validation(curt):
    from pydantic import ValidationError
    profile, _ = curt
    assert profile.newsletter.caps.per_section == {"direct": 30, "mechanisms": 15, "preprints": 10, "trials": 10} and profile.newsletter.caps.max_html_kb == 95
    assert CapsConfig().per_section["direct"] == 30 and CapsConfig(per_section={"direct": None}).per_section == {"direct": None}
    for bad in ({"per_section": {"nonsense": 5}}, {"per_section": {"direct": 0}}, {"total": 0}, {"max_html_kb": -1}):
        with pytest.raises(ValidationError):
            CapsConfig(**bad)


def test_the_default_caps_hold_an_edition_to_the_section_limits_and_the_rest_roll_over(factory, seeded):
    profile, field = seeded
    for n in range(1, 41):
        add(factory, profile, n, days_ago=2, entities=[{"type": "journal", "key": "cephalalgia", "track": "A", "via": "issn", "confidence": "id"}] if n <= 5 else [])
    r = svc(factory).build(profile, field, WEEK_END)
    assert r.counts == {"direct": 30}
    with session_scope(factory) as s:
        issue = s.scalar(select(m.Issue))
        assert issue.stats["held_back"] == {"section cap (direct)": 10}
        assert "10 further matching papers were held back" in issue.html and "10 further matching papers were held back" in issue.text
    sent = {i[0] for i in item_ids(factory)}
    assert set(range(1, 6)) <= {pid for pid in sent}                       # watched-journal papers outrank the rest
    r2 = svc(factory, NOW + timedelta(days=7)).build(profile, field, date(2026, 11, 6))
    with session_scope(factory) as s:
        second = {pid for (pid,) in s.execute(select(m.IssueItem.paper_id).join(m.Issue, m.Issue.id == m.IssueItem.issue_id).where(m.Issue.issue_key == "curt-2026-11-06"))}
    assert r2.counts == {"direct": 10} and not (sent & second) and len(sent | second) == 40     # the held-back ten come next, none twice


def test_the_size_limit_trims_the_lowest_ranked_papers_but_never_retractions(factory, seeded):
    profile, field = seeded
    tight = profile.model_copy(update={"newsletter": profile.newsletter.model_copy(update={"caps": CapsConfig(per_section={}, max_html_kb=40)})})
    best = add(factory, profile, 1, entities=[{"type": "journal", "key": "cephalalgia", "track": "A", "via": "issn", "confidence": "id"}])
    for n in range(2, 121):
        add(factory, profile, n, abstract="Conclusions: " + "A fairly long concluding sentence about migraine outcomes. " * 4)
    bad = add(factory, profile, 200, retracted=True)
    r = svc(factory).build(tight, field, WEEK_END)
    with session_scope(factory) as s:
        issue = s.scalar(select(m.Issue))
        assert len(issue.html.encode()) <= 40 * 1024 and issue.stats["html_bytes"] <= 40 * 1024
        assert issue.stats["held_back"]["size limit"] == 120 - r.counts["direct"]
    ids = {i[0] for i in item_ids(factory)}
    assert best in ids and bad in ids and r.counts["retractions"] == 1 and 0 < r.counts["direct"] < 120
    assert any("held back" in n and "size limit" in n for n in r.notes)


def test_no_caps_means_everything_is_included(factory, seeded):
    profile, field = seeded
    open_ = profile.model_copy(update={"newsletter": profile.newsletter.model_copy(update={"caps": CapsConfig(per_section={}, max_html_kb=None)})})
    for n in range(1, 51):
        add(factory, profile, n)
    assert svc(factory).build(open_, field, WEEK_END).counts == {"direct": 50}


def test_a_paper_about_the_field_outranks_one_that_only_mentions_it(factory, seeded):
    profile, field = seeded
    mentions = add(factory, profile, 1, title="Epilepsy and infection risk in adults taking anti-seizure medicines for a long period", abstract="A comparison group has migraine.")
    about = add(factory, profile, 2, title="Migraine prevention with a new agent in adults over twelve weeks of treatment")
    svc(factory).build(profile, field, WEEK_END)
    assert [i[0] for i in item_ids(factory)] == [about, mentions]
    assert score(watched_journal=False, watched_researcher=False, specific_subjects=0, has_abstract=False, printable_full_text=False, core_journal=False, title_match=True) == 2


def test_trials_are_registered_not_published_and_the_footer_reads_properly(factory, seeded):
    profile, field = seeded
    add(factory, profile, 1, source="ctgov", sid="NCT1", nct_id="NCT1", doi=None, pmid=None, is_trial=True, published_date=date(2026, 10, 27),
        title="A migraine trial registered with a title long enough to count", url="https://clinicaltrials.gov/study/NCT1")
    add(factory, profile, 2, published_date=date(2026, 10, 26))
    svc(factory).build(profile, field, WEEK_END)
    with session_scope(factory) as s:
        issue = s.scalar(select(m.Issue))
    assert "Registered 27 Oct 2026" in issue.text and "Published 26 Oct 2026" in issue.text and "Registered 27 Oct 2026" in issue.html
    assert " between " not in issue.text.split("Selection:")[1] and "from " in issue.text.split("Selection:")[1]


# ---------------------------------------------------------------- freshness: papers published long before we found them are left out (owner, 2026-10-31)
def test_is_stale_rules():
    from research_agent.domain.newsletter import is_stale, latest_possible_publication
    seen = date(2026, 10, 30)
    assert not is_stale(date(2026, 10, 23), None, seen, 7)       # exactly 7 days: kept
    assert is_stale(date(2026, 10, 22), None, seen, 7)           # 8 days: left out
    assert not is_stale(date(2026, 11, 1), None, seen, 7)        # future-dated (print issues): kept
    assert not is_stale(date(2020, 1, 1), None, seen, None)      # no limit configured
    assert not is_stale(None, None, seen, 7) and not is_stale(None, "2026", seen, 7) and not is_stale(None, "garbage", seen, 7)   # unknown: never stale
    assert not is_stale(None, "2026 Oct", seen, 7) and not is_stale(None, "2026-10", seen, 7)      # month-only counts as its last day
    assert is_stale(None, "2026 Jul", seen, 7) and is_stale(None, "2026-09", seen, 7)              # certainly older than 7 days
    assert latest_possible_publication(None, "2026 Dec") == date(2026, 12, 31) and latest_possible_publication(None, "2026 Feb") == date(2026, 2, 28)
    assert is_stale(date(2026, 10, 23), None, seen, 6) and not is_stale(date(2026, 10, 30), None, seen, 0)


def test_late_indexed_papers_are_left_out_but_kept_in_the_database(factory, seeded):
    profile, field = seeded
    fresh = add(factory, profile, 1, days_ago=2, published_date=date(2026, 10, 26))
    old = add(factory, profile, 2, days_ago=2, published_date=date(2026, 7, 29))            # published in July, only stored this week
    month = add(factory, profile, 3, days_ago=2, published_date=None, extra={"published_label": "2026 Oct"})
    r = svc(factory).build(profile, field, WEEK_END)
    ids = {i[0] for i in item_ids(factory)}
    assert fresh in ids and month in ids and old not in ids
    assert any("1 paper(s) published more than 7 days before we first found them" in n for n in r.notes)
    with session_scope(factory) as s:
        assert s.scalar(select(m.Issue.stats))["left_out_as_stale"] == 1 and s.get(m.Paper, old) is not None
        assert s.scalar(select(func.count()).select_from(m.IssueItem).where(m.IssueItem.paper_id == old)) == 0     # not marked as sent


def test_freshness_is_measured_from_when_we_stored_the_paper_not_from_when_the_edition_is_built(factory, seeded):
    profile, field = seeded
    capped = profile.model_copy(update={"newsletter": profile.newsletter.model_copy(update={"caps": CapsConfig(per_section={"direct": 1}, max_html_kb=None)})})
    a = add(factory, profile, 1, days_ago=3, published_date=date(2026, 10, 24), entities=[{"type": "journal", "key": "cephalalgia", "track": "A", "via": "issn", "confidence": "id"}])
    b = add(factory, profile, 2, days_ago=3, published_date=date(2026, 10, 24))
    svc(factory).build(capped, field, WEEK_END)
    assert [i[0] for i in item_ids(factory)] == [a]
    r = svc(factory, NOW + timedelta(days=7)).build(capped, field, date(2026, 11, 6))   # a week later, the held-back paper is 13 days past publication
    assert [i[0] for i in item_ids(factory)][-1] == b and r.counts == {"direct": 1}      # still eligible: it was fresh when we stored it


def test_a_retraction_alert_about_an_old_paper_is_never_left_out_as_stale(factory, seeded):
    profile, field = seeded
    bad = add(factory, profile, 1, retracted=True, published_date=date(2024, 5, 1))
    assert svc(factory).build(profile, field, WEEK_END).counts == {"retractions": 1} and item_ids(factory)[0][0] == bad


def test_freshness_can_be_switched_off_and_is_validated(factory, seeded):
    from pydantic import ValidationError
    from research_agent.domain.field_config import FreshnessConfig
    profile, field = seeded
    assert profile.newsletter.freshness.max_publication_age_days == 7
    off = profile.model_copy(update={"newsletter": profile.newsletter.model_copy(update={"freshness": FreshnessConfig(max_publication_age_days=None)})})
    add(factory, profile, 1, days_ago=2, published_date=date(2026, 7, 29))
    assert svc(factory).build(off, field, WEEK_END).total == 1
    with pytest.raises(ValidationError):
        FreshnessConfig(max_publication_age_days=-1)


def test_stale_papers_cost_no_model_calls(factory, seeded):
    from test_summaries import FakeLlm, nl
    profile, field = seeded
    fresh = add(factory, profile, 1, days_ago=2, published_date=date(2026, 10, 27))
    add(factory, profile, 2, days_ago=2, published_date=date(2026, 7, 1))
    llm = FakeLlm()
    nl(factory, llm).build(profile, field, WEEK_END)
    assert llm.calls == [fresh]


# ---------------------------------------------------------------- printable full text ranks higher (owner, 2026-10-31); weights are settings
def add_fulltext(factory, pid, *, rights="entitled", licence_key=None, printable=True):
    with session_scope(factory) as s:
        s.add(m.PaperFulltext(paper_id=pid, status="full_text", provider="p", rights=rights, licence_key=licence_key, print_allowed=printable, words=5000, text="x " * 5000))


def test_papers_with_printable_full_text_rank_higher_whatever_the_licence(factory, seeded):
    profile, field = seeded
    plain = add(factory, profile, 1)
    paid = add(factory, profile, 2)                       # text obtained through a paid entitlement
    nc = add(factory, profile, 3)                         # a restrictive open licence
    add_fulltext(factory, paid)
    add_fulltext(factory, nc, rights="open", licence_key="cc-by-nc-nd")
    svc(factory).build(profile, field, WEEK_END)
    order = [i[0] for i in item_ids(factory)]
    assert order[-1] == plain and set(order[:2]) == {paid, nc}


def test_text_the_policy_does_not_allow_in_print_earns_no_bonus(factory, seeded):
    profile, field = seeded
    blocked, plain = add(factory, profile, 1), add(factory, profile, 2)
    add_fulltext(factory, blocked, printable=False)
    svc(factory).build(profile, field, WEEK_END)
    with session_scope(factory) as s:
        snap = {i.paper_id: i.snapshot["score"] for i in s.scalars(select(m.IssueItem))}
    assert snap[blocked] == snap[plain]


def test_ranking_weights_are_settings_and_validated(curt):
    from pydantic import ValidationError
    from research_agent.domain.field_config import RankingConfig
    from research_agent.domain.newsletter import DEFAULT_WEIGHTS
    profile, _ = curt
    assert profile.newsletter.ranking.weights == DEFAULT_WEIGHTS and DEFAULT_WEIGHTS["printable_full_text"] == 3
    assert RankingConfig(weights={"printable_full_text": 6}).weights["watched_journal"] == 3        # unspecified signals keep their defaults
    for bad in ({"nonsense": 1}, {"title_match": -1}):
        with pytest.raises(ValidationError):
            RankingConfig(weights=bad)
    kw = dict(watched_journal=False, watched_researcher=False, specific_subjects=0, has_abstract=False, printable_full_text=True, core_journal=False)
    assert score(**kw) == 3 and score(**kw, weights={"printable_full_text": 8}) == 8 and score(**kw, weights={"printable_full_text": 0}) == 0


def test_tidy_title_drops_the_added_full_stop_only():
    from research_agent.domain.newsletter import tidy_title
    assert tidy_title("Effectiveness of rimegepant across multiple attacks: a real-world study.") == "Effectiveness of rimegepant across multiple attacks: a real-world study"
    assert tidy_title("Migraine in the U.S.") == "Migraine in the U.S." and tidy_title("Triptans vs.") == "Triptans vs."
    assert tidy_title("What happens next...") == "What happens next..." and tidy_title("Short title.") == "Short title."
    assert tidy_title("A title without one") == "A title without one" and tidy_title("") == ""


# ---------------------------------------------------------------- watched sources are chosen first when a cap bites (owner, 2026-10-31)
def test_watched_sources_are_chosen_before_higher_scoring_papers():
    from research_agent.domain.newsletter import apply_caps
    watched = view(1, sc=3); watched.watched = ["Watched journal: Neurology"]
    items = [watched, view(2, sc=9), view(3, sc=8)]
    kept, held = apply_caps(items, {"direct": 2}, None)
    assert {i.paper_id for i in kept} == {1, 2} and [i.paper_id for i, _ in held] == [3]       # the low-scoring watched paper beats the 8-point one
    kept, held = apply_caps(items, {"direct": 2}, None, watched_first=False)
    assert {i.paper_id for i in kept} == {2, 3}                                                  # the old behaviour is one switch away


def test_the_overall_cap_also_prefers_watched_papers():
    from research_agent.domain.newsletter import apply_caps
    watched = view(1, "mechanisms", 1); watched.watched = ["Watched researcher: X"]
    kept, held = apply_caps([watched, view(2, "direct", 9), view(3, "preprints", 8)], {}, 1)
    assert [i.paper_id for i in kept] == [1]


def test_a_closed_access_paper_from_a_watched_journal_is_not_dropped_for_open_access_ones(factory, seeded):
    profile, field = seeded
    small = profile.model_copy(update={"newsletter": profile.newsletter.model_copy(update={"caps": CapsConfig(per_section={"direct": 2}, max_html_kb=None)})})
    j = {"type": "journal", "key": "neurology", "track": "A", "via": "issn", "confidence": "id"}
    closed_watched = add(factory, profile, 1, entities=[j])                                       # watched journal, no full text
    open_a, open_b = add(factory, profile, 2), add(factory, profile, 3)
    for pid in (open_a, open_b):
        add_fulltext(factory, pid, rights="open", licence_key="cc-by")                           # full text in hand: +3 each
    svc(factory).build(small, field, WEEK_END)
    assert closed_watched in {i[0] for i in item_ids(factory)}


def test_the_size_limit_trims_non_watched_papers_before_watched_ones(factory, seeded):
    profile, field = seeded
    tight = profile.model_copy(update={"newsletter": profile.newsletter.model_copy(update={"caps": CapsConfig(per_section={}, max_html_kb=30)})})
    j = {"type": "journal", "key": "cephalalgia", "track": "A", "via": "issn", "confidence": "id"}
    watched = [add(factory, profile, n, entities=[j]) for n in range(1, 6)]
    for n in range(6, 100):
        add(factory, profile, n, abstract="Conclusions: " + "A fairly long concluding sentence about migraine outcomes. " * 4)
        add_fulltext(factory, n, rights="open", licence_key="cc-by")                            # these outscore the watched ones on points
    svc(factory).build(tight, field, WEEK_END)
    assert set(watched) <= {i[0] for i in item_ids(factory)}


# ---------------------------------------------------------------- held-back papers return once, ranked lower, then drop out
def with_config(profile, **newsletter):
    return profile.model_copy(update={"newsletter": profile.newsletter.model_copy(update=newsletter)})


def caps(n, **kw):
    return {"caps": CapsConfig(per_section={"direct": n}, max_html_kb=None), **kw}


def edition(factory, key):
    with session_scope(factory) as s:
        issue = s.scalar(select(m.Issue).where(m.Issue.issue_key == key))
        return [(it.paper_id, it.snapshot.get("carried_over"), it.snapshot.get("score")) for it in issue.items], issue.stats


WATCHED = [{"type": "journal", "key": "cephalalgia", "track": "A", "via": "issn", "confidence": "id"}]


def test_the_defaults_give_one_extra_week_and_a_penalty_and_bad_values_are_refused(curt):
    from pydantic import ValidationError
    from research_agent.domain.field_config import NewsletterConfig
    cfg = curt[0].newsletter
    assert (cfg.lookback_days, cfg.max_catchup_days, cfg.carryover_penalty) == (7, 14, 3)
    for bad in ({"max_catchup_days": 6}, {"carryover_penalty": -1}):
        with pytest.raises(ValidationError):
            NewsletterConfig(**bad)
    assert NewsletterConfig(max_catchup_days=7, carryover_penalty=0).max_catchup_days == 7


def test_the_penalty_only_applies_to_a_carried_over_paper():
    kw = dict(watched_journal=False, watched_researcher=False, specific_subjects=1, has_abstract=True, printable_full_text=False, core_journal=False)
    assert score(**kw, carried_over=False, carryover_penalty=3) == score(**kw) and score(**kw, carried_over=True, carryover_penalty=3) == score(**kw) - 3
    assert score(**kw, carried_over=True, carryover_penalty=0) == score(**kw)


def test_a_held_back_paper_returns_in_the_next_edition_only_and_ranks_below_an_equal_new_paper(factory, seeded):
    profile, field = with_config(seeded[0], **caps(1)), seeded[1]
    held = add(factory, profile, 1, days_ago=3)
    winner = add(factory, profile, 2, days_ago=2, entities=WATCHED)                 # a followed journal wins the single slot
    first = svc(factory).build(profile, field, WEEK_END)
    items, stats = edition(factory, first.issue_key)
    assert [i[0] for i in items] == [winner] and stats["carried_over"] == 0          # nothing is carried in the first edition
    new_equal = add(factory, profile, 3, days_ago=-3)                               # stored after the first edition, same strength as `held`
    roomy = with_config(seeded[0], **caps(5))
    second = svc(factory, NOW + timedelta(days=7)).build(roomy, field, date(2026, 11, 6))
    items, stats = edition(factory, second.issue_key)
    order = [i[0] for i in items]
    assert order == [new_equal, held]                                               # the late paper comes after the new one of equal strength
    flags = {pid: carried for pid, carried, _ in items}
    assert flags == {new_equal: False, held: True} and stats["carried_over"] == 1
    scores = {pid: sc for pid, _, sc in items}
    assert scores[new_equal] - scores[held] == 3
    assert any("carried over from the previous edition" in n for n in second.notes)


def test_a_paper_held_back_twice_is_gone_for_good(factory, seeded):
    profile, field = with_config(seeded[0], **caps(1)), seeded[1]
    gone = add(factory, profile, 1, days_ago=3)
    add(factory, profile, 2, days_ago=2, entities=WATCHED)
    svc(factory).build(profile, field, WEEK_END)                                    # edition 1: `gone` is held back
    add(factory, profile, 3, days_ago=-3, entities=WATCHED)                         # a stronger new paper takes the only slot of edition 2
    second = svc(factory, NOW + timedelta(days=7)).build(profile, field, date(2026, 11, 6))
    assert gone not in [i[0] for i in edition(factory, second.issue_key)[0]]        # edition 2: held back again
    roomy = with_config(seeded[0], **caps(50))
    third = svc(factory, NOW + timedelta(days=14)).build(roomy, field, date(2026, 11, 13))
    assert gone not in [i[0] for i in edition(factory, third.issue_key)[0]]         # edition 3: it no longer qualifies, even with plenty of room
    with session_scope(factory) as s:
        assert s.get(m.Paper, gone) is not None                                     # still in the database (and so in the app's search)


def test_only_a_strong_old_paper_beats_a_new_one_and_the_penalty_decides_it(factory, seeded):
    def run(penalty):
        from research_agent.infrastructure.db.models import Base
        from research_agent.infrastructure.db.session import make_engine, make_session_factory
        engine = make_engine("sqlite://")
        Base.metadata.create_all(engine)
        f = make_session_factory(engine)
        with session_scope(f) as s:
            Repository(s).sync_config(*seeded)
        profile = with_config(seeded[0], **caps(1, carryover_penalty=penalty))
        strong_old = add(f, profile, 1, days_ago=3)
        add(f, profile, 2, days_ago=2, entities=WATCHED)                            # takes edition 1's slot
        with session_scope(f) as s:
            s.add(m.PaperFulltext(paper_id=strong_old, status="full_text", rights="open", print_allowed=True, words=900, text="body"))
        svc(f).build(profile, seeded[1], WEEK_END)
        weak_new = add(f, profile, 3, days_ago=-3)
        second = svc(f, NOW + timedelta(days=7)).build(profile, seeded[1], date(2026, 11, 6))
        return strong_old, weak_new, [i[0] for i in edition(f, second.issue_key)[0]]
    old, new, winners = run(penalty=2)
    assert winners == [old]                                                          # held full text outweighs a small penalty
    old, new, winners = run(penalty=5)
    assert winners == [new]                                                          # a big penalty lets the new paper win the only slot
    old, new, winners = run(penalty=0)
    assert winners == [old]


def test_retraction_alerts_are_never_penalised_or_flagged_as_carried(factory, seeded):
    profile, field = seeded
    pid = add(factory, profile, 1, days_ago=3)
    svc(factory).build(profile, field, WEEK_END)                                     # edition 1 carries it
    with session_scope(factory) as s:
        s.get(m.Paper, pid).is_retracted = True                                      # retracted afterwards
    second = svc(factory, NOW + timedelta(days=7)).build(profile, field, date(2026, 11, 6))
    items, stats = edition(factory, second.issue_key)
    assert [i[0] for i in items] == [pid] and items[0][1] is False and stats["carried_over"] == 0     # first stored 10 days before this cutoff, yet not "late"
    with session_scope(factory) as s:
        assert s.scalar(select(m.IssueItem.role).where(m.IssueItem.issue_id == s.scalar(select(m.Issue.id).where(m.Issue.issue_key == second.issue_key)))) == "retraction"


# ---------------------------------------------------------------- layout and wording (the draft must look right in Gmail's compose window)
def built(factory, profile, field, **kw):
    add(factory, profile, 1, days_ago=2, entities=WATCHED, subjects=[("2", "Trigeminovascular & neuropeptides", "A")])
    r = svc(factory).build(profile, field, WEEK_END, **kw)
    with session_scope(factory) as s:
        i = s.get(m.Issue, r.issue_id)
        return i.html, i.text


def test_the_html_is_styled_inline_in_tables_because_gmail_drops_style_blocks(factory, seeded):
    import re
    html, _ = built(factory, *seeded)
    assert "<style" not in html and "<link" not in html and "@media" not in html
    assert html.count("<table") == html.count("</table>") and html.count("<tr") == html.count("</tr>") and html.count("<td") == html.count("</td>")
    assert "max-width:760px" in html and 'role="presentation"' in html
    for tag in re.findall(r"<(?:h1|a|td|div|span|p)\b[^>]*>", html):                 # every visible element carries its own style
        assert "style=" in tag, tag


def test_greeting_introduction_lead_closing_signature_and_footer_appear_in_order(factory, seeded):
    profile, field = seeded
    cfg = profile.newsletter.model_copy(update={"greeting": "Hello Curt,", "introduction": "Here is the round-up.", "results_lead": "The papers:", "closing": "Enjoy the weekend.",
                                                "footer": "Prepared by the team.", "signature": SignatureConfig(name="A. Sender", title="Assistant", email="a@x.org", disclaimer="Confidential.")})
    html, text = built(factory, profile.model_copy(update={"newsletter": cfg}), field)
    for doc in (html, text):
        positions = [doc.index(x) for x in ("Hello Curt,", "Here is the round-up.", "The papers:", "Enjoy the weekend.", "A. Sender", "Assistant", "Confidential.", "Prepared by the team.")]
        assert positions == sorted(positions)
    assert "mailto:a@x.org" in html and "a@x.org" in text
    assert html.index("Hello Curt,") < html.index("Direct research") < html.index("Enjoy the weekend.")


def test_blank_wording_is_left_out_not_shown_as_empty_boxes(factory, seeded):
    profile, field = seeded
    cfg = profile.newsletter.model_copy(update={"greeting": "", "introduction": "", "results_lead": "", "closing": "", "signature": None, "footer": ""})
    html, text = built(factory, profile.model_copy(update={"newsletter": cfg}), field)
    assert "Hello" not in html and "weekend" not in html and "mailto" not in html and "Prepared" not in html
    assert "This week's papers" not in html and "Direct research" in html


def test_wording_is_escaped_in_html_and_left_plain_in_text(factory, seeded):
    profile, field = seeded
    cfg = profile.newsletter.model_copy(update={"greeting": "Hi <b>Curt</b> & co,", "closing": "See you <i>soon</i>"})
    html, text = built(factory, profile.model_copy(update={"newsletter": cfg}), field)
    assert "Hi &lt;b&gt;Curt&lt;/b&gt; &amp; co," in html and "<b>Curt</b>" not in html and "&lt;i&gt;soon&lt;/i&gt;" in html
    assert "Hi <b>Curt</b> & co," in text


def test_the_accent_colour_is_a_setting_that_reaches_the_header_and_links_and_is_validated(factory, seeded):
    from pydantic import ValidationError
    from research_agent.domain.field_config import NewsletterConfig
    profile, field = seeded
    html, _ = built(factory, profile.model_copy(update={"newsletter": profile.newsletter.model_copy(update={"accent_color": "#7a1fa2"})}), field)
    assert html.count("#7a1fa2") >= 5 and "#1f5f8b" not in html
    for bad in ("blue", "#12345", "#gggggg", "1f5f8b"):
        with pytest.raises(ValidationError):
            NewsletterConfig(accent_color=bad)


def test_label_pills_are_coloured_by_what_they_say():
    from research_agent.infrastructure.rendering import badge_tone
    assert badge_tone("RETRACTED", "#123456")[1] == "#b42318" and badge_tone("Full text available", "#123456")[1] == "#166534"
    assert badge_tone("Open access", "#123456")[1] == "#1d4ed8" and badge_tone("Preprint: not peer reviewed", "#123456")[1] == "#92400e"
    assert badge_tone("Watched journal: Cephalalgia", "#123456")[1] == "#123456" and badge_tone("Exploratory", "#123456") == ("#f1f5f9", "#475569")


def test_the_glance_row_counts_sections_full_text_and_followed_sources(factory, seeded):
    import re
    profile, field = seeded
    pid = add(factory, profile, 1, days_ago=2, entities=WATCHED)
    with session_scope(factory) as s:
        s.add(m.PaperFulltext(paper_id=pid, status="full_text", rights="open", print_allowed=True, words=500, text="t"))
    add(factory, profile, 2, days_ago=2)
    r = svc(factory).build(profile, field, WEEK_END)
    with session_scope(factory) as s:
        html = s.get(m.Issue, r.issue_id).html
    assert "This week at a glance" in html and re.search(r">2</strong> Direct research", html) and re.search(r">1</strong> with the full text held", html) and re.search(r">1</strong> from followed journals or researchers", html)


def test_a_late_paper_wears_a_held_over_label_and_a_new_one_does_not(factory, seeded):
    profile, field = with_config(seeded[0], **caps(1)), seeded[1]
    late = add(factory, profile, 1, days_ago=3)
    add(factory, profile, 2, days_ago=2, entities=WATCHED)
    svc(factory).build(profile, field, WEEK_END)
    new = add(factory, profile, 3, days_ago=-3)
    r = svc(factory, NOW + timedelta(days=7)).build(with_config(seeded[0], **caps(5)), field, date(2026, 11, 6))
    with session_scope(factory) as s:
        html = s.get(m.Issue, r.issue_id).html
    assert html.count("Held over from last week") == 1
    assert html.index(f"number {new} with") < html.index(f"number {late} with") < html.index("Held over from last week")      # the new paper first; the late one, and its label, after


def test_the_curt_profile_has_its_own_wording_in_the_same_spirit_as_the_old_agent(curt):
    cfg = curt[0].newsletter
    assert cfg.greeting.startswith("Hello Curt") and cfg.signature and cfg.signature.name and cfg.footer
    assert cfg.greeting != "Good morning Curt," and cfg.closing != "Have a great weekend." and cfg.results_lead != "Here are the latest publications:"     # similar, not the same
    assert "email" not in (cfg.signature.model_dump(exclude_none=True)) and "phone" not in cfg.signature.model_dump(exclude_none=True)   # contact details are not committed unless chosen
