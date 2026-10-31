from __future__ import annotations

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import create_engine

from research_agent.infrastructure.db.models import Base
from research_agent.settings import Settings, normalise_db_url

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    x = config.attributes.get("url") or context.get_x_argument(as_dictionary=True).get("url")
    return normalise_db_url(x) if x else Settings.from_env().database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, render_as_batch=True, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
