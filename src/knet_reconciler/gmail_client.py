"""Gmail API client. Read-only scope, OAuth desktop flow, with email caching.

Idempotent: messages already in the local DB are skipped, so re-running fetch
costs one Gmail `messages.list` call plus zero `messages.get` calls for cached
IDs.
"""
from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Iterable, Iterator

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from .db import Email

log = logging.getLogger(__name__)

# `gmail.modify` lets us apply labels to messages (for the KNET-Missing folder).
# It allows changing labels/metadata but NOT deleting messages or modifying body
# content — strictly the smallest scope that supports labelling.
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


@dataclass
class FetchedMessage:
    gmail_id: str
    thread_id: str
    from_address: str | None
    from_domain: str | None
    subject: str | None
    received_at: datetime | None
    snippet: str | None
    raw_html: str | None
    raw_text: str | None


def _domain_of(addr: str | None) -> str | None:
    if not addr:
        return None
    _, email = parseaddr(addr)
    if "@" not in email:
        return None
    return email.split("@", 1)[1].lower().strip()


def _header(headers: list[dict], name: str) -> str | None:
    name_l = name.lower()
    for h in headers:
        if h.get("name", "").lower() == name_l:
            return h.get("value")
    return None


def _decode_b64(data: str | None) -> bytes:
    if not data:
        return b""
    # Gmail uses URL-safe base64 without padding.
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _extract_bodies(payload: dict) -> tuple[str | None, str | None]:
    """Return (html, text) by walking the MIME tree."""
    html_parts: list[str] = []
    text_parts: list[str] = []

    def walk(part: dict):
        mime = part.get("mimeType", "")
        body = part.get("body", {})
        data = body.get("data")
        if mime == "text/html" and data:
            try:
                html_parts.append(_decode_b64(data).decode("utf-8", errors="replace"))
            except Exception as e:
                log.warning("decode html failed: %s", e)
        elif mime == "text/plain" and data:
            try:
                text_parts.append(_decode_b64(data).decode("utf-8", errors="replace"))
            except Exception as e:
                log.warning("decode text failed: %s", e)
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    html = "\n".join(html_parts) if html_parts else None
    text = "\n".join(text_parts) if text_parts else None
    return html, text


def _parse_received_at(headers: list[dict]) -> datetime | None:
    raw = _header(headers, "Date")
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def authenticate(credentials_path: Path, token_path: Path) -> Credentials:
    """Run the OAuth desktop-app flow. Writes token.json. Reuses it on later calls.

    If an existing token doesn't cover the current SCOPES (e.g. after a scope
    expansion from readonly to modify), the token is discarded and we re-run
    the full browser flow. Note: Google does not let you upgrade scopes via
    refresh — only via a fresh consent flow."""
    creds: Credentials | None = None
    if token_path.exists():
        # Inspect the token file directly so we can detect a scope mismatch
        # BEFORE handing the file to google-auth, which silently overrides
        # the stored scopes with whatever we pass in.
        try:
            import json
            saved = set(json.loads(token_path.read_text()).get("scopes") or [])
        except Exception:
            saved = set()
        if saved and set(SCOPES).issubset(saved):
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        else:
            log.info("token scopes %s do not cover %s — re-authenticating", saved, SCOPES)
            token_path.unlink()
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not credentials_path.exists():
                raise FileNotFoundError(
                    f"OAuth credentials file not found at {credentials_path}. "
                    "Follow the README setup steps to download credentials.json."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())
    return creds


