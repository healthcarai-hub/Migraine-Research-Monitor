"""Two limited Postgres roles for the application API, so the web-facing process never holds the database owner's credentials.

  curt_api_read    reads only the tables the API shows, and its sessions are read-only
  curt_api_config  reads and writes only `newsletter_selections` (the newsletter's on/off switches)

Row level security is already on for every table (the migrations do that, for Supabase's REST interface), and a role with no policy sees nothing, so each
grant comes with an explicit policy for that role. Nothing here uses BYPASSRLS or superuser rights. Passwords are random, put straight into the git-ignored
.env as connection strings, and never printed. Running it again changes the passwords.
"""
from __future__ import annotations

import re
import secrets
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from ..envfile import set_env_values

READ_ROLE, CONFIG_ROLE = "curt_api_read", "curt_api_config"
CONFIG_TABLE = "newsletter_selections"
# every table the query layer and the settings view read
READ_TABLES = ("papers", "profile_matches", "paper_fulltext", "paper_summaries", "paper_links", "paper_hits", "issues", "issue_items", "runs", CONFIG_TABLE)
_SAFE_PASSWORD = re.compile(r"^[A-Za-z0-9_-]{20,}$")


def new_password() -> str:
    return secrets.token_urlsafe(24)


def _role_statements(role: str, password: str, grants: str, tables: tuple[str, ...], policy_for: str, read_only_sessions: bool) -> list[str]:
    if not _SAFE_PASSWORD.match(password):
        raise ValueError("password has characters that are not safe to put in a statement")
    out = [
        f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN CREATE ROLE {role} LOGIN; END IF; END $$",
        f"ALTER ROLE {role} WITH LOGIN PASSWORD '{password}'",    # a new role is already no superuser, no bypass of row security; apply_roles checks that afterwards
        f"ALTER ROLE {role} SET default_transaction_read_only = {'on' if read_only_sessions else 'off'}",
        f"GRANT USAGE ON SCHEMA public TO {role}",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}",
        f"GRANT {grants} ON {', '.join(tables)} TO {role}",
    ]
    for t in tables:
        out.append(f"DROP POLICY IF EXISTS {role} ON {t}")
        extra = " WITH CHECK (true)" if policy_for != "SELECT" else ""
        out.append(f"CREATE POLICY {role} ON {t} FOR {policy_for} TO {role} USING (true){extra}")
    return out


def role_statements(read_password: str, config_password: str) -> list[str]:
    return (_role_statements(READ_ROLE, read_password, "SELECT", READ_TABLES, "SELECT", True)
            + _role_statements(CONFIG_ROLE, config_password, "SELECT, INSERT, UPDATE, DELETE", (CONFIG_TABLE,), "ALL", False))


def preview_sql() -> str:
    """The statements `db roles` runs, with a placeholder where each random password goes."""
    placeholder = "RANDOM_PASSWORD_SET_AT_RUN_TIME"
    return ";\n".join(role_statements(placeholder, placeholder)) + ";"


def project_ref(owner_url: str) -> str:
    """The Supabase project reference: the suffix of a pooler user ('postgres.<ref>'), or the middle of the direct host ('db.<ref>.supabase.co')."""
    u = make_url(owner_url)
    suffix = (u.username or "").partition(".")[2]
    if suffix:
        return suffix
    m = re.match(r"^db\.([a-z0-9]+)\.supabase\.co$", u.host or "")
    return m.group(1) if m else ""


def role_url(owner_url: str, role: str, password: str, *, pooler_host: str | None = None, pooler_port: int = 5432) -> str:
    """The owner's connection string with another user. Supabase's pooler names users '<role>.<project ref>'. With `pooler_host` the string points at the
    pooler (default port 5432, session mode, for servers that stay up; 6543 is transaction mode for serverless functions). The direct host is IPv6 only, so hosts without IPv6 need the pooler."""
    u = make_url(owner_url)
    ref = project_ref(owner_url)
    through_pooler = bool(pooler_host) or "." in (u.username or "")      # only pooler users carry the project suffix; a direct connection uses the plain role name
    if pooler_host:
        if not ref:
            raise ValueError("cannot work out the Supabase project reference from this connection string")
        u = u.set(host=pooler_host, port=pooler_port)
    return u.set(username=f"{role}.{ref}" if through_pooler and ref else role, password=password).render_as_string(hide_password=False)


def apply_roles(owner_url: str, env_path: Path, pooler_host: str | None = None, pooler_port: int = 5432) -> None:
    if not owner_url.startswith("postgres"):
        raise ValueError("roles are for Postgres (Supabase); this database is not")
    if pooler_host:
        role_url(owner_url, READ_ROLE, "x" * 24, pooler_host=pooler_host, pooler_port=pooler_port)     # fail early, before anything is changed, if the project reference is unknown
    read_pw, config_pw = new_password(), new_password()
    engine = create_engine(owner_url, future=True)
    try:
        with engine.begin() as conn:
            for stmt in role_statements(read_pw, config_pw):
                try:
                    conn.exec_driver_sql(stmt)
                except DBAPIError as exc:           # the statement text holds a password, so show only what the database said
                    raise RuntimeError(f"the database refused a step ({stmt.split(' ')[0]} ...): {str(exc.orig).strip()}") from None
            bad = conn.exec_driver_sql("SELECT rolname FROM pg_roles WHERE rolname IN (%s, %s) AND (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication)",
                                       (READ_ROLE, CONFIG_ROLE)).scalars().all()
            if bad:                                  # raising rolls the whole transaction back
                raise RuntimeError(f"role(s) {', '.join(bad)} have elevated rights; nothing was changed")
    finally:
        engine.dispose()
    set_env_values(env_path, {"API_DATABASE_URL": role_url(owner_url, READ_ROLE, read_pw, pooler_host=pooler_host, pooler_port=pooler_port),
                              "API_ADMIN_DATABASE_URL": role_url(owner_url, CONFIG_ROLE, config_pw, pooler_host=pooler_host, pooler_port=pooler_port)})
