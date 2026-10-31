"""Command line: `research-agent db upgrade | seed | plan | collect | status | papers`."""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select

from .application.planner import build_specs
from .bootstrap import build_fulltext, build_newsletter, build_pipeline, build_publisher, providers as make_providers, session_factory
from .domain.field_config import load_profile
from .domain.models import DateWindow
from .infrastructure.db import migrate, models as m
from .infrastructure.db.repository import Repository
from .infrastructure.db.session import session_scope
from .settings import Settings


def _csv(v: str | None) -> set[str] | None:
    return {x.strip() for x in v.split(",") if x.strip()} if v else None


def cmd_db(args, settings: Settings) -> int:
    if args.action == "roles":
        from .infrastructure.db import roles
        from .settings import PROJECT_ROOT
        if args.print_sql:
            print(roles.preview_sql())
            return 0
        try:
            roles.apply_roles(settings.database_url, PROJECT_ROOT / ".env", args.pooler_host, args.pooler_port)
        except (ValueError, RuntimeError) as exc:
            print(exc)
            return 2
        print(f"roles {roles.READ_ROLE} (read only) and {roles.CONFIG_ROLE} (newsletter switches only) are ready; "
              "API_DATABASE_URL and API_ADMIN_DATABASE_URL were written to .env (passwords not shown)")
        return 0
    if args.action == "upgrade":
        migrate.upgrade(settings.database_url)
        print("database is at head")
    else:
        migrate.check(settings.database_url)
        print("models and migrations agree")
    return 0


def cmd_job(args, settings: Settings) -> int:
    from .jobs import run_job
    week_ending = args.week_ending or datetime.now(timezone.utc).date().isoformat()
    return run_job(args.kind, settings, args.profile, week_ending, main)


def cmd_fix_abstracts(args, settings: Settings) -> int:
    """Repair glued words (section labels and sentence ends) in abstracts already stored. Shows what it would do unless --apply is given."""
    from .domain.text_repair import repair_abstract
    changed = spaces = 0
    examples: list[str] = []
    with session_scope(session_factory(settings)) as s:
        for p in s.scalars(select(m.Paper).where(m.Paper.abstract != "")):
            fixed = repair_abstract(p.abstract)
            if fixed == p.abstract:
                continue
            changed += 1
            spaces += len(fixed) - len(p.abstract)
            if len(examples) < 5:
                at = next(i for i, (a, b) in enumerate(zip(p.abstract, fixed)) if a != b)
                examples.append(f"  paper {p.id}: ...{p.abstract[max(0, at - 18):at + 16]!r} -> ...{fixed[max(0, at - 18):at + 17]!r}")
            if args.apply:
                p.abstract = fixed
    print(f"{changed} abstract(s) {'repaired' if args.apply else 'would be repaired'}, {spaces} space(s) {'added' if args.apply else 'to add'}; nothing else changes")
    print("\n".join(examples))
    if changed and not args.apply:
        print("Run again with --apply to store the repaired text.")
    return 0


def cmd_gmail(args, settings: Settings) -> int:
    """One-time Gmail sign-in: opens the browser, asks Google for permission to create drafts (never to read or send), and writes the refresh token
    straight into .env. Nothing secret is printed."""
    from .infrastructure.envfile import set_env_values
    from .infrastructure.publishers import GMAIL_COMPOSE_SCOPE, build_gmail_service
    from .settings import PROJECT_ROOT
    if not (settings.google_client_id and settings.google_client_secret):
        print("Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env first (an OAuth client of type Desktop app; see README, section Gmail drafts).")
        return 2
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print('Gmail support is not installed here: pip install -e ".[gmail]"')
        return 2
    config = {"installed": {"client_id": settings.google_client_id, "client_secret": settings.google_client_secret,
                            "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": ["http://localhost"]}}
    flow = InstalledAppFlow.from_client_config(config, scopes=[GMAIL_COMPOSE_SCOPE])
    creds = flow.run_local_server(port=args.port, access_type="offline", prompt="consent")
    if not creds.refresh_token:
        print("Google did not return a refresh token. Remove this app at https://myaccount.google.com/permissions and run the command again.")
        return 1
    set_env_values(PROJECT_ROOT / ".env", {"GOOGLE_REFRESH_TOKEN": creds.refresh_token})
    build_gmail_service(settings.google_client_id, settings.google_client_secret, creds.refresh_token).users().drafts().list(userId="me", maxResults=1).execute()
    print("Gmail is authorised for creating drafts. GOOGLE_REFRESH_TOKEN was written to .env (not shown); copy it into the weekly-newsletter service's variables.")
    return 0


