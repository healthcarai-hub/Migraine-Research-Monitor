"""Deployment on Railway: serverless-safe database options, pooler connection strings, the Docker image, the scheduled jobs, the staleness check."""
import re
import signal
import time
from argparse import Namespace
from datetime import date, datetime, timedelta, timezone

import pytest
import yaml
from sqlalchemy.pool import NullPool

from research_agent import jobs
from research_agent.cli import cmd_status, main
from research_agent.infrastructure.db import models as m
from research_agent.infrastructure.db.models import Base
from research_agent.infrastructure.db.roles import project_ref, role_url
from research_agent.infrastructure.db.session import engine_options, make_engine, make_session_factory, session_scope
from research_agent.settings import PROJECT_ROOT, Settings

DIRECT = "postgresql+psycopg://curt_api_read:pw@db.abcdefgh.supabase.co:5432/postgres"
SESSION_POOL = "postgresql+psycopg://curt_api_read.abcdefgh:pw@aws-0-eu-west-1.pooler.supabase.com:5432/postgres"
TX_POOL = "postgresql+psycopg://curt_api_read.abcdefgh:pw@aws-0-eu-west-1.pooler.supabase.com:6543/postgres"


@pytest.fixture(autouse=True)
def _no_real_dotenv(monkeypatch):
    """Settings.from_env() also reads the project's real .env; these tests must depend only on what they set themselves."""
    monkeypatch.setattr("research_agent.settings.load_dotenv", lambda *a, **k: None)


# ---------------------------------------------------------------- database options
def test_sqlite_options_are_unchanged():
    assert engine_options("sqlite:///x.sqlite", True) == {"future": True, "connect_args": {"check_same_thread": False}}


def test_the_direct_connection_asks_for_read_only_sessions_only_when_read_only():
    assert engine_options(DIRECT, True)["connect_args"] == {"options": "-c default_transaction_read_only=on"}
    assert "connect_args" not in engine_options(DIRECT, False) and "poolclass" not in engine_options(DIRECT, True)


def test_the_session_pooler_gets_no_startup_options_but_keeps_prepared_statements_and_its_pool():
    kw = engine_options(SESSION_POOL, True)
    assert "connect_args" not in kw and "poolclass" not in kw


def test_the_transaction_pooler_gets_no_prepared_statements_no_local_pool_and_no_startup_options():
    for read_only in (True, False):
        kw = engine_options(TX_POOL, read_only)
        assert kw["connect_args"] == {"prepare_threshold": None} and kw["poolclass"] is NullPool


def test_a_read_only_sqlite_engine_still_works(tmp_path):
    with make_engine(f"sqlite:///{tmp_path / 'a.sqlite'}", read_only=True).connect() as c:
        assert c.exec_driver_sql("select 1").scalar() == 1


# ---------------------------------------------------------------- pooler connection strings
def test_project_reference_comes_from_the_pooler_user_or_the_direct_host():
    assert project_ref("postgresql://postgres.abcdefgh:x@aws-0-eu.pooler.supabase.com:5432/postgres") == "abcdefgh"
    assert project_ref("postgresql://postgres:x@db.abcdefgh.supabase.co:5432/postgres") == "abcdefgh"
    assert project_ref("postgresql://postgres:x@localhost/db") == ""


def test_role_urls_point_at_the_session_pooler_by_default_and_can_use_the_transaction_pooler():
    owner = "postgresql+psycopg://postgres:ownerpw@db.abcdefgh.supabase.co:5432/postgres"
    url = role_url(owner, "curt_api_read", "newpw", pooler_host="aws-0-eu-west-1.pooler.supabase.com")
    assert url == "postgresql+psycopg://curt_api_read.abcdefgh:newpw@aws-0-eu-west-1.pooler.supabase.com:5432/postgres" and "ownerpw" not in url
    tx = role_url(owner, "curt_api_config", "p", pooler_host="aws-0-eu-west-1.pooler.supabase.com", pooler_port=6543)
    assert tx.endswith("@aws-0-eu-west-1.pooler.supabase.com:6543/postgres") and "curt_api_config.abcdefgh" in tx


def test_a_pooler_url_needs_a_known_project_reference():
    with pytest.raises(ValueError, match="project reference"):
        role_url("postgresql://postgres:x@localhost/db", "curt_api_read", "p", pooler_host="h.pooler.supabase.com")


# ---------------------------------------------------------------- the Docker image and CI
def read(name):
    return (PROJECT_ROOT / name).read_text(encoding="utf8")


