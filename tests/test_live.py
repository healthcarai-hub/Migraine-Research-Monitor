"""Hits the real services. Run with:  pytest -m live  (needs network; keys are read from .env)."""
from datetime import date

import pytest

from research_agent.bootstrap import build_pipeline
from research_agent.domain.field_config import load_profile
from research_agent.domain.models import DateWindow
from research_agent.infrastructure.db.models import Base
from research_agent.infrastructure.db.session import make_engine, make_session_factory
from research_agent.settings import PROJECT_ROOT, Settings

pytestmark = pytest.mark.live


def test_topic_path_end_to_end_against_real_services():
    settings = Settings.from_env()
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    pipe = build_pipeline(settings, make_session_factory(engine))
    profile, field = load_profile(PROJECT_ROOT / "config", "curt")
    s = pipe.run(profile, field, DateWindow.rolling(date.today(), 3), only_paths={"topic", "trial"})
    assert s.status in ("ok", "partial") and s.new_papers > 0
    assert not [e for e in s.errors], s.errors
