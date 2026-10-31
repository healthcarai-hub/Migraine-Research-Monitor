from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from ...settings import PROJECT_ROOT


def alembic_config(url: str) -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    cfg.attributes["url"] = url
    return cfg


def upgrade(url: str, revision: str = "head") -> None:
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(alembic_config(url), revision)


def check(url: str) -> None:
    """Raises if the models and the migrated schema differ."""
    command.check(alembic_config(url))
