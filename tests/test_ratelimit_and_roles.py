"""Request limits and lockout on the API, and the limited database roles (SQL, connection strings, .env writing)."""
import pytest
from fastapi.testclient import TestClient

from research_agent.bootstrap import build_api
from research_agent.infrastructure.db import roles
from research_agent.interfaces.api.ratelimit import SlidingWindow
from research_agent.settings import Settings
from test_api_search_and_config import ADMIN, H, HA, READ, env  # noqa: F401  (the env fixture)


# ---------------------------------------------------------------- the limiter
class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def test_a_client_gets_its_limit_per_window_and_is_told_how_long_to_wait():
    clock = Clock()
    w = SlidingWindow(3, 60, clock)
    assert [w.check("a") for _ in range(3)] == [0.0, 0.0, 0.0]
    clock.t += 10
    assert w.check("a") == pytest.approx(50.0)                      # the oldest hit leaves the window 60 s after it was made
    assert w.check("b") == 0.0                                      # another client is counted separately
    clock.t += 50.1
    assert w.check("a") == 0.0                                      # the window has moved on


def test_waiting_records_nothing_and_a_limit_of_zero_means_off():
    w = SlidingWindow(1, 60, Clock())
    assert w.wait("a") == 0.0 and w.wait("a") == 0.0
    w.record("a")
    assert w.wait("a") > 0
    off = SlidingWindow(0)
    assert all(off.check("a") == 0.0 for _ in range(1000))


def test_memory_stays_bounded_with_many_distinct_clients():
    w = SlidingWindow(5, 60, Clock(), max_keys=50)
    for i in range(500):
        w.record(f"client-{i}")
    assert len(w._hits) <= 51


# ---------------------------------------------------------------- the API
def test_too_many_requests_get_429_with_retry_after_but_health_stays_open(env):
    c = env[0](api_rate_limit_per_minute=5)
    codes = [c.get("/v1/stats", headers=H).status_code for _ in range(7)]
    assert codes == [200] * 5 + [429] * 2
    r = c.get("/v1/stats", headers=H)
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1 and r.headers["cache-control"] == "no-store"
    assert all(c.get("/health").status_code == 200 for _ in range(20))


def test_clients_are_told_apart_only_by_the_header_the_host_is_known_to_set(env):
    named = env[0](api_rate_limit_per_minute=2, api_client_ip_header="X-Real-IP")
    for ip in ("1.1.1.1", "2.2.2.2"):
        assert [named.get("/v1/stats", headers={**H, "X-Real-IP": ip}).status_code for _ in range(2)] == [200, 200]
    assert named.get("/v1/stats", headers={**H, "X-Real-IP": "1.1.1.1"}).status_code == 429
    assert named.get("/v1/stats", headers={**H, "X-Forwarded-For": "7.7.7.7"}).status_code == 200      # a different header is ignored: no address from it, so the connecting one is used
    unnamed = env[0](api_rate_limit_per_minute=2)                                                      # no header configured: a spoofed one buys nothing
    codes = [unnamed.get("/v1/stats", headers={**H, "X-Real-IP": f"10.0.0.{i}"}).status_code for i in range(3)]
    assert codes == [200, 200, 429]


def test_a_list_header_uses_its_last_entry_and_whoami_shows_what_the_server_sees(env):
    c = env[0](api_client_ip_header="X-Forwarded-For")
    seen = c.get("/v1/whoami", headers={**H, "X-Forwarded-For": "6.6.6.6, 9.9.9.9, 3.3.3.3"}).json()
    assert seen["client"] == "3.3.3.3" and seen["header_setting"] == "X-Forwarded-For" and seen["header_value"] == "6.6.6.6, 9.9.9.9, 3.3.3.3" and seen["connecting_address"]
    plain = env[0]().get("/v1/whoami", headers={**H, "X-Forwarded-For": "6.6.6.6"}).json()
    assert plain["client"] == plain["connecting_address"] and plain["header_setting"] is None
    assert env[0]().get("/v1/whoami").status_code == 401


