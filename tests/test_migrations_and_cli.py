import sqlite3

import pytest

from research_agent import cli
from research_agent.infrastructure.db import migrate
from research_agent.infrastructure.db.models import Base


def tables(path):
    with sqlite3.connect(path) as c:
        return {r[0] for r in c.execute("select name from sqlite_master where type='table'")} - {"alembic_version"}


def test_migration_matches_models_and_round_trips(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.sqlite'}"
    migrate.upgrade(url)
    assert tables(tmp_path / "m.sqlite") == set(Base.metadata.tables)
    migrate.check(url)  # raises if the models drifted from the migration
    migrate.upgrade(url)  # idempotent
    from alembic import command
    command.downgrade(migrate.alembic_config(url), "base")
    assert tables(tmp_path / "m.sqlite") == set()
    migrate.upgrade(url)
    assert tables(tmp_path / "m.sqlite") == set(Base.metadata.tables)


def test_foreign_keys_are_enforced(tmp_path):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    from research_agent.infrastructure.db.session import make_engine
    url = f"sqlite:///{tmp_path / 'f.sqlite'}"
    migrate.upgrade(url)
    with make_engine(url).begin() as c:
        with pytest.raises(IntegrityError):
            c.execute(text("insert into paper_links (paper_id, kind, url, source) values (999, 'doi', 'x', 'y')"))


@pytest.fixture()
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'c.sqlite'}")
    return tmp_path


def test_cli_db_seed_plan_status_papers(cli_env, capsys):
    assert cli.main(["db", "upgrade"]) == 0 and cli.main(["db", "check"]) == 0
    assert cli.main(["seed", "--profile", "curt"]) == 0
    assert "10 journals, 30 researchers, 12 subjects" in capsys.readouterr().out
    assert cli.main(["plan", "--sources", "pubmed", "--paths", "topic"]) == 0
    assert "1 searches" in capsys.readouterr().out
    assert cli.main(["status"]) == 0 and cli.main(["papers"]) == 0


def test_cli_collect_with_a_stub_pipeline(cli_env, capsys, monkeypatch):
    from research_agent.application.ingest import IngestPipeline
    from research_agent.infrastructure.db.session import make_engine, make_session_factory
    from test_pipeline import Fake
    from conftest import rec
    cli.main(["db", "upgrade"])
    seen = {}

    def fake_build(settings):
        fac = make_session_factory(make_engine(settings.database_url))
        pm = Fake("pubmed", lambda sp, w: seen.setdefault("w", w) and [rec("pubmed", "1", pmid="1", doi="10.1/a")])
        names = ("pubmed", "europepmc", "crossref", "openalex", "ctgov")
        src = {n: pm if n == "pubmed" else Fake(n) for n in names}
        return IngestPipeline(fac, src, {})

    monkeypatch.setattr(cli, "build_pipeline", fake_build)
    code = cli.main(["collect", "--date", "2026-10-30", "--paths", "topic", "--trigger", "cron"])
    out = capsys.readouterr().out
    assert code == 0 and "window 2026-10-27..2026-10-30" in out and "new papers 1" in out
    assert cli.main(["collect", "--date", "2026-10-30", "--overlap", "7", "--paths", "topic", "--no-enrich"]) == 0
    assert str(seen["w"]) == "2026-10-27..2026-10-30"  # first call's window; the second used a wider one
    assert cli.main(["papers", "--days", "30"]) == 0
    assert "Migraine prevention" in capsys.readouterr().out