def test_the_image_installs_the_project_with_pdf_support_and_runs_as_a_normal_user():
    d = read("Dockerfile")
    assert d.startswith("# One image") and "FROM python:3.12" in d and "poppler-utils" in d
    assert 'pip install -e ".[gmail]"' in d                                          # editable: config/ and migrations/ are found next to the code
    for folder in ("src", "config", "migrations"):
        assert f"COPY {folder} ./{folder}" in d
    assert "USER app" in d and d.index("USER app") < d.index("CMD")
    assert ".env" not in d.replace(".env.example", "")


def test_the_image_starts_the_api_on_the_hosts_port_on_all_addresses():
    cmd = [ln for ln in read("Dockerfile").splitlines() if ln.startswith("CMD")][0]
    assert "api serve" in cmd and "--host 0.0.0.0" in cmd and "${PORT:-8000}" in cmd and "exec" in cmd


def test_secrets_and_local_data_are_never_copied_into_the_image():
    lines = read(".dockerignore").splitlines()
    for must in (".env", ".env.*", "data/", "*.sqlite", "outputs/", ".git/", ".venv/", "tests/"):
        assert must in lines


def test_ci_runs_the_tests_on_every_push_with_pinned_actions():
    ci = yaml.safe_load(read(".github/workflows/ci.yml"))
    steps = next(iter(ci["jobs"].values()))["steps"]
    assert any("pytest" in s.get("run", "") for s in steps) and ci["permissions"] == {"contents": "read"}
    assert all(re.search(r"@v\d+$", s["uses"]) for s in steps if "uses" in s)
    assert [p.name for p in (PROJECT_ROOT / ".github/workflows").glob("*.yml")] == ["ci.yml"]       # the schedule lives on the host, not here


def test_the_new_settings_are_documented_and_the_old_proxy_switch_is_gone():
    example = read(".env.example")
    for name in ("API_CLIENT_IP_HEADER", "JOB_OVERLAP_DAYS", "JOB_FULLTEXT_LIMIT", "API_DATABASE_URL", "API_ADMIN_DATABASE_URL"):
        assert name in example
    assert "API_TRUST_PROXY" not in example


def test_the_job_settings_are_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("JOB_OVERLAP_DAYS", "5")
    monkeypatch.setenv("JOB_FULLTEXT_LIMIT", "250")
    monkeypatch.setenv("API_CLIENT_IP_HEADER", " X-Real-IP ")
    s = Settings.from_env()
    assert (s.job_overlap_days, s.job_fulltext_limit, s.api_client_ip_header) == (5, 250, "X-Real-IP")
    monkeypatch.delenv("JOB_OVERLAP_DAYS")
    monkeypatch.delenv("JOB_FULLTEXT_LIMIT")
    monkeypatch.delenv("API_CLIENT_IP_HEADER")
    s = Settings.from_env()
    assert (s.job_overlap_days, s.job_fulltext_limit) == (3, 500)


# ---------------------------------------------------------------- the scheduled jobs
def test_the_daily_job_updates_the_schema_and_profile_then_collects_with_the_configured_overlap():
    steps = jobs.daily_steps(Settings(database_url="sqlite://", job_overlap_days=4), "curt")
    assert [argv[0] for _, argv in steps] == ["db", "seed", "collect"]
    assert steps[0][1] == ["db", "upgrade"] and steps[2][1] == ["collect", "--profile", "curt", "--trigger", "cron", "--overlap", "4"]


def test_the_weekly_job_checks_freshness_first_then_full_text_then_builds_and_only_drafts_with_gmail_set_up():
    plain = jobs.weekly_steps(Settings(database_url="sqlite://", job_fulltext_limit=300), "curt", "2026-10-30")
    assert [lbl for lbl, _ in plain] == ["freshness guard", "database schema", "full text", "build the edition"]
    assert plain[0][1] == ["status", "--runs", "1", "--max-age-hours", "36"] and plain[2][1] == ["fulltext", "--profile", "curt", "--limit", "300"]
    assert plain[3][1] == ["newsletter", "--profile", "curt", "build", "--week-ending", "2026-10-30"]
    gmail = jobs.weekly_steps(Settings(database_url="sqlite://", google_refresh_token="x"), "curt", "2026-10-30")
    assert gmail[-1][1] == ["newsletter", "--profile", "curt", "publish", "curt-2026-10-30", "--to", "gmail"] and len(gmail) == 5


