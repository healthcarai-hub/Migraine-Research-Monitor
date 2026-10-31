"""Where a built issue goes for a person to review. Both publishers create *drafts*; nothing here can send an email."""
from __future__ import annotations

import base64
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path
from typing import Any

ISSUE_HEADER = "X-Research-Agent-Issue-Key"
GMAIL_COMPOSE_SCOPE = "https://www.googleapis.com/auth/gmail.compose"  # create and edit drafts; narrower than gmail.modify and cannot read the mailbox


def mime_message(subject: str, html: str, text: str, issue_key: str, recipients: list[str]) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    msg[ISSUE_HEADER] = issue_key
    if recipients:
        msg["To"] = ", ".join(recipients)
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    return msg


class FileDraftPublisher:
    """Writes newsletter.html, newsletter.txt and newsletter.eml for review. Needs no account; overwrites its own previous output."""

    name = "file"

    def __init__(self, out_dir: Path):
        self.out_dir = Path(out_dir)

    def publish(self, subject: str, html: str, text: str, issue_key: str, recipients: list[str], existing_ref: str | None = None) -> str:
        folder = self.out_dir / issue_key
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "newsletter.html").write_text(html, encoding="utf8")
        (folder / "newsletter.txt").write_text(text, encoding="utf8")
        (folder / "newsletter.eml").write_bytes(mime_message(subject, html, text, issue_key, recipients).as_bytes())
        return str(folder)


class GmailDraftPublisher:
    """Creates a Gmail draft, once per issue: an existing draft with the same issue-key header is reused. Ported from the earlier newsletter agent.

    Not yet run against a real Gmail account (no OAuth credentials exist); the logic is covered by tests with a fake Gmail service."""

    name = "gmail"

    def __init__(self, service: Any):
        self.service = service

    @staticmethod
    def _headers(payload: dict) -> dict[str, str]:
        return {str(h.get("name", "")).casefold(): str(h.get("value", "")) for h in payload.get("headers", [])}

    def _find(self, subject: str, issue_key: str) -> str | None:
        q = f'in:drafts subject:"{subject.replace(chr(34), "")}"'
        for d in self.service.users().drafts().list(userId="me", q=q, maxResults=100).execute().get("drafts", []):
            full = self.service.users().drafts().get(userId="me", id=d["id"], format="metadata").execute()
            if self._headers(full.get("message", {}).get("payload", {})).get(ISSUE_HEADER.casefold()) == issue_key:
                return str(d["id"])
        return None

    def publish(self, subject: str, html: str, text: str, issue_key: str, recipients: list[str], existing_ref: str | None = None) -> str:
        if existing_ref:
            try:
                self.service.users().drafts().get(userId="me", id=existing_ref).execute()
                return existing_ref
            except Exception:
                pass
        found = self._find(subject, issue_key)
        if found:
            return found
        raw = base64.urlsafe_b64encode(mime_message(subject, html, text, issue_key, recipients).as_bytes()).decode("ascii")
        return str(self.service.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()["id"])


def build_gmail_service(client_id: str | None, client_secret: str | None, refresh_token: str | None):
    if not all([client_id, client_secret, refresh_token]):
        raise RuntimeError("GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN are required for Gmail drafts")
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError("Gmail support is not installed: pip install 'research-agent[gmail]'") from exc
    creds = Credentials(token=None, refresh_token=refresh_token, token_uri="https://oauth2.googleapis.com/token", client_id=client_id,
                        client_secret=client_secret, scopes=[GMAIL_COMPOSE_SCOPE])
    return build("gmail", "v1", credentials=creds, cache_discovery=False)
