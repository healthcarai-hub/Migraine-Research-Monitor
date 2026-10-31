"""The one-time Gmail sign-in command: it asks only for permission to create drafts, writes the token into .env without printing it, and fails safely."""
import stat
import sys
import types

import pytest

from research_agent.cli import main
from research_agent.infrastructure.envfile import set_env_values

TOKEN = "1//refresh-token-that-must-never-be-printed"


class FakeFlow:
    seen = {}

    @classmethod
    def from_client_config(cls, config, scopes):
        cls.seen = {"config": config, "scopes": scopes}
        return cls()

    def run_local_server(self, **kw):
        FakeFlow.seen["run"] = kw
        return types.SimpleNamespace(refresh_token=FakeFlow.token)


@pytest.fixture()
def gmail(monkeypatch, tmp_path):
    """A fake Google library, a temporary project folder (so the real .env is never touched) and credentials in the environment."""
    FakeFlow.token, FakeFlow.seen = TOKEN, {}
    flow_mod = types.ModuleType("google_auth_oauthlib.flow")
    flow_mod.InstalledAppFlow = FakeFlow
    pkg = types.ModuleType("google_auth_oauthlib")
    pkg.flow = flow_mod
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", pkg)
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", flow_mod)
    monkeypatch.setattr("research_agent.settings.PROJECT_ROOT", tmp_path)
    listed = []
    service = types.SimpleNamespace(users=lambda: types.SimpleNamespace(drafts=lambda: types.SimpleNamespace(
        list=lambda **kw: types.SimpleNamespace(execute=lambda: listed.append(kw) or {}))))
    monkeypatch.setattr("research_agent.infrastructure.publishers.build_gmail_service", lambda *a, **k: service)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id-123")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret-456")
    (tmp_path / ".env").write_text("KEEP=me\nGOOGLE_REFRESH_TOKEN=\n# a comment\n", encoding="utf8")
    return tmp_path, listed


def test_a_successful_sign_in_writes_the_token_to_env_and_prints_nothing_secret(gmail, capsys):
    root, listed = gmail
    assert main(["gmail", "authorize"]) == 0
    out = capsys.readouterr()
    assert TOKEN not in out.out + out.err and "client-secret-456" not in out.out + out.err and "written to .env" in out.out
    assert (root / ".env").read_text(encoding="utf8").splitlines() == ["KEEP=me", f"GOOGLE_REFRESH_TOKEN={TOKEN}", "# a comment"]
    assert stat.S_IMODE((root / ".env").stat().st_mode) == 0o600
    assert listed == [{"userId": "me", "maxResults": 1}]                    # draft access was actually checked


def test_it_asks_google_only_for_permission_to_create_drafts_and_for_a_lasting_token(gmail):
    main(["gmail", "authorize"])
    assert FakeFlow.seen["scopes"] == ["https://www.googleapis.com/auth/gmail.compose"]           # not read, not send
    assert FakeFlow.seen["run"]["access_type"] == "offline" and FakeFlow.seen["run"]["prompt"] == "consent"
    assert FakeFlow.seen["config"]["installed"]["client_id"] == "client-id-123"


def test_without_a_client_id_and_secret_it_stops_before_opening_a_browser(gmail, monkeypatch, capsys):
    root, _ = gmail
    monkeypatch.delenv("GOOGLE_CLIENT_ID")
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET")
    monkeypatch.setattr("research_agent.settings.load_dotenv", lambda *a, **k: None)             # do not read a real .env either
    assert main(["gmail", "authorize"]) == 2 and "GOOGLE_CLIENT_ID" in capsys.readouterr().out
    assert FakeFlow.seen == {} and "GOOGLE_REFRESH_TOKEN=\n" in (root / ".env").read_text(encoding="utf8")


def test_if_google_returns_no_refresh_token_nothing_is_written(gmail, capsys):
    root, _ = gmail
    FakeFlow.token = None
    assert main(["gmail", "authorize"]) == 1 and "refresh token" in capsys.readouterr().out
    assert (root / ".env").read_text(encoding="utf8") == "KEEP=me\nGOOGLE_REFRESH_TOKEN=\n# a comment\n"


def test_a_missing_google_library_gives_a_clear_message(gmail, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", None)                            # makes the import fail
    assert main(["gmail", "authorize"]) == 2 and "pip install" in capsys.readouterr().out


def test_env_files_are_edited_in_place_and_locked_to_their_owner(tmp_path):
    f = tmp_path / ".env"
    f.write_text("A=1\n", encoding="utf8")
    set_env_values(f, {"A": "2", "B": "3"})
    assert f.read_text(encoding="utf8") == "A=2\nB=3\n" and stat.S_IMODE(f.stat().st_mode) == 0o600


# ---------------------------------------------------------------- the 'To' address
def test_recipients_are_split_on_commas_and_only_plain_addresses_are_accepted():
    from research_agent.settings import parse_recipients
    assert parse_recipients("a@x.org, b.c+tag@y.co.uk ,") == ("a@x.org", "b.c+tag@y.co.uk") and parse_recipients("") == () and parse_recipients("  ") == ()
    for bad in ("not-an-address", "a@x", "Name <a@x.org>", "a b@x.org", "a@x.org\nBcc: z@y.org", "a@x.org;b@y.org", "@x.org", "a@@x.org"):
        with pytest.raises(ValueError, match="plain email address"):
            parse_recipients(bad)


def test_the_environment_variable_is_read_and_overrides_the_profile(monkeypatch):
    from research_agent.cli import choose_recipients
    from research_agent.settings import Settings
    monkeypatch.setenv("NEWSLETTER_RECIPIENTS", "me@x.org, you@y.org")
    settings = Settings.from_env()
    assert settings.newsletter_recipients == ("me@x.org", "you@y.org")
    profile = types.SimpleNamespace(newsletter=types.SimpleNamespace(recipients=["from-profile@z.org"]))
    assert choose_recipients(settings, profile) == ["me@x.org", "you@y.org"]
    monkeypatch.delenv("NEWSLETTER_RECIPIENTS")
    assert choose_recipients(Settings.from_env(), profile) == ["from-profile@z.org"]            # unset: the profile's list (empty by default)
    profile.newsletter.recipients = []
    assert choose_recipients(Settings.from_env(), profile) == []


def test_a_bad_recipient_setting_stops_the_program_rather_than_writing_a_strange_header(monkeypatch):
    from research_agent.settings import Settings
    monkeypatch.setenv("NEWSLETTER_RECIPIENTS", "ok@x.org, evil@x.org\nBcc: other@y.org")
    with pytest.raises(ValueError):
        Settings.from_env()


def test_the_draft_carries_the_to_header_only_when_there_are_recipients():
    from research_agent.infrastructure.publishers import mime_message
    assert mime_message("S", "<p>h</p>", "t", "k", ["a@x.org", "b@y.org"])["To"] == "a@x.org, b@y.org"
    assert mime_message("S", "<p>h</p>", "t", "k", [])["To"] is None


def test_the_example_env_file_documents_the_variable():
    from research_agent.settings import PROJECT_ROOT
    assert "NEWSLETTER_RECIPIENTS=" in (PROJECT_ROOT / ".env.example").read_text(encoding="utf8")
