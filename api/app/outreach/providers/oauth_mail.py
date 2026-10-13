"""Gmail and Microsoft Graph providers, with OAuth 2.0 (authorization-code flow).

Least privilege:
* Microsoft: ``Mail.ReadWrite`` (create drafts, read replies) + ``User.Read`` + ``offline_access``.
  ``Mail.Send`` is requested only once an administrator has switched the policy to sending.
* Gmail: ``gmail.compose`` (drafts; Google has no drafts-only scope, so sending is blocked in our code
  until an administrator enables it) + ``gmail.readonly`` (read replies and bounces).

Tokens are stored encrypted (``app.outreach.crypto``). No passwords are ever requested or stored.
Real sending is additionally blocked unless ``OUTREACH_EXTERNAL_SENDING_ALLOWED=true``.
"""

from __future__ import annotations

import base64
import re
from datetime import UTC, datetime, timedelta
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import Mailbox
from app.outreach.crypto import decrypt_tokens, encrypt_tokens
from app.outreach.providers.base import (
    DeliveryEvent,
    EmailProviderError,
    InboundMessage,
    OutgoingEmail,
    SendResult,
    SyncResult,
)

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
MS_LOGIN = "https://login.microsoftonline.com"
GRAPH_API = "https://graph.microsoft.com/v1.0"
EMAIL_RE = re.compile(r"[\w.+'-]+@[\w-]+\.[\w.-]+")


def redirect_uri(provider: str, s: Settings | None = None) -> str:
    s = s or get_settings()
    return f"{s.oauth_redirect_base_url.rstrip('/')}/api/outreach/oauth/{provider}/callback"


def scopes_for(provider: str, sending_mode: str) -> list[str]:
    if provider == "gmail":
        return ["https://www.googleapis.com/auth/gmail.compose", "https://www.googleapis.com/auth/gmail.readonly"]
    if provider == "microsoft":
        base = ["offline_access", "User.Read", "Mail.ReadWrite"]
        return [*base, "Mail.Send"] if sending_mode == "send" else base
    raise ValueError(provider)


def configured(provider: str, s: Settings | None = None) -> bool:
    s = s or get_settings()
    if provider == "gmail":
        return bool(s.google_client_id and s.google_client_secret and s.token_encryption_key)
    if provider == "microsoft":
        return bool(s.microsoft_client_id and s.microsoft_client_secret and s.token_encryption_key)
    return False