def test_every_step_of_both_jobs_is_a_real_command_with_valid_arguments():
    from research_agent.cli import build_parser
    steps = jobs.daily_steps(Settings(database_url="sqlite://"), "curt") + jobs.weekly_steps(Settings(database_url="sqlite://", google_refresh_token="x"), "curt", "2026-10-30")
    parser = build_parser()
    for _, argv in steps:
        parser.parse_args(argv)                                   # raises SystemExit if a step no longer matches the command line


def test_a_job_stops_at_the_first_failing_step_and_returns_its_exit_code():
    ran, lines = [], []
    def fake_main(argv):
        ran.append(argv[0])
        return 3 if argv[0] == "seed" else 0
    code = jobs.run_steps(jobs.daily_steps(Settings(database_url="sqlite://"), "curt"), fake_main, 1, lines.append)
    assert code == 3 and ran == ["db", "seed"] and "profile failed (exit 3)" in lines[-1]


def test_a_command_that_exits_instead_of_returning_counts_as_a_failure():
    code = jobs.run_steps([("x", ["a"]), ("y", ["b"])], lambda argv: (_ for _ in ()).throw(SystemExit(2)), 1, lambda _l: None)
    assert code == 2
    assert jobs.run_steps([("x", ["a"])], lambda argv: (_ for _ in ()).throw(SystemExit("bad")), 1, lambda _l: None) == 2


def test_a_job_that_finishes_returns_zero_and_logs_each_step():
    lines = []
    assert jobs.run_steps([("one", ["a", "1"]), ("two", ["b"])], lambda argv: 0, 1, lines.append) == 0
    assert "one: research-agent a 1" in lines[0] and "two: research-agent b" in lines[1] and "2 steps ok" in lines[-1]


def test_a_job_that_hangs_is_stopped_and_the_alarm_is_cleaned_up():
    lines = []
    before = signal.getsignal(signal.SIGALRM)
    started = time.monotonic()
    code = jobs.run_steps([("slow", ["x"])], lambda argv: time.sleep(5) or 0, 0.01, lines.append)     # 0.6 s limit
    assert code == 1 and time.monotonic() - started < 3 and "ran longer than" in lines[-1]
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0) and signal.getsignal(signal.SIGALRM) == before


def test_the_job_command_is_wired_to_the_cli(monkeypatch):
    seen = {}
    monkeypatch.setattr("research_agent.jobs.run_job", lambda kind, settings, profile, week, main_fn, out=print: seen.update(kind=kind, profile=profile, week=week) or 0)
    assert main(["job", "weekly", "--week-ending", "2026-10-30"]) == 0
    assert seen == {"kind": "weekly", "profile": "curt", "week": "2026-10-30"}
    assert main(["job", "daily"]) == 0 and seen["kind"] == "daily" and seen["week"] == datetime.now(timezone.utc).date().isoformat()
    with pytest.raises(SystemExit):
        main(["job", "hourly"])


# ---------------------------------------------------------------- staleness check
@pytest.fixture()
def db(tmp_path):
    url = f"sqlite:///{tmp_path / 's.sqlite'}"
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    return Settings(database_url=url), make_session_factory(engine)


def add_run(factory, hours_ago, status="ok", kind="collect"):
    now = datetime.now(timezone.utc)
    with session_scope(factory) as s:
        s.add(m.Run(kind=kind, trigger="t", window_start=date.today(), window_end=date.today(), status=status,
                    started_at=now - timedelta(hours=hours_ago + 0.1), finished_at=now - timedelta(hours=hours_ago)))


def status(settings, factory, monkeypatch, max_age_hours=0):
    monkeypatch.setattr("research_agent.cli.session_factory", lambda _s: factory)
    return cmd_status(Namespace(runs=3, max_age_hours=max_age_hours), settings)


def test_the_check_passes_when_a_collection_finished_recently(db, monkeypatch, capsys):
    settings, factory = db
    add_run(factory, 5)
    assert status(settings, factory, monkeypatch, 36) == 0 and "fresh" in capsys.readouterr().out


def test_the_check_fails_when_collection_is_old_missing_or_only_failed(db, monkeypatch, capsys):
    settings, factory = db
    assert status(settings, factory, monkeypatch, 36) == 1 and "no successful collection" in capsys.readouterr().out
    add_run(factory, 2, status="failed")
    add_run(factory, 50, status="ok")
    assert status(settings, factory, monkeypatch, 36) == 1 and "50 hours ago" in capsys.readouterr().out
    add_run(factory, 30, status="partial")                  # a partial run counts as collected
    assert status(settings, factory, monkeypatch, 36) == 0


def test_without_a_limit_status_only_reports(db, monkeypatch):
    settings, factory = db
    assert status(settings, factory, monkeypatch) == 0
