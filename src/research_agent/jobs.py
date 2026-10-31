"""The scheduled jobs, run as `research-agent job daily` and `research-agent job weekly` (one cron service each on the host).

A job is a fixed list of the CLI's own commands run in order. It stops at the first one that fails and exits non-zero, so the host marks the run failed and
nobody waits for a newsletter built from bad data. Hosts such as Railway do not stop a cron run that hangs, so each job also has a hard time limit.

daily   database schema -> profile -> collect (looks back JOB_OVERLAP_DAYS, default 3)
weekly  freshness guard (a successful collection within 36 hours) -> schema -> full text (JOB_FULLTEXT_LIMIT papers) -> build the edition
        -> create a Gmail draft when Gmail credentials are set. It never sends anything; the edition itself is stored in the database and served by the API.
"""
from __future__ import annotations

import signal
from datetime import datetime, timezone
from typing import Callable

from .settings import Settings

DAILY_TIMEOUT_MINUTES = 45
WEEKLY_TIMEOUT_MINUTES = 150
FRESHNESS_HOURS = 36

Step = tuple[str, list[str]]


class JobTimeout(Exception):
    pass


def _on_alarm(*_):
    raise JobTimeout()


def daily_steps(settings: Settings, profile: str) -> list[Step]:
    return [("database schema", ["db", "upgrade"]),
            ("profile", ["seed", "--profile", profile]),
            ("collect", ["collect", "--profile", profile, "--trigger", "cron", "--overlap", str(settings.job_overlap_days)])]


def weekly_steps(settings: Settings, profile: str, week_ending: str) -> list[Step]:
    steps: list[Step] = [("freshness guard", ["status", "--runs", "1", "--max-age-hours", str(FRESHNESS_HOURS)]),
                         ("database schema", ["db", "upgrade"]),
                         ("full text", ["fulltext", "--profile", profile, "--limit", str(settings.job_fulltext_limit)]),
                         ("build the edition", ["newsletter", "--profile", profile, "build", "--week-ending", week_ending])]
    if settings.google_refresh_token:
        steps.append(("Gmail draft", ["newsletter", "--profile", profile, "publish", f"{profile}-{week_ending}", "--to", "gmail"]))
    return steps


def run_steps(steps: list[Step], main: Callable[[list[str]], int], timeout_minutes: float, out: Callable[[str], None] = print) -> int:
    stamp = lambda: datetime.now(timezone.utc).strftime("%H:%M:%S")
    previous = signal.signal(signal.SIGALRM, _on_alarm)
    signal.setitimer(signal.ITIMER_REAL, timeout_minutes * 60)
    try:
        for label, argv in steps:
            out(f"[{stamp()}] {label}: research-agent {' '.join(argv)}")
            try:
                code = main(argv)
            except SystemExit as exc:                # a command that rejects its arguments exits instead of returning
                code = exc.code if isinstance(exc.code, int) else 2
            if code:
                out(f"[{stamp()}] {label} failed (exit {code}); the job stops here")
                return int(code)
        out(f"[{stamp()}] job finished: {len(steps)} steps ok")
        return 0
    except JobTimeout:
        out(f"[{stamp()}] job stopped: it ran longer than {timeout_minutes:g} minutes")
        return 1
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def run_job(kind: str, settings: Settings, profile: str, week_ending: str, main: Callable[[list[str]], int], out: Callable[[str], None] = print) -> int:
    if kind == "daily":
        return run_steps(daily_steps(settings, profile), main, DAILY_TIMEOUT_MINUTES, out)
    return run_steps(weekly_steps(settings, profile, week_ending), main, WEEKLY_TIMEOUT_MINUTES, out)