def cmd_seed(args, settings: Settings) -> int:
    profile, field = load_profile(settings.config_dir, args.profile)
    with session_scope(session_factory(settings)) as s:
        Repository(s).sync_config(profile, field)
    print(f"seeded profile {profile.id}: {len(profile.journals)} journals, {len(profile.researchers)} researchers, {len(field.subjects)} subjects")
    return 0


def cmd_plan(args, settings: Settings) -> int:
    profile, field = load_profile(settings.config_dir, args.profile)
    specs = build_specs(profile, field, sources=_csv(args.sources), paths=_csv(args.paths))
    for sp in specs:
        print(f"{sp.path:10} {sp.source:10} {sp.label[:60]:60} {sp.confidence}")
    print(f"{len(specs)} searches")
    return 0


def cmd_collect(args, settings: Settings) -> int:
    profile, field = load_profile(settings.config_dir, args.profile)
    end = date.fromisoformat(args.date) if args.date else datetime.now(timezone.utc).date()
    window = DateWindow.rolling(end, args.overlap if args.overlap is not None else settings.overlap_days)
    pipeline = build_pipeline(settings)
    summary = pipeline.run(profile, field, window, trigger=args.trigger, only_sources=_csv(args.sources), only_paths=_csv(args.paths), enrich=not args.no_enrich)
    print(f"run {summary.run_id} [{summary.status}] window {summary.window}  {summary.duration_s}s")
    print(f"  searches {summary.specs}  records {summary.records}  rejected by gate {summary.rejected_by_gate}")
    print(f"  new papers {summary.new_papers}  new matches {summary.new_matches}  passed gate {summary.passed_matches}")
    for name, c in sorted(summary.by_source.items()):
        print(f"  {name:10} {dict(c)}")
    if summary.enrichment:
        print(f"  enrichment {summary.enrichment}")
    for line in summary.suspect:
        print(f"  SUSPECT ZERO: {line}")
    for line in summary.errors[:15]:
        print(f"  ERROR: {line}")
    return 0 if summary.status in ("ok", "partial") else 1


def cmd_status(args, settings: Settings) -> int:
    with session_scope(session_factory(settings)) as s:
        for r in s.scalars(select(m.Run).order_by(m.Run.id.desc()).limit(args.runs)):
            st = r.stats or {}
            detail = (f"text {st.get('obtained', '-')}/{st.get('considered', '-')} re-checked {st.get('refreshed', '-')} new PMC IDs {st.get('pmcid_found', '-')}"
                      if r.kind == "fulltext" else f"new {st.get('new_papers', '-')} passed {st.get('passed_matches', '-')}")
            print(f"run {r.id:4} {r.kind:9} {r.status:8} {r.window_start}..{r.window_end} started {r.started_at:%Y-%m-%d %H:%M} {detail} errors {len(st.get('errors', []))}")
        print("papers:", s.scalar(select(func.count()).select_from(m.Paper)), " matches passed:",
              s.scalar(select(func.count()).select_from(m.ProfileMatch).where(m.ProfileMatch.gate_status == "passed")))
        if args.max_age_hours:       # watchdog: a schedule that stopped quietly must not go unnoticed
            last = s.scalar(select(func.max(m.Run.finished_at)).where(m.Run.kind == "collect", m.Run.status.in_(["ok", "partial"])))
            if last is not None and last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            age = None if last is None else (datetime.now(timezone.utc) - last).total_seconds() / 3600
            if age is None or age > args.max_age_hours:
                print(f"STALE: " + ("no successful collection run yet" if age is None else f"the last successful collection finished {age:.0f} hours ago") + f" (limit {args.max_age_hours} hours)")
                return 1
            print(f"fresh: the last successful collection finished {age:.1f} hours ago")
    return 0