def authorization_url(provider: str, state: str, sending_mode: str, s: Settings | None = None) -> str:
    s = s or get_settings()
    scopes = " ".join(scopes_for(provider, sending_mode))
    if provider == "gmail":
        q = {
            "client_id": s.google_client_id,
            "redirect_uri": redirect_uri(provider, s),
            "response_type": "code",
            "scope": scopes,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return f"{GOOGLE_AUTH}?{urlencode(q)}"
    q = {
        "client_id": s.microsoft_client_id,
        "redirect_uri": redirect_uri(provider, s),
        "response_type": "code",
        "response_mode": "query",
        "scope": scopes,
        "state": state,
        "prompt": "select_account",
    }
    return f"{MS_LOGIN}/{s.microsoft_tenant}/oauth2/v2.0/authorize?{urlencode(q)}"


def _token_request(provider: str, data: dict[str, str], client: httpx.Client, s: Settings) -> dict[str, Any]:
    if provider == "gmail":
        url = GOOGLE_TOKEN
        data = {**data, "client_id": s.google_client_id or "", "client_secret": s.google_client_secret or ""}
    else:
        url = f"{MS_LOGIN}/{s.microsoft_tenant}/oauth2/v2.0/token"
        data = {**data, "client_id": s.microsoft_client_id or "", "client_secret": s.microsoft_client_secret or ""}
    r = client.post(url, data=data)
    if r.status_code >= 400:
        raise EmailProviderError(f"OAuth token request failed ({r.status_code})", transient=False, needs_reauth=True)
    t = r.json()
    t["expires_at"] = (datetime.now(UTC) + timedelta(seconds=int(t.get("expires_in", 3600)) - 60)).isoformat()
    return t


def exchange_code(
    provider: str, code: str, client: httpx.Client | None = None, s: Settings | None = None
) -> tuple[dict[str, Any], str, str]:
    """Exchange an authorization code. Returns (tokens, email_address, display_name)."""
    s = s or get_settings()
    client = client or httpx.Client(timeout=30)
    tokens = _token_request(
        provider, {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(provider, s)}, client, s
    )
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    if provider == "gmail":
        p = client.get(f"{GMAIL_API}/profile", headers=headers).json()
        email = p.get("emailAddress", "")
        return tokens, email, email
    me = client.get(f"{GRAPH_API}/me", headers=headers).json()
    return tokens, me.get("mail") or me.get("userPrincipalName", ""), me.get("displayName") or ""


class _OAuthProvider:
    name = ""

    def __init__(
        self, db: Session, mailbox: Mailbox, client: httpx.Client | None = None, settings: Settings | None = None
    ) -> None:
        self.db, self.mailbox, self.s = db, mailbox, settings or get_settings()
        self.client = client or httpx.Client(timeout=30)

    def _access_token(self) -> str:
        if not self.mailbox.token_ciphertext:
            raise EmailProviderError("Mailbox is not connected", transient=False, needs_reauth=True)
        t = decrypt_tokens(self.mailbox.token_ciphertext)
        if datetime.fromisoformat(t["expires_at"]) <= datetime.now(UTC):
            if not t.get("refresh_token"):
                raise EmailProviderError("Mailbox authorization expired; reconnect it", transient=False, needs_reauth=True)
            new = _token_request(
                self.name, {"grant_type": "refresh_token", "refresh_token": t["refresh_token"]}, self.client, self.s
            )
            new.setdefault("refresh_token", t["refresh_token"])
            self.mailbox.token_ciphertext = encrypt_tokens(new)
            self.mailbox.token_expires_at = datetime.fromisoformat(new["expires_at"])
            t = new
        return str(t["access_token"])

    def _req(self, method: str, url: str, **kw: Any) -> httpx.Response:
        try:
            r = self.client.request(method, url, headers={"Authorization": f"Bearer {self._access_token()}"}, **kw)
        except httpx.HTTPError as e:
            raise EmailProviderError(f"{self.name} network error: {e.__class__.__name__}") from e
        if r.status_code == 401:
            raise EmailProviderError(
                f"{self.name} rejected the mailbox authorization; reconnect it", transient=False, needs_reauth=True
            )
        if r.status_code == 429 or r.status_code >= 500:
            raise EmailProviderError(f"{self.name} temporarily unavailable ({r.status_code})", transient=True)
        if r.status_code >= 400:
            raise EmailProviderError(f"{self.name} error {r.status_code}: {r.text[:200]}", transient=False)
        return r


class GmailProvider(_OAuthProvider):
    name = "gmail"

    def _raw(self, email: OutgoingEmail) -> dict[str, Any]:
        raw = base64.urlsafe_b64encode(email.to_mime().as_bytes()).decode()
        return {"raw": raw, **({"threadId": email.thread_id} if email.thread_id else {})}

    def create_draft(self, email: OutgoingEmail) -> SendResult:
        d = self._req("POST", f"{GMAIL_API}/drafts", json={"message": self._raw(email)}).json()
        msg = d.get("message", {})
        return SendResult(provider_message_id=d.get("id", ""), thread_id=msg.get("threadId"))

    def send(self, email: OutgoingEmail) -> SendResult:
        m = self._req("POST", f"{GMAIL_API}/messages/send", json=self._raw(email)).json()
        return SendResult(provider_message_id=m.get("id", ""), thread_id=m.get("threadId"))

    def sync(self, cursor: str | None) -> SyncResult:
        since = int(cursor) if cursor else int((datetime.now(UTC) - timedelta(days=2)).timestamp())
        ids = self._req("GET", f"{GMAIL_API}/messages", params={"q": f"in:inbox after:{since}", "maxResults": 50}).json()
        res = SyncResult(cursor=str(since))
        newest = since
        for ref in ids.get("messages", []):
            m = self._req("GET", f"{GMAIL_API}/messages/{ref['id']}", params={"format": "raw"}).json()
            parsed: EmailMessage = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(m["raw"] + "=="))  # type: ignore[assignment,arg-type]
            when = datetime.fromtimestamp(int(m.get("internalDate", "0")) / 1000, UTC)
            newest = max(newest, int(when.timestamp()))
            sender = (parsed.get("From") or "").lower()
            body_part = parsed.get_body(preferencelist=("plain",))
            body = body_part.get_content() if body_part else ""
            if "mailer-daemon" in sender or "postmaster" in sender:
                failed = parsed.get("X-Failed-Recipients") or next(iter(EMAIL_RE.findall(body)), None)
                res.delivery.append(
                    DeliveryEvent(
                        provider_message_id=None,
                        to_email=failed,
                        status="bounced",
                        detail=(parsed.get("Subject") or "")[:200],
                        at=when,
                    )
                )
                continue
            res.inbound.append(
                InboundMessage(
                    provider_message_id=m["id"],
                    from_email=(EMAIL_RE.findall(sender) or [sender])[0],
                    subject=parsed.get("Subject") or "",
                    body=body[:20000],
                    received_at=when,
                    thread_id=m.get("threadId"),
                    in_reply_to=parsed.get("In-Reply-To"),
                )
            )
        res.cursor = str(newest)
        return res


