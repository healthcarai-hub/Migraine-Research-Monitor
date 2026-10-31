from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool


def uses_supabase_pooler(url: str) -> bool:
    return "pooler.supabase.com" in (make_url(url).host or "")


def is_transaction_pooler(url: str) -> bool:
    """Supabase's transaction-mode pooler (port 6543): a connection is lent out per transaction, which suits serverless hosts such as Vercel."""
    u = make_url(url)
    return uses_supabase_pooler(url) and u.port == 6543


def engine_options(url: str, read_only: bool = False) -> dict:
    """Keyword arguments for create_engine. Kept separate so they can be checked without a database."""
    kw: dict = {"future": True}
    if url.startswith("sqlite"):
        kw["connect_args"] = {"check_same_thread": False}
        return kw
    kw["pool_pre_ping"] = True
    args: dict = {}
    if read_only and not uses_supabase_pooler(url):   # the pooler does not take startup options; there, the role's own setting and the per-transaction rule below apply
        args["options"] = "-c default_transaction_read_only=on"
    if is_transaction_pooler(url):
        args["prepare_threshold"] = None              # transaction mode cannot keep server-side prepared statements
        kw["poolclass"] = NullPool                    # the pooler is the pool; do not hold connections inside short-lived functions
    if args:
        kw["connect_args"] = args
    return kw


def make_engine(url: str, *, read_only: bool = False) -> Engine:
    """`read_only=True` makes the database itself refuse every write on these connections (used by the application API), not just the code."""
    engine = create_engine(url, **engine_options(url, read_only))
    if not url.startswith("sqlite") and read_only:
        @event.listens_for(engine, "begin")
        def _read_only_transaction(conn):             # also true behind a transaction pooler, where session settings do not stick
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")   # SQLite ignores foreign keys unless asked
            if read_only:
                dbapi_conn.execute("PRAGMA query_only=ON")
    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    s = factory()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