def cmd_papers(args, settings: Settings) -> int:
    profile, _ = load_profile(settings.config_dir, args.profile)
    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    excluded = [] if args.all_types else profile.digest.exclude_content_types
    with session_scope(session_factory(settings)) as s:
        rows = Repository(s).digest_candidates(profile.id, since=since, exclude_types=excluded, limit=args.limit)
        for p, mt in rows:
            flag = "RETRACTED " if p.is_retracted else ("NOTICE " if p.is_retraction_notice else "")
            print(f"{p.first_entry_date} [{mt.track}{'*' if mt.exploratory else ''}] {p.content_type:18} {flag}{p.title[:80]} | {p.journal} | {p.doi or p.pmid}")
        print(f"{len(rows)} papers" + ("" if args.all_types else f" (hiding {', '.join(excluded)}; use --all-types to show them)"))
    return 0


def cmd_pmc_lag(args, settings: Settings) -> int:
    profile, _ = load_profile(settings.config_dir, args.profile)
    with session_scope(session_factory(settings)) as s:
        r = Repository(s).pmc_lag_report(profile.id, exclude_types=profile.digest.exclude_content_types, now=datetime.now(timezone.utc))
    print(f"{r['papers']} papers with a DOI that passed the gate; {r['with_pmcid']} have a PMC ID")
    print("time from first stored to PMC ID found:", r["found_after"] or "-")
    print("still without a PMC ID, by time since first stored:", r["still_without_by_age"] or "-")
    return 0


def choose_recipients(settings: Settings, profile) -> list[str]:
    """The 'To' addresses for a draft: NEWSLETTER_RECIPIENTS if set, otherwise the profile's list (empty by default)."""
    return list(settings.newsletter_recipients) or list(profile.newsletter.recipients)


def cmd_newsletter(args, settings: Settings) -> int:
    from .application.newsletter import IssueExists
    profile, field = load_profile(settings.config_dir, args.profile)
    svc = build_newsletter(settings, field_name=field.name.lower(), max_words=profile.newsletter.summary_words)
    if args.nl == "build":
        week_end = date.fromisoformat(args.week_ending) if args.week_ending else datetime.now(timezone.utc).date()
        try:
            r = svc.build(profile, field, week_end, rebuild=args.rebuild, recheck=not args.no_recheck, use_llm=not args.no_llm)
        except IssueExists as exc:
            print(exc)
            return 1
        print(f"{r.issue_key} [{r.status}] {'built' if r.created else 'already existed'}: {r.total} items {r.counts}")
        print(f"  window {r.window_start:%Y-%m-%d %H:%M} to {r.window_end:%Y-%m-%d %H:%M} UTC")
        for n in r.notes:
            print("  note:", n)
        if r.total == 0:
            print("  warning: this edition is empty")
        return 0
    with session_scope(session_factory(settings)) as s:
        repo = Repository(s)
        if args.nl == "list":
            for i in s.scalars(select(m.Issue).where(m.Issue.profile_id == profile.id).order_by(m.Issue.window_end.desc()).limit(20)):
                print(f"{i.issue_key:28} {i.status:9} {sum(i.stats.get('counts', {}).values()):4} items  {i.stats.get('counts', {})}")
            return 0
        issue = repo.get_issue(profile.id, args.issue)
        if issue is None:
            print(f"no issue {args.issue}")
            return 1
        if args.nl == "show":
            print(issue.html if args.format == "html" else issue.text)
            return 0
        key, subject, html, text, ref = issue.issue_key, issue.subject, issue.html, issue.text, issue.draft_ref
        status = issue.status
    if args.nl == "publish":
        if status == "sent":
            print("issue is already marked sent")
            return 1
        ref = build_publisher(settings, args.to).publish(subject, html, text, key, choose_recipients(settings, profile), ref)
        svc.mark_published(profile.id, key, ref)
        print(f"{key}: draft ready via {args.to}: {ref}")
        return 0
    if args.nl == "mark-sent":
        svc.mark_sent(profile.id, key)
        print(f"{key}: marked sent")
        return 0
    return 2