class MicrosoftGraphProvider(_OAuthProvider):
    name = "microsoft"

    def _payload(self, email: OutgoingEmail) -> dict[str, Any]:
        return {
            "subject": email.subject,
            "body": {"contentType": "Text", "content": email.body},
            "toRecipients": [{"emailAddress": {"address": email.to_email}}],
        }

    def create_draft(self, email: OutgoingEmail) -> SendResult:
        m = self._req("POST", f"{GRAPH_API}/me/messages", json=self._payload(email)).json()
        return SendResult(
            provider_message_id=m.get("id", ""), thread_id=m.get("conversationId"), internet_message_id=m.get("internetMessageId")
        )

    def send(self, email: OutgoingEmail) -> SendResult:
        draft = self.create_draft(email)
        self._req("POST", f"{GRAPH_API}/me/messages/{draft.provider_message_id}/send")
        return draft

    def sync(self, cursor: str | None) -> SyncResult:
        since = cursor or (datetime.now(UTC) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {
            "$filter": f"receivedDateTime gt {since}",
            "$orderby": "receivedDateTime",
            "$top": "50",
            "$select": "id,subject,from,receivedDateTime,conversationId,internetMessageId,body,internetMessageHeaders",
        }
        data = self._req("GET", f"{GRAPH_API}/me/mailFolders/inbox/messages", params=params).json()
        res = SyncResult(cursor=since)
        for m in data.get("value", []):
            when = datetime.fromisoformat(m["receivedDateTime"].replace("Z", "+00:00"))
            res.cursor = m["receivedDateTime"]
            sender = (m.get("from", {}).get("emailAddress", {}).get("address") or "").lower()
            body = m.get("body", {}).get("content", "")
            if m.get("body", {}).get("contentType") == "html":
                body = re.sub(r"<[^>]+>", " ", body)
            subject = m.get("subject") or ""
            if sender.startswith("postmaster") or subject.lower().startswith("undeliverable"):
                failed = next(iter(EMAIL_RE.findall(body)), None)
                res.delivery.append(
                    DeliveryEvent(provider_message_id=None, to_email=failed, status="bounced", detail=subject[:200], at=when)
                )
                continue
            headers = {h["name"].lower(): h["value"] for h in m.get("internetMessageHeaders") or []}
            res.inbound.append(
                InboundMessage(
                    provider_message_id=m["id"],
                    from_email=sender,
                    subject=subject,
                    body=body[:20000],
                    received_at=when,
                    thread_id=m.get("conversationId"),
                    in_reply_to=headers.get("in-reply-to"),
                )
            )
        return res
