from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from research_agent.domain.field_config import load_profile
from research_agent.domain.models import PaperRecord
from research_agent.infrastructure.db.models import Base
from research_agent.infrastructure.db.session import make_engine, make_session_factory
from research_agent.settings import PROJECT_ROOT

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf8"))


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf8")


@pytest.fixture(scope="session")
def curt():
    return load_profile(PROJECT_ROOT / "config", "curt")


@pytest.fixture()
def factory():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def rec(source="pubmed", sid="1", **kw) -> PaperRecord:
    base = dict(title="Migraine prevention with a new agent in adults: a trial", abstract="Migraine days fell.", languages=["eng"],
                entry_date=date(2026, 10, 30))
    base.update(kw)
    return PaperRecord(source=source, source_id=sid, **base)
