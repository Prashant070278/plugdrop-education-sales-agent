"""Email provider interface.

Business logic only uses ``EmailProvider``. A provider creates drafts, sends (only when the caller has
already decided sending is allowed) and reports what happened afterwards: delivery results and inbound
replies. OAuth tokens are handled by the provider; no passwords exist anywhere in the system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Protocol


class EmailProviderError(Exception):
    def __init__(self, message: str, *, transient: bool = True, needs_reauth: bool = False) -> None:
        super().__init__(message)
        self.transient = transient
        self.needs_reauth = needs_reauth


@dataclass(frozen=True)
class OutgoingEmail:
    from_email: str
    from_name: str
    to_email: str
    subject: str
    body: str
    thread_id: str | None = None  # continue an existing thread (follow-ups)
    in_reply_to: str | None = None  # RFC 5322 Message-ID of the previous email
    idempotency_key: str = ""

    def to_mime(self) -> EmailMessage:
        m = EmailMessage()
        m["From"] = f"{self.from_name} <{self.from_email}>"
        m["To"] = self.to_email
        m["Subject"] = self.subject
        m["Message-ID"] = make_msgid(idstring=self.idempotency_key or None, domain=self.from_email.split("@")[-1])
        m["List-Unsubscribe"] = f"<mailto:{self.from_email}?subject=unsubscribe>"
        if self.in_reply_to:
            m["In-Reply-To"] = self.in_reply_to
            m["References"] = self.in_reply_to
        m.set_content(self.body)
        return m


@dataclass(frozen=True)
class SendResult:
    provider_message_id: str
    thread_id: str | None
    internet_message_id: str | None = None


@dataclass(frozen=True)
class InboundMessage:
    provider_message_id: str
    from_email: str
    subject: str
    body: str
    received_at: datetime
    thread_id: str | None = None
    in_reply_to: str | None = None


@dataclass(frozen=True)
class DeliveryEvent:
    provider_message_id: str | None  # our sent message, when known
    to_email: str | None
    status: str  # "delivered" | "bounced"
    detail: str = ""
    at: datetime | None = None


@dataclass
class SyncResult:
    inbound: list[InboundMessage] = field(default_factory=list)
    delivery: list[DeliveryEvent] = field(default_factory=list)
    cursor: str | None = None


class EmailProvider(Protocol):
    name: str

    def create_draft(self, email: OutgoingEmail) -> SendResult: ...

    def send(self, email: OutgoingEmail) -> SendResult: ...

    def sync(self, cursor: str | None) -> SyncResult: ...


def provider_for(db: object, mailbox: object) -> EmailProvider:
    """Return the provider implementation for a mailbox."""
    from app.outreach.providers.mock import MockEmailProvider
    from app.outreach.providers.oauth_mail import GmailProvider, MicrosoftGraphProvider

    kind = getattr(mailbox, "provider", "")
    if kind == "mock":
        return MockEmailProvider(db, mailbox)  # type: ignore[arg-type]
    if kind == "gmail":
        return GmailProvider(db, mailbox)  # type: ignore[arg-type]
    if kind == "microsoft":
        return MicrosoftGraphProvider(db, mailbox)  # type: ignore[arg-type]
    raise EmailProviderError(f"Unknown email provider '{kind}'", transient=False)