class GmailClient:
    def __init__(self, creds: Credentials):
        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    @classmethod
    def from_paths(cls, credentials_path: Path, token_path: Path) -> "GmailClient":
        return cls(authenticate(credentials_path, token_path))

    def list_message_ids(self, query: str) -> Iterator[str]:
        """Yield Gmail message IDs matching `query`. Paginates."""
        page_token: str | None = None
        while True:
            try:
                resp = (
                    self._service.users()
                    .messages()
                    .list(userId="me", q=query, pageToken=page_token, maxResults=500)
                    .execute()
                )
            except HttpError as e:
                log.error("Gmail list error: %s", e)
                raise
            for m in resp.get("messages", []) or []:
                yield m["id"]
            page_token = resp.get("nextPageToken")
            if not page_token:
                return

    def get_message(self, gmail_id: str) -> FetchedMessage:
        msg = (
            self._service.users()
            .messages()
            .get(userId="me", id=gmail_id, format="full")
            .execute()
        )
        payload = msg.get("payload", {}) or {}
        headers = payload.get("headers", []) or []
        from_addr = _header(headers, "From")
        subject = _header(headers, "Subject")
        html, text = _extract_bodies(payload)
        return FetchedMessage(
            gmail_id=msg["id"],
            thread_id=msg.get("threadId", ""),
            from_address=from_addr,
            from_domain=_domain_of(from_addr),
            subject=subject,
            received_at=_parse_received_at(headers),
            snippet=msg.get("snippet"),
            raw_html=html,
            raw_text=text,
        )

    def fetch_new_messages(
        self, query: str, known_ids: Iterable[str]
    ) -> Iterator[FetchedMessage]:
        """List + fetch full messages, skipping any gmail_id in `known_ids`."""
        seen = set(known_ids)
        for gmail_id in self.list_message_ids(query):
            if gmail_id in seen:
                continue
            yield self.get_message(gmail_id)

    def get_or_create_label(self, name: str) -> str:
        """Return the Gmail label ID for `name`, creating it if necessary."""
        existing = self._service.users().labels().list(userId="me").execute().get("labels", []) or []
        for lbl in existing:
            if lbl.get("name") == name:
                return lbl["id"]
        body = {
            "name": name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        }
        created = self._service.users().labels().create(userId="me", body=body).execute()
        log.info("created Gmail label %s (%s)", name, created.get("id"))
        return created["id"]

    def add_label(self, message_id: str, label_id: str) -> bool:
        """Apply `label_id` to `message_id`. Returns True if the label was newly added,
        False if the message already had it (idempotent — Gmail accepts redundant adds
        silently but we check to avoid noise in logs)."""
        try:
            msg = (
                self._service.users().messages()
                .get(userId="me", id=message_id, format="minimal")
                .execute()
            )
        except HttpError as e:
            log.warning("could not fetch %s for labelling: %s", message_id, e)
            return False
        if label_id in (msg.get("labelIds") or []):
            return False
        self._service.users().messages().modify(
            userId="me", id=message_id, body={"addLabelIds": [label_id]}
        ).execute()
        return True


def cache_messages(session: Session, messages: Iterable[FetchedMessage]) -> int:
    """Persist FetchedMessages to the `emails` table. Returns count inserted.

    Idempotent: skips any gmail_id already present.
    """
    n = 0
    for m in messages:
        if session.get(Email, m.gmail_id) is not None:
            continue
        session.add(
            Email(
                gmail_id=m.gmail_id,
                thread_id=m.thread_id,
                from_address=m.from_address,
                from_domain=m.from_domain,
                subject=m.subject,
                received_at=m.received_at,
                snippet=m.snippet,
                raw_html=m.raw_html,
                raw_text=m.raw_text,
                parsed=False,
            )
        )
        n += 1
        if n % 50 == 0:
            session.commit()
    session.commit()
    return n


def known_gmail_ids(session: Session) -> set[str]:
    return {row[0] for row in session.query(Email.gmail_id).all()}


def with_since(query: str, since: datetime | None) -> str:
    """Append a Gmail `after:` clause if `since` is supplied.

    Gmail's `after:` takes YYYY/MM/DD.
    """
    if not since:
        return query
    stamp = since.strftime("%Y/%m/%d")
    if re.search(r"\bafter:", query):
        return re.sub(r"\bafter:\S+", f"after:{stamp}", query)
    return f"{query} after:{stamp}"