def cmd_api(args, settings: Settings) -> int:
    if args.action == "token":    # a new random token to put in API_TOKENS; it is printed once and not stored anywhere
        import secrets
        print(secrets.token_urlsafe(32))
        return 0
    import uvicorn
    from .bootstrap import build_api
    from .interfaces.api.app import ApiNotConfigured
    try:
        app = build_api(settings)
    except ApiNotConfigured as exc:
        print(exc)
        return 1
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def cmd_reclassify(args, settings: Settings) -> int:
    with session_scope(session_factory(settings)) as s:
        print(f"{Repository(s).reclassify_all()} papers changed content type")
    return 0


def cmd_providers(args, settings: Settings) -> int:
    p = make_providers(settings)
    for i in p.describe():
        print(f"{'ON ' if i.active else 'off'} {i.kind:9} {i.name:26} {i.tier.value:8} {i.detail}")
        if i.upgrade and (not i.active or i.tier.value == 'free'):
            print(f"{'':43}upgrade: {i.upgrade}")
    order = [x.name for x in p.fulltext]
    print("full-text routing order:", " -> ".join(order) if order else "(none active)")
    return 0


def cmd_fulltext(args, settings: Settings) -> int:
    profile, field = load_profile(settings.config_dir, args.profile)
    if args.reapply_policy:   # no network: recompute which stored texts are printable under the profile's current policy
        from .application.fulltext import policy_for
        from .domain.capabilities import Rights
        from .domain.fulltext import FullTextResult
        policy = policy_for(profile)
        with session_scope(session_factory(settings)) as s:
            n = Repository(s).reapply_print_policy(lambda lic, rights: policy.print_allowed(FullTextResult("ok", "", licence=lic, rights=Rights(rights or "unknown"))))
        print(f"{n} stored texts changed printable status")
        return 0
    service = build_fulltext(settings)
    if args.dry_run:
        for title, route in service.routes(profile, limit=args.limit):
            print(f"{title:72} -> {', '.join(route) or 'no route (abstract + links only)'}")
        return 0
    if args.verify_stored:   # no network: drop stored texts that are not their paper
        gone = service.verify_stored(profile)
        for pid, title in gone:
            print(f"  removed wrong text for paper {pid}: {title}")
        print(f"{len(gone)} stored texts were not their paper and were removed")
        return 0
    if args.recover_abstracts:   # no network: look for the abstract in full texts we already hold
        print(f"{service.recover_abstracts(profile, field)} abstracts recovered from stored texts")
        return 0
    r = service.run(profile, limit=args.limit, refresh=not args.no_refresh, field_cfg=field)
    print(f"abstracts recovered from the paper's own text: {r.abstracts_recovered}")
    print(f"re-checked {r.refreshed} papers for a PMC ID or free copy: {r.pmcid_found} new PMC IDs, {r.free_links_found} papers with new free links")
    for e in r.refresh_errors:
        print("  refresh error:", e)
    print(f"considered {r.considered}: text obtained {r.obtained}, without text {r.without_text}, printable under policy {r.print_allowed}")
    print("order:", " -> ".join(r.provider_order))
    for name, c in r.by_provider.items():
        print(f"  {name:26} {dict(c)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-agent")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("db"); p.add_argument("action", choices=["upgrade", "check", "roles"])
    p.add_argument("--print-sql", action="store_true", help="roles: show the SQL without running it")
    p.add_argument("--pooler-host", help="roles: Supabase pooler host (aws-0-<region>.pooler.supabase.com); the connection strings written to .env then use it")
    p.add_argument("--pooler-port", type=int, default=5432, help="roles: 5432 = session mode (default, for servers that stay up), 6543 = transaction mode (for serverless functions)")
    p.set_defaults(fn=cmd_db)
    p = sub.add_parser("job", help="a scheduled job: daily (collect) or weekly (full text, newsletter edition)")
    p.add_argument("kind", choices=["daily", "weekly"]); p.add_argument("--profile", default="curt"); p.add_argument("--week-ending", help="weekly: YYYY-MM-DD; default today UTC"); p.set_defaults(fn=cmd_job)
    p = sub.add_parser("gmail", help="one-time Gmail sign-in so the weekly job can create drafts")
    p.add_argument("action", choices=["authorize"]); p.add_argument("--port", type=int, default=0, help="local port for Google's redirect (default: any free port)"); p.set_defaults(fn=cmd_gmail)
    p = sub.add_parser("fix-abstracts", help="repair glued words in stored abstracts (dry run unless --apply)")
    p.add_argument("--apply", action="store_true"); p.set_defaults(fn=cmd_fix_abstracts)
    p = sub.add_parser("seed"); p.add_argument("--profile", default="curt"); p.set_defaults(fn=cmd_seed)
    p = sub.add_parser("plan"); p.add_argument("--profile", default="curt"); p.add_argument("--sources"); p.add_argument("--paths"); p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("collect")
    p.add_argument("--profile", default="curt"); p.add_argument("--date", help="window end (YYYY-MM-DD); default today UTC")
    p.add_argument("--overlap", type=int, help="days before the end date to include (default 3)"); p.add_argument("--sources"); p.add_argument("--paths")
    p.add_argument("--no-enrich", action="store_true"); p.add_argument("--trigger", default="manual"); p.set_defaults(fn=cmd_collect)
    p = sub.add_parser("status"); p.add_argument("--runs", type=int, default=5)
    p.add_argument("--max-age-hours", type=float, default=0, help="exit 1 if no successful collection run finished within this many hours"); p.set_defaults(fn=cmd_status)
    p = sub.add_parser("papers"); p.add_argument("--profile", default="curt"); p.add_argument("--days", type=int, default=7); p.add_argument("--limit", type=int, default=50)
    p.add_argument("--all-types", action="store_true", help="include repository deposits, datasets and book chapters"); p.set_defaults(fn=cmd_papers)
    p = sub.add_parser("providers"); p.set_defaults(fn=cmd_providers)
    p = sub.add_parser("fulltext"); p.add_argument("--profile", default="curt"); p.add_argument("--limit", type=int, default=50)
    p.add_argument("--dry-run", action="store_true", help="show the route per paper without any network call")
    p.add_argument("--no-refresh", action="store_true", help="skip the re-check for PMC IDs and free copies")
    p.add_argument("--verify-stored", action="store_true", help="check stored PDF/plain texts really belong to their papers and remove those that do not (no network)")
    p.add_argument("--recover-abstracts", action="store_true", help="find abstracts in full texts already stored, for papers that have none (no network)")
    p.add_argument("--reapply-policy", action="store_true", help="recompute which stored texts are printable after a policy change (no network)"); p.set_defaults(fn=cmd_fulltext)
    p = sub.add_parser("pmc-lag"); p.add_argument("--profile", default="curt"); p.set_defaults(fn=cmd_pmc_lag)
    p = sub.add_parser("newsletter"); p.add_argument("--profile", default="curt"); nl = p.add_subparsers(dest="nl", required=True)
    b = nl.add_parser("build"); b.add_argument("--week-ending", help="YYYY-MM-DD; default today"); b.add_argument("--rebuild", action="store_true", help="replace an unpublished issue")
    b.add_argument("--no-recheck", action="store_true", help="skip the retraction re-check of recently sent papers")
    b.add_argument("--no-llm", action="store_true", help="use abstract extracts instead of model-written summaries")
    nl.add_parser("list")
    sh = nl.add_parser("show"); sh.add_argument("issue"); sh.add_argument("--format", choices=["text", "html"], default="text")
    pb = nl.add_parser("publish"); pb.add_argument("issue"); pb.add_argument("--to", choices=["file", "gmail"], default="file")
    ms = nl.add_parser("mark-sent"); ms.add_argument("issue")
    p.set_defaults(fn=cmd_newsletter)
    p = sub.add_parser("api"); p.add_argument("action", nargs="?", choices=["serve", "token"], default="serve")
    p.add_argument("--host", default="127.0.0.1"); p.add_argument("--port", type=int, default=8000); p.set_defaults(fn=cmd_api)
    p = sub.add_parser("reclassify"); p.set_defaults(fn=cmd_reclassify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    return args.fn(args, Settings.from_env())


if __name__ == "__main__":
    sys.exit(main())