def test_repeated_wrong_tokens_are_slowed_to_a_crawl_but_a_right_token_always_works(env):
    c = env[0](api_auth_failures_per_minute=3)
    assert [c.get("/v1/stats", headers={"Authorization": "Bearer wrong"}).status_code for _ in range(3)] == [401, 401, 401]
    r = c.get("/v1/stats", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1                                  # further wrong tokens are refused outright
    assert c.get("/v1/stats").status_code == 429                                                        # so is a missing one
    assert c.get("/v1/stats", headers=H).status_code == 200                                             # real users are never locked out by someone else's failures
    assert c.patch("/v1/newsletter/config", json={"journals": {"cephalalgia": False}}, headers=HA).status_code == 200
    assert c.patch("/v1/newsletter/config", json={"journals": {"cephalalgia": True}}, headers=H).status_code == 403   # a read token still cannot change settings
    assert c.get("/health").status_code == 200


def test_right_tokens_never_lock_anyone_out_and_the_lockout_can_be_switched_off(env):
    c = env[0](api_auth_failures_per_minute=3)
    assert all(c.get("/v1/stats", headers=H).status_code == 200 for _ in range(10))
    off = env[0](api_auth_failures_per_minute=0)
    assert [off.get("/v1/stats", headers={"Authorization": "Bearer wrong"}).status_code for _ in range(20)] == [401] * 20
    assert off.get("/v1/stats", headers=H).status_code == 200


def test_the_data_routes_use_the_api_database_url_when_it_is_set(env, tmp_path):
    other = f"sqlite:///{tmp_path / 'empty.sqlite'}"                 # the owner connection points somewhere else entirely
    app = build_api(Settings(database_url=other, api_database_url=env[3], api_tokens=(READ,)))
    assert TestClient(app).get("/v1/stats", headers=H).json()["relevant_papers"] == 5


def test_changes_are_written_over_the_admin_database_url(env, tmp_path):
    url = env[3]
    app = build_api(Settings(database_url=f"sqlite:///{tmp_path / 'e.sqlite'}", api_database_url=url, api_admin_database_url=url, api_tokens=(READ,), api_admin_tokens=(ADMIN,)))
    c = TestClient(app)
    assert c.put("/v1/newsletter/config/sources/crossref", json={"enabled": False}, headers=HA).json()["switched_off"] == {"sources": ["crossref"]}


# ---------------------------------------------------------------- the database roles
def sql():
    return roles.role_statements("A" * 24, "B" * 24)


def test_the_read_role_can_only_read_the_tables_the_api_shows():
    joined = "\n".join(s for s in sql() if "curt_api_read" in s)
    assert ("GRANT SELECT ON papers, profile_matches, paper_fulltext, paper_summaries, paper_links, paper_hits, issues, issue_items, runs, newsletter_selections "
            "TO curt_api_read") in joined
    assert "default_transaction_read_only = on" in joined and "SUPERUSER" not in joined and "BYPASSRLS" not in joined
    assert "INSERT" not in joined and "UPDATE" not in joined and "DELETE" not in joined
    assert joined.count("CREATE POLICY curt_api_read") == len(roles.READ_TABLES) and "FOR SELECT" in joined and "FOR ALL" not in joined


def test_the_config_role_can_only_touch_the_newsletter_switches_table():
    joined = "\n".join(s for s in sql() if "curt_api_config" in s)
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON newsletter_selections TO curt_api_config" in joined
    assert "papers" not in joined and "profile_matches" not in joined
    assert "CREATE POLICY curt_api_config ON newsletter_selections FOR ALL TO curt_api_config USING (true) WITH CHECK (true)" in joined
    assert "BYPASSRLS" not in joined and "default_transaction_read_only = off" in joined


def test_no_statement_hands_out_wide_rights():
    for s in sql():
        assert "SUPERUSER" not in s and "BYPASSRLS" not in s and "CREATEROLE" not in s
        assert "ALL PRIVILEGES" not in s and "GRANT ALL" not in s


def test_passwords_that_could_break_a_statement_are_refused():
    for bad in ("short", "x' ; drop table papers; --" + "a" * 20, "a" * 24 + "'", "a b" * 10):
        with pytest.raises(ValueError):
            roles.role_statements(bad, "B" * 24)


def test_the_preview_shows_a_placeholder_never_a_password():
    text = roles.preview_sql()
    assert "RANDOM_PASSWORD_SET_AT_RUN_TIME" in text and "CREATE POLICY" in text and text.rstrip().endswith(";")


def test_role_urls_keep_the_pooler_project_suffix_and_the_rest_of_the_address():
    direct = roles.role_url("postgresql+psycopg://postgres:ownerpw@db.abc.supabase.co:5432/postgres", "curt_api_read", "newpw")
    assert direct == "postgresql+psycopg://curt_api_read:newpw@db.abc.supabase.co:5432/postgres" and "ownerpw" not in direct
    pooled = roles.role_url("postgresql://postgres.abcref:ownerpw@aws-0-eu.pooler.supabase.com:5432/postgres", "curt_api_config", "p2")
    assert pooled == "postgresql://curt_api_config.abcref:p2@aws-0-eu.pooler.supabase.com:5432/postgres"


def test_env_values_are_set_in_place_and_everything_else_is_kept(tmp_path):
    f = tmp_path / ".env"
    f.write_text("A=1\n# API_DATABASE_URL=old comment\nAPI_DATABASE_URL=old\nB=2\n", encoding="utf8")
    roles.set_env_values(f, {"API_DATABASE_URL": "new", "API_ADMIN_DATABASE_URL": "added"})
    assert f.read_text(encoding="utf8").splitlines() == ["A=1", "# API_DATABASE_URL=old comment", "API_DATABASE_URL=new", "B=2", "API_ADMIN_DATABASE_URL=added"]
    roles.set_env_values(tmp_path / "fresh.env", {"X": "y"})
    assert (tmp_path / "fresh.env").read_text(encoding="utf8") == "X=y\n"


def test_roles_are_only_for_postgres(tmp_path):
    with pytest.raises(ValueError, match="Postgres"):
        roles.apply_roles("sqlite:///x.sqlite", tmp_path / ".env")
    assert not (tmp_path / ".env").exists()


class _Conn:
    def __init__(self, fail_on=None, bad_roles=()):
        self.fail_on, self.bad_roles, self.ran = fail_on, list(bad_roles), []

    def exec_driver_sql(self, stmt, params=None):
        from sqlalchemy.exc import DBAPIError
        if self.fail_on and stmt.startswith(self.fail_on):
            raise DBAPIError(stmt, None, Exception("permission denied to alter role"))
        self.ran.append(stmt)
        rows = self.bad_roles
        return type("R", (), {"scalars": lambda self_: type("S", (), {"all": lambda s_: rows})()})()


class _Engine:
    def __init__(self, conn):
        self.conn = conn

    def begin(self):
        from contextlib import contextmanager
        @contextmanager
        def cm():
            yield self.conn
        return cm()

    def dispose(self):
        pass


def test_a_refused_step_reports_the_database_message_and_never_the_password(monkeypatch, tmp_path):
    monkeypatch.setattr(roles, "create_engine", lambda *a, **k: _Engine(_Conn(fail_on="ALTER ROLE")))
    with pytest.raises(RuntimeError) as exc:
        roles.apply_roles("postgresql://postgres:x@h/db", tmp_path / ".env")
    assert "permission denied to alter role" in str(exc.value) and "PASSWORD" not in str(exc.value) and not (tmp_path / ".env").exists()


def test_roles_with_elevated_rights_are_refused_and_nothing_is_written(monkeypatch, tmp_path):
    monkeypatch.setattr(roles, "create_engine", lambda *a, **k: _Engine(_Conn(bad_roles=["curt_api_read"])))
    with pytest.raises(RuntimeError, match="elevated rights"):
        roles.apply_roles("postgresql://postgres:x@h/db", tmp_path / ".env")
    assert not (tmp_path / ".env").exists()


def test_a_good_run_writes_both_urls_with_the_new_passwords(monkeypatch, tmp_path):
    conn = _Conn()
    monkeypatch.setattr(roles, "create_engine", lambda *a, **k: _Engine(conn))
    roles.apply_roles("postgresql://postgres.ref:ownerpw@pool.example:5432/postgres", tmp_path / ".env")
    env = dict(line.split("=", 1) for line in (tmp_path / ".env").read_text(encoding="utf8").splitlines())
    assert env["API_DATABASE_URL"].startswith("postgresql://curt_api_read.ref:") and env["API_ADMIN_DATABASE_URL"].startswith("postgresql://curt_api_config.ref:")
    assert "ownerpw" not in "".join(env.values()) and len(conn.ran) > 10
