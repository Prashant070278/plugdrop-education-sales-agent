"""Mock email provider: stores mail in the ``mock_mail`` table. Nothing ever leaves the system.

Simulations for demos and tests:
* recipient addresses containing "bounce" bounce; all others are delivered;
* ``simulate_inbound`` places a reply in the mailbox's inbox, as if the prospect had answered.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Mailbox, MockMail
from app.outreach.providers.base import DeliveryEvent, InboundMessage, OutgoingEmail, SendResult, SyncResult


class MockEmailProvider:
    name = "mock"

    def __init__(self, db: Session, mailbox: Mailbox) -> None:
        self.db, self.mailbox = db, mailbox

    def _store(self, folder: str, email: OutgoingEmail) -> SendResult:
        mid = f"<mock-{uuid.uuid4().hex[:16]}@{self.mailbox.email_address.split('@')[-1]}>"
        thread = email.thread_id or f"thread-{uuid.uuid4().hex[:12]}"
        delivery = None
        if folder == "sent":
            delivery = "bounced" if "bounce" in email.to_email.lower() else "delivered"
        self.db.add(
            MockMail(
                mailbox_id=self.mailbox.id,
                folder=folder,
                message_id=mid,
                thread_id=thread,
                in_reply_to=email.in_reply_to,
                from_email=self.mailbox.email_address,
                to_email=email.to_email,
                subject=email.subject,
                body=email.body,
                delivery=delivery,
            )
        )
        self.db.flush()
        return SendResult(provider_message_id=mid, thread_id=thread, internet_message_id=mid)

    def create_draft(self, email: OutgoingEmail) -> SendResult:
        return self._store("drafts", email)

    def send(self, email: OutgoingEmail) -> SendResult:
        return self._store("sent", email)

    def sync(self, cursor: str | None) -> SyncResult:
        last = int(cursor or 0)
        rows = self.db.scalars(
            select(MockMail).where(MockMail.mailbox_id == self.mailbox.id, MockMail.id > last).order_by(MockMail.id)
        ).all()
        res = SyncResult(cursor=str(rows[-1].id) if rows else cursor)
        for r in rows:
            if r.folder == "inbox":
                res.inbound.append(
                    InboundMessage(
                        provider_message_id=r.message_id,
                        from_email=r.from_email,
                        subject=r.subject,
                        body=r.body,
                        received_at=r.created_at or datetime.now(UTC),
                        thread_id=r.thread_id,
                        in_reply_to=r.in_reply_to,
                    )
                )
            elif r.folder == "sent" and r.delivery:
                res.delivery.append(
                    DeliveryEvent(
                        provider_message_id=r.message_id,
                        to_email=r.to_email,
                        status=r.delivery,
                        detail="Mailbox does not exist (simulated)" if r.delivery == "bounced" else "",
                        at=r.created_at,
                    )
                )
        return res


def simulate_inbound(
    db: Session, mailbox: Mailbox, *, from_email: str, subject: str, body: str, thread_id: str | None, in_reply_to: str | None
) -> MockMail:
    m = MockMail(
        mailbox_id=mailbox.id,
        folder="inbox",
        message_id=f"<mock-in-{uuid.uuid4().hex[:16]}@prospect>",
        thread_id=thread_id or f"thread-{uuid.uuid4().hex[:12]}",
        in_reply_to=in_reply_to,
        from_email=from_email,
        to_email=mailbox.email_address,
        subject=subject,
        body=body,
    )
    db.add(m)
    db.flush()
    return m
