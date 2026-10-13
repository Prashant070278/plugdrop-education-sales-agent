"""Channel coordination (Module D).

Before anything is sent to a person — on any channel — ``check_outreach`` looks at the shared contact record:
suppression, the WhatsApp conversation state, unanswered messages from them, meetings being arranged, recent
messages on every channel, live sequences and open tasks. The goal is that the person never receives duplicate or
contradictory messages (an automated follow-up the day after they asked for a meeting on WhatsApp, say).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.engagement.logs import event
from app.engagement.policy import load_policy
from app.models import (
    Activity,
    ConsentRecord,
    Contact,
    EmailReply,
    Meeting,
    OutreachMessage,
    OutreachSequence,
    Task,
    WaConversation,
    WaMessage,
)
from app.services import suppressed_contact_ids

ARRANGING = ("proposed", "awaiting_approval", "scheduled", "confirmed")
LIVE_SEQ = ("draft", "active")
EMAIL_OUT = ("sent", "delivered")
WA_OUT = ("sent", "delivered", "read")
AUTOMATED = ("cold", "follow_up")  # purposes that are outreach rather than a response to the person


@dataclass
class Check:
    key: str
    kind: str  # block | defer | warn | ok
    detail: str


@dataclass
class Verdict:
    channel: str
    purpose: str
    checks: list[Check] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return not any(c.kind in ("block", "defer") for c in self.checks)

    @property
    def blocking(self) -> list[Check]:
        return [c for c in self.checks if c.kind == "block"]

    @property
    def deferring(self) -> list[Check]:
        return [c for c in self.checks if c.kind == "defer"]

    def summary(self) -> str:
        return "; ".join(c.detail for c in self.checks if c.kind in ("block", "defer"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "purpose": self.purpose,
            "allowed": self.allowed,
            "checks": [asdict(c) for c in self.checks],
        }


def whatsapp_opted_in(db: Session, contact: Contact) -> ConsentRecord | None:
    if not contact.whatsapp_number:
        return None
    return db.scalars(
        select(ConsentRecord)
        .where(
            ConsentRecord.status == "consented",
            ConsentRecord.channel == "whatsapp",
            or_(ConsentRecord.contact_id == contact.id, ConsentRecord.phone == contact.whatsapp_number),
        )
        .order_by(ConsentRecord.recorded_at.desc())
    ).first()


def phone_suppressed(db: Session, number: str | None) -> bool:
    if not number:
        return False
    return bool(
        db.scalar(
            select(func.count())
            .select_from(ConsentRecord)
            .where(ConsentRecord.phone == number, ConsentRecord.status.in_(("opted_out", "suppressed")))
        )
    )


def contact_state(db: Session, contact: Contact, now: datetime | None = None) -> dict[str, Any]:
    """Everything the coordination rules look at, also shown on the lead page."""
    now = now or datetime.now(UTC)
    conv = (
        db.scalar(select(WaConversation).where(WaConversation.wa_number == contact.whatsapp_number))
        if contact.whatsapp_number
        else None
    ) or db.scalars(select(WaConversation).where(WaConversation.contact_id == contact.id)).first()
    last_email_out = db.scalar(
        select(func.max(OutreachMessage.sent_at))
        .join(OutreachSequence)
        .where(OutreachSequence.contact_id == contact.id, OutreachMessage.status.in_(EMAIL_OUT))
    )
    last_wa_out = conv.last_outbound_at if conv else None
    last_email_in = db.scalar(select(func.max(EmailReply.received_at)).where(EmailReply.contact_id == contact.id))
    unhandled_email = db.scalar(
        select(func.count())
        .select_from(EmailReply)
        .where(EmailReply.contact_id == contact.id, EmailReply.handled.is_(False), EmailReply.label != "out_of_office")
    )
    wa_waiting = bool(
        conv and conv.last_inbound_at and (conv.last_outbound_at is None or conv.last_inbound_at > conv.last_outbound_at)
    )
    live = db.scalars(
        select(OutreachSequence).where(OutreachSequence.contact_id == contact.id, OutreachSequence.status.in_(LIVE_SEQ))
    ).first()
    meeting = db.scalars(
        select(Meeting)
        .where(Meeting.contact_id == contact.id, Meeting.status.in_(ARRANGING))
        .order_by(Meeting.start_at.asc().nullsfirst())
    ).first()
    tasks = db.scalars(
        select(Task).where(Task.contact_id == contact.id, Task.status == "open").order_by(Task.due_at.asc().nullslast())
    ).all()
    touches = [t for t in (last_email_out, last_wa_out) if t]
    optin = whatsapp_opted_in(db, contact)
    return {
        "contact_id": contact.id,
        "suppressed": contact.id in suppressed_contact_ids(db, [contact]) or phone_suppressed(db, contact.whatsapp_number),
        "email": contact.email,
        "whatsapp_number": contact.whatsapp_number,
        "whatsapp_opt_in": {"at": optin.recorded_at, "source": optin.source, "reason": optin.reason} if optin else None,
        "conversation": {
            "id": conv.id,
            "state": conv.state,
            "assigned_to_id": conv.assigned_to_id,
            "last_inbound_at": conv.last_inbound_at,
            "window_open_until": conv.last_inbound_at + timedelta(hours=24)
            if conv.last_inbound_at and conv.last_inbound_at + timedelta(hours=24) > now
            else None,
        }
        if conv
        else None,
        "awaiting_our_reply": bool(unhandled_email) or wa_waiting,
        "last_outbound_at": max(touches) if touches else None,
        "last_email_out_at": last_email_out,
        "last_whatsapp_out_at": last_wa_out,
        "last_inbound_at": max([t for t in (last_email_in, conv.last_inbound_at if conv else None) if t], default=None),
        "live_sequence": {"id": live.id, "status": live.status} if live else None,
        "meeting": {"id": meeting.id, "status": meeting.status, "start_at": meeting.start_at} if meeting else None,
        "open_tasks": [{"id": t.id, "title": t.title, "due_at": t.due_at, "reason": t.reason} for t in tasks],
    }


def check_outreach(
    db: Session,
    contact: Contact,
    channel: str,
    purpose: str,
    *,
    now: datetime | None = None,
    exclude_email_id: int | None = None,
    exclude_wa_id: int | None = None,
    sequence_id: int | None = None,
) -> Verdict:
    """Decide whether a message on ``channel`` for ``purpose`` (cold | follow_up | reply | meeting) may go out now."""
    now = now or datetime.now(UTC)
    pol = load_policy(db)
    st = contact_state(db, contact, now)
    v = Verdict(channel, purpose)
    add = v.checks.append

    if st["suppressed"]:
        add(Check("suppressed", "block", "Opted out or suppressed (on any channel): no messages"))
    if channel == "whatsapp":
        if not contact.whatsapp_number:
            add(Check("whatsapp_number", "block", "No WhatsApp number on the contact record"))
        elif not st["whatsapp_opt_in"] and purpose in AUTOMATED:
            add(Check("whatsapp_opt_in", "block", "No recorded WhatsApp opt-in: business-initiated messages need consent"))
    conv = st["conversation"]
    if conv and conv["state"] == "handoff" and purpose in AUTOMATED:
        add(Check("handoff", "block", "A person has taken over this contact's WhatsApp conversation"))
    if purpose in AUTOMATED and pol["block_while_awaiting_our_reply"] and st["awaiting_our_reply"]:
        add(Check("awaiting_reply", "block", "They wrote to us and are waiting for an answer: reply instead of sending outreach"))
    m = st["meeting"]
    if m and purpose in AUTOMATED:
        add(
            Check(
                "meeting",
                "block",
                f"A meeting is being arranged or booked (#{m['id']}, {m['status']}): outreach would contradict it",
            )
        )
    if purpose in AUTOMATED and st["live_sequence"] and channel == "whatsapp":
        add(
            Check(
                "email_sequence",
                "block",
                f"An email sequence is running (#{st['live_sequence']['id']}): WhatsApp would duplicate it",
            )
        )
    if (
        purpose == "cold"
        and channel == "email"
        and conv
        and conv["last_inbound_at"]
        and conv["last_inbound_at"] > now - timedelta(days=14)
    ):
        add(Check("whatsapp_active", "block", "They've been talking to us on WhatsApp in the last 14 days: continue there"))
    # Recent messages on any channel (excluding the message being checked)
    recent = _recent_outbound(
        db, contact, now - timedelta(hours=pol["min_hours_between_touches"]), exclude_email_id, exclude_wa_id
    )
    if recent and purpose in AUTOMATED:
        when, ch = recent
        add(
            Check(
                "recent_message",
                "defer",
                f"Already messaged on {ch} {_ago(now, when)}; the policy leaves {pol['min_hours_between_touches']} hours between messages",
            )
        )
    for t in st["open_tasks"][:3]:
        add(Check("open_task", "warn", f"Open task: {t['title']}"))
    if not any(c.kind != "warn" for c in v.checks):
        add(Check("clear", "ok", "Nothing on other channels conflicts with this message"))
    event(
        "coordination_check",
        contact_id=contact.id,
        channel=channel,
        purpose=purpose,
        allowed=v.allowed,
        reasons=[c.key for c in v.checks if c.kind in ("block", "defer")],
        sequence_id=sequence_id,
    )
    return v


def _recent_outbound(
    db: Session, contact: Contact, since: datetime, exclude_email_id: int | None, exclude_wa_id: int | None
) -> tuple[datetime, str] | None:
    q = (
        select(func.max(OutreachMessage.sent_at))
        .join(OutreachSequence)
        .where(OutreachSequence.contact_id == contact.id, OutreachMessage.status.in_(EMAIL_OUT), OutreachMessage.sent_at >= since)
    )
    if exclude_email_id:
        q = q.where(OutreachMessage.id != exclude_email_id)
    email_at = db.scalar(q)
    wq = (
        select(func.max(WaMessage.sent_at))
        .join(WaConversation)
        .where(
            or_(WaConversation.contact_id == contact.id, WaConversation.wa_number == (contact.whatsapp_number or "-")),
            WaMessage.direction == "out",
            WaMessage.status.in_(WA_OUT),
            WaMessage.sent_at >= since,
        )
    )
    if exclude_wa_id:
        wq = wq.where(WaMessage.id != exclude_wa_id)
    wa_at = db.scalar(wq)
    found = [(t, ch) for t, ch in ((email_at, "email"), (wa_at, "WhatsApp")) if t]
    return max(found) if found else None


def _ago(now: datetime, when: datetime) -> str:
    h = int((now - when).total_seconds() // 3600)
    return "less than an hour ago" if h < 1 else f"{h} hour{'s' if h != 1 else ''} ago"


def record_activity(
    db: Session,
    activity_type: str,
    subject: str,
    *,
    lead_id: int | None,
    institution_id: int | None,
    contact_id: int | None,
    user_id: int | None = None,
    body: str | None = None,
    details: dict[str, Any] | None = None,
    is_demo: bool = False,
    occurred_at: datetime | None = None,
) -> Activity:
    """One entry in the unified timeline. Flushed so callers can link tasks and meetings to it."""
    a = Activity(
        activity_type=activity_type,
        subject=subject[:300],
        body=body,
        lead_id=lead_id,
        institution_id=institution_id,
        contact_id=contact_id,
        user_id=user_id,
        details=details or {},
        is_demo=is_demo,
    )
    if occurred_at:
        a.occurred_at = occurred_at
    db.add(a)
    db.flush()
    return a
