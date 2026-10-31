"""Routing: order, fallbacks, retries, rights policy, and the 'add a premium key later' upgrade path."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from research_agent.application.fulltext import FullTextService
from research_agent.domain.capabilities import ProviderInfo, Rights, Tier
from research_agent.domain.fulltext import ERROR, NOT_AVAILABLE, NOT_PERMITTED, OK, FullTextResult
from research_agent.infrastructure.db import models as m
from research_agent.infrastructure.db.repository import Repository
from research_agent.infrastructure.db.session import session_scope
from conftest import rec


class FakeProvider:
    def __init__(self, name, priority, outcome=OK, tier=Tier.FREE, rights=Rights.OPEN, licence="cc-by", on=True, supports=lambda r: True, crash=False):
        self.name, self.priority, self.tier = name, priority, tier
        self.outcome, self.rights, self.licence, self.on, self._supports, self.crash = outcome, rights, licence, on, supports, crash
        self.calls = []

    def info(self):
        return ProviderInfo(self.name, "fulltext", self.tier, self.on)

    def active(self):
        return self.on

    def supports(self, req):
        return self._supports(req)

    def fetch(self, req):
        self.calls.append(req.paper_id)
        if self.crash:
            raise RuntimeError("bug")
        if self.outcome != OK:
            return FullTextResult(self.outcome, self.name, detail="nope")
        return FullTextResult(OK, self.name, text="word " * 500, format="jats_xml", licence=self.licence, rights=self.rights, source_url=f"https://{self.name}/x")


@pytest.fixture()
def seeded(factory, curt):
    profile, field = curt
    with session_scope(factory) as s:
        repo = Repository(s)
        repo.sync_config(profile, field)
        ids = []
        for i in range(3):
            p = repo.merge_record(rec(sid=str(i), pmid=str(i), doi=f"10.1016/x{i}", title=f"Migraine study number {i} with a sufficiently long title here")).paper
            repo.upsert_match(profile.id, p.id, gate_status="passed", language_status="ok", track="A", subjects=[], entities=[])
            ids.append(p.id)
    return profile, ids


def svc(factory, *providers, now=None):
    return FullTextService(factory, list(providers), now=now or (lambda: datetime.now(timezone.utc)))


def rows(factory):
    with session_scope(factory) as s:
        return {r.paper_id: r for r in s.scalars(select(m.PaperFulltext))}


def test_first_successful_provider_in_priority_order_wins(factory, seeded):
    profile, ids = seeded
    a, b = FakeProvider("late", 50), FakeProvider("early", 10)
    summary = svc(factory, a, b).run(profile)  # the service trusts the order it is given (the registry sorts); here late is first
    assert summary.obtained == 3 and len(a.calls) == 3 and b.calls == []
    r = rows(factory)[ids[0]]
    assert r.status == "full_text" and r.provider == "late" and r.print_allowed is True and r.words == 500 and r.licence_key == "cc-by"


def test_fallback_to_the_next_provider_and_attempts_are_recorded(factory, seeded):
    profile, ids = seeded
    s = svc(factory, FakeProvider("first", 1, NOT_AVAILABLE), FakeProvider("second", 2, ERROR), FakeProvider("third", 3))
    summary = s.run(profile)
    assert summary.obtained == 3 and summary.by_provider["first"][NOT_AVAILABLE] == 3 and summary.by_provider["third"][OK] == 3
    with session_scope(factory) as db:
        assert {(a.provider, a.outcome) for a in db.scalars(select(m.FulltextAttempt))} == {("first", NOT_AVAILABLE), ("second", ERROR), ("third", OK)}


def test_inactive_providers_are_ignored_and_unsupported_papers_skipped(factory, seeded):
    profile, ids = seeded
    off = FakeProvider("off", 1, on=False)
    only_first = FakeProvider("selective", 2, supports=lambda r: r.paper_id == ids[0])
    s = svc(factory, off, only_first)
    assert [p.name for p in s.providers] == ["selective"]
    summary = s.run(profile)
    assert summary.obtained == 1 and off.calls == [] and only_first.calls == [ids[0]]
    assert rows(factory)[ids[1]].status == "abstract_and_links_only"


def test_a_provider_that_could_not_help_is_not_asked_again_until_the_retry_window_passes(factory, seeded):
    profile, ids = seeded
    t0 = datetime(2026, 10, 30, tzinfo=timezone.utc)
    p = FakeProvider("flaky", 1, NOT_AVAILABLE)
    svc(factory, p, now=lambda: t0).run(profile)
    assert len(p.calls) == 3
    svc(factory, p, now=lambda: t0 + timedelta(days=2)).run(profile)
    assert len(p.calls) == 3  # inside the 7 day window: not asked again
    svc(factory, p, now=lambda: t0 + timedelta(days=8)).run(profile)
    assert len(p.calls) == 6  # window passed: asked again


def test_errors_are_retried_with_escalating_waits(factory, seeded):
    profile, _ = seeded
    t0 = datetime(2026, 10, 30, tzinfo=timezone.utc)
    p = FakeProvider("down", 1, ERROR)
    calls_after = lambda days: (svc(factory, p, now=lambda: t0 + timedelta(days=days)).run(profile), len(p.calls))[1]
    assert calls_after(0) == 3                 # first failure
    assert calls_after(0.5) == 3               # too soon: waits 1 day after one error
    assert calls_after(1.1) == 6               # retried after a day -> second error in a row
    assert calls_after(2.2) == 6               # now waits 2 days
    assert calls_after(3.5) == 9               # third error in a row -> next wait 4 days (capped at the 7 day retry window)
    assert calls_after(5) == 9
    assert calls_after(8) == 12


def test_a_success_clears_the_error_streak(factory, seeded):
    profile, _ = seeded
    t0 = datetime(2026, 10, 30, tzinfo=timezone.utc)
    flaky = FakeProvider("flaky", 1, ERROR)
    svc(factory, flaky, now=lambda: t0).run(profile)
    flaky.outcome = OK
    summary = svc(factory, flaky, now=lambda: t0 + timedelta(days=1.1)).run(profile)
    assert summary.obtained == 3


def test_adding_a_premium_provider_later_retries_old_failures_with_only_the_new_one(factory, seeded):
    profile, ids = seeded
    free = FakeProvider("free", 10, NOT_AVAILABLE)
    svc(factory, free).run(profile)
    assert all(r.status == "abstract_and_links_only" for r in rows(factory).values())
    premium = FakeProvider("premium", 30, tier=Tier.PREMIUM, rights=Rights.ENTITLED, licence=None)
    summary = svc(factory, free, premium).run(profile)   # same moment, so the free provider is inside its retry window
    assert summary.obtained == 3 and len(free.calls) == 3 and len(premium.calls) == 3
    r = rows(factory)[ids[0]]
    assert r.status == "full_text" and r.tier == "premium" and r.rights == "entitled"


def cautious(profile, **kw):
    return profile.model_copy(update={"fulltext": profile.fulltext.model_copy(update={"print_text_regardless_of_licence": False, **kw})})


def test_paid_access_text_is_printable_by_default(factory, seeded):
    profile, ids = seeded
    svc(factory, FakeProvider("paid", 1, tier=Tier.PREMIUM, rights=Rights.ENTITLED, licence=None)).run(profile)
    r = rows(factory)[ids[0]]
    assert r.status == "full_text" and r.print_allowed is True and r.rights == "entitled" and r.text      # recorded as entitled, but not blocked from print


def test_in_cautious_mode_paid_access_is_stored_but_not_printable_unless_allowed(factory, seeded):
    profile, ids = seeded
    svc(factory, FakeProvider("paid", 1, tier=Tier.PREMIUM, rights=Rights.ENTITLED, licence=None)).run(cautious(profile))
    assert rows(factory)[ids[0]].print_allowed is False and rows(factory)[ids[0]].text


def test_in_cautious_mode_the_profile_can_allow_entitled_text_in_print(factory, seeded):
    profile, ids = seeded
    svc(factory, FakeProvider("paid", 1, tier=Tier.PREMIUM, rights=Rights.ENTITLED, licence=None)).run(cautious(profile, allow_entitled_in_print=True))
    assert rows(factory)[ids[0]].print_allowed is True


def test_policy_refusing_to_store_entitled_text(factory, seeded):
    profile, ids = seeded
    strict = profile.model_copy(update={"fulltext": profile.fulltext.model_copy(update={"store_entitled_text": False})})
    summary = svc(factory, FakeProvider("paid", 1, tier=Tier.PREMIUM, rights=Rights.ENTITLED, licence=None)).run(strict)
    assert summary.obtained == 0 and rows(factory)[ids[0]].status == "abstract_and_links_only" and rows(factory)[ids[0]].text is None


def test_restrictive_licences_do_not_block_printing_by_default_but_are_recorded(factory, seeded):
    profile, ids = seeded
    svc(factory, FakeProvider("nc", 1, licence="cc-by-nc-nd")).run(profile)
    r = rows(factory)[ids[0]]
    assert r.status == "full_text" and r.licence_key == "cc-by-nc-nd" and r.print_allowed is True


def test_in_cautious_mode_a_restrictive_licence_is_kept_but_not_printable(factory, seeded):
    profile, ids = seeded
    svc(factory, FakeProvider("nc", 1, licence="cc-by-nc-nd")).run(cautious(profile))
    r = rows(factory)[ids[0]]
    assert r.status == "full_text" and r.licence_key == "cc-by-nc-nd" and r.print_allowed is False


def test_reapplying_the_policy_changes_stored_texts_without_any_fetching(factory, seeded):
    from research_agent.application.fulltext import policy_for
    profile, ids = seeded
    svc(factory, FakeProvider("nc", 1, licence="cc-by-nc-nd")).run(cautious(profile))
    assert rows(factory)[ids[0]].print_allowed is False
    policy = policy_for(profile)                       # the default: print regardless of licence
    with session_scope(factory) as s:
        n = Repository(s).reapply_print_policy(lambda lic, rights: policy.print_allowed(FullTextResult(OK, "", licence=lic, rights=Rights(rights or "unknown"))))
    assert n == 3 and all(r.print_allowed for r in rows(factory).values())


def test_a_crashing_provider_does_not_stop_the_batch(factory, seeded):
    profile, _ = seeded
    s = svc(factory, FakeProvider("buggy", 1, crash=True), FakeProvider("good", 2))
    summary = s.run(profile)
    assert summary.obtained == 3 and summary.by_provider["buggy"][ERROR] == 3


def test_already_fetched_trials_and_excluded_types_are_not_candidates(factory, seeded, curt):
    profile, ids = seeded
    with session_scope(factory) as s:
        repo = Repository(s)
        t = repo.merge_record(rec(source="ctgov", sid="NCT1", nct_id="NCT1", doi=None, is_trial=True, title="A registered migraine trial with a long enough title")).paper
        z = repo.merge_record(rec(sid="99", pmid="99", doi="10.5281/zenodo.1", title="Deposited migraine slides with a long enough title here")).paper
        for p in (t, z):
            repo.upsert_match(profile.id, p.id, gate_status="passed", language_status="ok", track="A", subjects=[], entities=[])
    p = FakeProvider("any", 1)
    summary = svc(factory, p).run(profile)
    assert summary.considered == 3 and t.id not in p.calls and z.id not in p.calls
    assert svc(factory, p).run(profile).considered == 0   # all three now have text


def test_per_provider_quota(factory, seeded):
    profile, _ = seeded
    p = FakeProvider("metered", 1)
    summary = svc(factory, p).run(profile, max_per_provider={"metered": 2})
    assert summary.obtained == 2 and len(p.calls) == 2


def test_dry_run_lists_routes_without_calling_anyone(factory, seeded):
    profile, ids = seeded
    a, b = FakeProvider("a", 1), FakeProvider("b", 2, supports=lambda r: False)
    routes = svc(factory, a, b).routes(profile)
    assert len(routes) == 3 and all(r[1] == ["a"] for r in routes) and a.calls == []


# ---------------------------------------------------------------- re-checking for a PMC ID / free copy after the first run missed it
class FakeEnricher:
    def __init__(self, name, by_doi=None, error=None):
        self.name, self.by_doi, self.error, self.calls = name, by_doi or {}, error, []

    def lookup(self, dois):
        self.calls.append(list(dois))
        return [r for d, r in self.by_doi.items() if d in dois], self.error


def pmc_only():
    return FakeProvider("pmc", 1, supports=lambda r: bool(r.pmcid))


def svc2(factory, provider, enrichers, now):
    return FullTextService(factory, [provider], enrichers, now=now)


T0 = datetime(2026, 10, 30, tzinfo=timezone.utc)


def test_a_paper_that_reaches_pmc_later_is_picked_up(factory, seeded):
    profile, ids = seeded
    epmc = FakeEnricher("europepmc")
    p = pmc_only()
    s1 = svc2(factory, p, {"europepmc": epmc}, lambda: T0).run(profile)
    assert s1.obtained == 0 and s1.refreshed == 3 and p.calls == []          # nothing in PMC yet: abstract and links only
    assert all(r.status == "abstract_and_links_only" for r in rows(factory).values())
    epmc.by_doi = {"10.1016/x1": rec("europepmc", "MED:1", doi="10.1016/x1", pmcid="PMC777")}   # three weeks later it is in PMC
    s2 = svc2(factory, p, {"europepmc": epmc}, lambda: T0 + timedelta(days=21)).run(profile)
    assert s2.pmcid_found == 1 and s2.obtained == 1 and p.calls == [ids[1]]
    assert rows(factory)[ids[1]].status == "full_text" and rows(factory)[ids[0]].status == "abstract_and_links_only"
    with session_scope(factory) as db:
        assert db.get(m.Paper, ids[1]).pmcid == "PMC777"


def test_free_copies_found_later_are_stored_as_links(factory, seeded):
    from research_agent.domain.models import Link
    profile, ids = seeded
    oa = FakeEnricher("openalex", {"10.1016/x0": rec("openalex", "W1", doi="10.1016/x0", links=[Link("pdf", "https://repo.test/a.pdf", "openalex", is_free=True, licence="cc-by")])})
    s = svc2(factory, FakeProvider("none", 1, NOT_AVAILABLE), {"openalex": oa}, lambda: T0).run(profile)
    assert s.free_links_found == 1
    with session_scope(factory) as db:
        assert [(l.url, l.is_free) for l in db.get(m.Paper, ids[0]).links if l.kind == "pdf"] == [("https://repo.test/a.pdf", True)]


def test_each_paper_is_looked_up_at_most_once_per_retry_window(factory, seeded):
    profile, _ = seeded
    epmc = FakeEnricher("europepmc")
    run = lambda days: svc2(factory, pmc_only(), {"europepmc": epmc}, lambda: T0 + timedelta(days=days)).run(profile)
    run(0); run(2); run(5)
    assert len(epmc.calls) == 1
    run(8)
    assert len(epmc.calls) == 2


def test_papers_older_than_the_recheck_window_are_left_alone(factory, seeded):
    profile, _ = seeded
    epmc = FakeEnricher("europepmc")
    s = svc2(factory, pmc_only(), {"europepmc": epmc}, lambda: T0 + timedelta(days=200)).run(profile)
    assert s.refreshed == 0 and epmc.calls == []


def test_a_failing_lookup_is_reported_and_does_not_stop_the_run(factory, seeded):
    profile, _ = seeded
    s = svc2(factory, FakeProvider("any", 1), {"europepmc": FakeEnricher("europepmc", error="HTTP 500")}, lambda: T0).run(profile)
    assert s.obtained == 3 and s.refresh_errors == ["europepmc: HTTP 500"]


def test_refresh_can_be_switched_off(factory, seeded):
    profile, _ = seeded
    epmc = FakeEnricher("europepmc")
    svc2(factory, pmc_only(), {"europepmc": epmc}, lambda: T0).run(profile, refresh=False)
    assert epmc.calls == []


def test_each_full_text_pass_is_logged_so_pmc_growth_can_be_tracked(factory, seeded):
    profile, ids = seeded
    epmc = FakeEnricher("europepmc", {"10.1016/x1": rec("europepmc", "MED:1", doi="10.1016/x1", pmcid="PMC777")})
    svc2(factory, pmc_only(), {"europepmc": epmc}, lambda: T0).run(profile, trigger="cron")
    with session_scope(factory) as db:
        run = db.scalar(select(m.Run).where(m.Run.kind == "fulltext"))
        assert run.trigger == "cron" and run.status == "ok" and run.stats["refreshed"] == 3 and run.stats["pmcid_found"] == 1 and run.stats["obtained"] == 1
