"""WhatsApp engagement (Module A): templates, conversations, sending, inbound processing, opt-outs, handoff.

Rules that are enforced in code, whatever the user interface does:
- Only the official WhatsApp Business Platform (or the mock provider) is used.
- Business-initiated messages go only to contacts with a recorded WhatsApp opt-in, and only as approved templates.
- Free-form text is only allowed inside the 24-hour customer-service window (they messaged us in the last 24 hours).
- Every outbound message is approved by a person; nothing is sent to numbers taken from research or the web
  without that person's opt-in.
- STOP / unsubscribe is honoured at once on every channel.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, selectinload

from app import audit
from app.config import Settings, get_settings
from app.engagement import coordination as C
from app.engagement import followups as F
from app.engagement.logs import event
from app.engagement.policy import load_policy
from app.models import (
    ConsentRecord,
    Contact,
    Lead,
    User,
    WaConversation,
    WaMessage,
    WaTemplate,
    WebhookEvent,
)
from app.outreach import writer as W
from app.outreach.classify import OPT_OUT, classify_rules
from app.whatsapp.providers import get_provider
from app.whatsapp.providers.base import OutgoingWa, WaProviderError, parse_webhook

WINDOW = timedelta(hours=24)
OUT_DONE = ("sent", "delivered", "read")
STATUS_ORDER = {"sending": 0, "sent": 1, "delivered": 2, "read": 3}
STOP_WORDS = re.compile(r"^\s*(stop|unsubscribe|opt[\s-]?out|cancel|end|quit|band karo|mat bhejo)\s*[.!]*\s*$", re.I)
HANDOFF_WORDS = re.compile(
    r"\b(?:speak|talk|connect) (?:to|with) (?:a |an )?(?:person|human|someone|representative|agent|executive)|\bcall me\b"
    r"|\bcomplain|\blegal\b|\bfraud|\bscam\b|\bharass",
    re.I,
)
PARAM_SOURCES = {
    "contact.greeting": "Contact greeting (e.g. Dr. Sharma)",
    "contact.first_name": "Contact first name",
    "institution.name": "Institution name",
    "sender.name": "Your name",
    "custom": "Typed for each message",
}
TEMPLATE_NAME = re.compile(r"^[a-z0-9_]{3,100}$")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def normalize_number(raw: str | None) -> str | None:
    """+E.164. A 10-digit Indian mobile (starting 6–9) gets +91; anything else must already have a country code."""
    if not raw:
        return None
    digits = re.sub(r"[^\d+]", "", raw.strip())
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if digits.startswith("+"):
        return digits if re.fullmatch(r"\+\d{8,15}", digits) else None
    if digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if re.fullmatch(r"[6-9]\d{9}", digits):
        return "+91" + digits
    if re.fullmatch(r"91[6-9]\d{9}", digits):
        return "+" + digits
    return None


def sending_mode(db: Session, s: Settings | None = None) -> tuple[bool, str]:
    s = s or get_settings()
    pol = load_policy(db)
    if not pol["whatsapp_enabled"]:
        return False, "WhatsApp is switched off in Settings → Channels"
    if s.whatsapp_provider != "mock" and not s.whatsapp_external_sending_allowed:
        return False, "Real WhatsApp sending is switched off on the server (WHATSAPP_EXTERNAL_SENDING_ALLOWED=false)"
    return True, "WhatsApp sending is on" + (
        " (test provider: nothing leaves the system)" if s.whatsapp_provider == "mock" else ""
    )


def window_open(conv: WaConversation, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    return bool(conv.last_inbound_at and conv.last_inbound_at + WINDOW > now)


def _lead_for(db: Session, contact: Contact | None) -> Lead | None:
    if contact is None:
        return None
    return db.scalars(
        select(Lead)
        .where(Lead.institution_id == contact.institution_id, Lead.stage.not_in(("won", "lost")))
        .order_by(Lead.id.desc())
    ).first()


def _activity(
    db: Session,
    conv: WaConversation,
    subject: str,
    *,
    msg: WaMessage | None,
    direction: str,
    user_id: int | None = None,
    body: str | None = None,
    typ: str = "whatsapp",
) -> Any:  # noqa: E501
    return C.record_activity(
        db,
        typ,
        subject,
        lead_id=conv.lead_id,
        institution_id=conv.institution_id,
        contact_id=conv.contact_id,
        user_id=user_id,
        body=body,
        details={"conversation_id": conv.id, "wa_message_id": msg.id if msg else None, "direction": direction},
        is_demo=conv.is_demo,
    )


def conversation_for(db: Session, number: str, contact: Contact | None = None, name: str | None = None) -> WaConversation:
    conv = db.scalar(select(WaConversation).where(WaConversation.wa_number == number))
    if contact is None:
        contact = db.scalars(select(Contact).where(Contact.whatsapp_number == number).order_by(Contact.id)).first()
    if conv is None:
        lead = _lead_for(db, contact)
        conv = WaConversation(
            wa_number=number,
            contact_id=contact.id if contact else None,
            institution_id=contact.institution_id if contact else None,
            lead_id=lead.id if lead else None,
            profile_name=name,
            is_demo=bool(contact and contact.is_demo),
        )
        db.add(conv)
        db.flush()
    else:
        if contact and conv.contact_id is None:
            lead = _lead_for(db, contact)
            conv.contact_id, conv.institution_id, conv.lead_id = contact.id, contact.institution_id, lead.id if lead else None
        if name and not conv.profile_name:
            conv.profile_name = name
    return conv


# ---------------------------------------------------------------------------
# opt-in / opt-out
# ---------------------------------------------------------------------------
OPT_IN_METHODS = {
    "website_form": "Ticked a WhatsApp consent box on a form",
    "written": "Asked in writing (email or letter) to be contacted on WhatsApp",
    "they_messaged_first": "They messaged our WhatsApp number first and agreed to further messages",
    "event": "Gave consent at an event, with a record kept",
    "verbal_recorded": "Gave consent on a recorded call",
}


def record_opt_in(db: Session, contact: Contact, number_raw: str, method: str, evidence: str, user: User) -> ConsentRecord:
    number = normalize_number(number_raw)
    if number is None:
        raise HTTPException(422, "Enter the WhatsApp number with its country code, e.g. +91 98123 45678")
    if method not in OPT_IN_METHODS:
        raise HTTPException(422, f"How did they opt in? One of: {', '.join(OPT_IN_METHODS)}")
    if len(evidence.strip()) < 10:
        raise HTTPException(422, "Describe the evidence of consent (where and when they agreed) in at least a few words")
    if C.phone_suppressed(db, number) or contact.id in C.suppressed_contact_ids(db, [contact]):
        raise HTTPException(
            409, "This person has opted out. A new opt-in must come from them directly; remove the opt-out first if they asked."
        )
    other = db.scalar(select(Contact).where(Contact.whatsapp_number == number, Contact.id != contact.id))
    if other:
        raise HTTPException(409, f"This WhatsApp number already belongs to {other.full_name}")
    contact.whatsapp_number = number
    rec = ConsentRecord(
        contact_id=contact.id,
        phone=number,
        channel="whatsapp",
        status="consented",
        reason=f"{OPT_IN_METHODS[method]}. Evidence: {evidence.strip()}"[:500],
        source=f"opt-in:{method}",
        recorded_by_id=user.id,
        is_demo=contact.is_demo,
    )
    db.add(rec)
    db.flush()
    audit.record(db, user, "whatsapp_opt_in", "contact", contact.id, {"number": number, "method": method})
    lead = _lead_for(db, contact)
    C.record_activity(
        db, "whatsapp", f"WhatsApp opt-in recorded for {contact.full_name}", lead_id=lead.id if lead else None,
        institution_id=contact.institution_id, contact_id=contact.id, user_id=user.id, body=rec.reason,
        details={"direction": "internal"}, is_demo=contact.is_demo,
    )  # fmt: skip
    db.commit()
    return rec


def _opt_out(db: Session, conv: WaConversation, msg: WaMessage, why: str) -> None:
    db.add(
        ConsentRecord(
            contact_id=conv.contact_id,
            phone=conv.wa_number,
            channel="whatsapp",
            status="opted_out",
            reason=why,
            source=f"WhatsApp message {msg.provider_message_id}",
            is_demo=conv.is_demo,
        )
    )
    msg.opt_out = True
    conv.state = "closed"
    # Cancel anything still waiting to go out on WhatsApp
    for m in conv.messages:
        if m.direction == "out" and m.status in ("draft", "approved", "queued"):
            m.status, m.error = "cancelled", "Opted out"
    if conv.contact_id:
        from app.outreach.service import stop_all_for_contact

        stop_all_for_contact(db, conv.contact_id, "opted_out", "Opted out on WhatsApp")
    _activity(db, conv, "Opted out on WhatsApp — no further messages on any channel", msg=msg, direction="in")
    event("whatsapp_opt_out", conversation_id=conv.id, contact_id=conv.contact_id)


# ---------------------------------------------------------------------------
# templates
# ---------------------------------------------------------------------------
def check_text(body: str, *, kind: str, category: str | None = None, params: int = 0) -> list[str]:
    """Content rules for WhatsApp messages and templates. Returns problems (empty = fine)."""
    problems = []
    text = W._normalise(body)
    low = text.lower()
    if len(text) > 1024:
        problems.append("Longer than 1,024 characters")
    if W.HYPE.search(text):
        problems.append(f"Hype wording: {W.HYPE.search(text).group(0)}")  # type: ignore[union-attr]
    rel = [m.group(0) for p in W.FALSE_RELATIONSHIP for m in [re.search(p, low)] if m]
    if rel:
        problems.append("Implies an existing relationship: " + ", ".join(rel))
    if W.INTERNAL_NOTES.search(text):
        problems.append("Contains internal research notes")
    if re.search(r"\[EDIT|\[[A-Z ]+\]", text):
        problems.append("Unfinished [EDIT] placeholder")
    if kind == "template":
        found = sorted({int(n) for n in re.findall(r"\{\{(\d+)\}\}", text)})
        if found != list(range(1, len(found) + 1)):
            problems.append("Placeholders must be numbered {{1}}, {{2}}, … without gaps")
        if len(found) != params:
            problems.append(f"The text has {len(found)} placeholder(s) but {params} are defined")
        if category == "marketing" and not re.search(r"\bstop\b", low):
            problems.append('Marketing templates must tell people how to opt out (e.g. "Reply STOP to opt out")')
    return problems


def save_template(db: Session, user: User, data: dict[str, Any], template: WaTemplate | None = None) -> WaTemplate:
    if not TEMPLATE_NAME.match(data["name"]):
        raise HTTPException(422, "Template name: lower-case letters, numbers and underscores only (WhatsApp rule)")
    for p in data.get("params", []):
        if p.get("source") not in PARAM_SOURCES:
            raise HTTPException(422, f"Unknown placeholder source: {p.get('source')}")
    problems = check_text(data["body"], kind="template", category=data["category"], params=len(data.get("params", [])))
    if problems:
        raise HTTPException(422, "Fix the template: " + "; ".join(problems))
    if template is None:
        template = WaTemplate(created_by_id=user.id)
        db.add(template)
    elif template.status not in ("draft", "rejected"):
        raise HTTPException(409, "Only draft or rejected templates can be edited; create a new version instead")
    for k in ("name", "language", "category", "body", "params", "purpose"):
        if k in data:
            setattr(template, k, data[k])
    template.status, template.rejection_reason = "draft", None
    db.flush()
    audit.record(db, user, "save", "wa_template", template.id, {"name": template.name})
    db.commit()
    return template


def submit_template(db: Session, user: User, t: WaTemplate) -> WaTemplate:
    if t.status not in ("draft", "rejected"):
        raise HTTPException(409, f"Template is already {t.status}")
    examples = [f"example {i + 1}" for i in range(len(t.params))]
    try:
        pid, status = get_provider(db).submit_template(t.name, t.language, t.category, t.body, examples)
    except WaProviderError as e:
        raise HTTPException(502, str(e)) from e
    t.provider_template_id, t.status = pid, status
    audit.record(db, user, "submit", "wa_template", t.id, {"status": status})
    event("whatsapp_template_submitted", template_id=t.id, status=status)
    db.commit()
    return t


def sync_templates(db: Session) -> int:
    statuses = get_provider(db).template_statuses()
    n = 0
    for t in db.scalars(select(WaTemplate)):
        st = statuses.get((t.name, t.language))
        if st and (t.status, t.rejection_reason) != st:
            t.status, t.rejection_reason = st
            n += 1
    db.commit()
    return n


def render(t: WaTemplate, contact: Contact | None, sender: User | None, custom: list[str]) -> list[str]:
    values = []
    it = iter(custom)
    for p in t.params:
        src = str(p.get("source") or "")
        v = (
            {
                "contact.greeting": contact.greeting_name if contact else None,
                "contact.first_name": contact.first_name if contact else None,
                "institution.name": contact.institution.name if contact else None,
                "sender.name": sender.full_name if sender else None,
            }.get(src)
            if src != "custom"
            else next(it, None)
        )
        if not v or not str(v).strip():
            raise HTTPException(422, f"Fill in '{p.get('label') or src}' for this message")
        if "\n" in str(v) or len(str(v)) > 200:
            raise HTTPException(422, "Template values must be single lines under 200 characters")
        values.append(str(v).strip())
    return values


def fill(body: str, values: list[str]) -> str:
    return re.sub(r"\{\{(\d+)\}\}", lambda m: values[int(m.group(1)) - 1] if int(m.group(1)) <= len(values) else m.group(0), body)


# ---------------------------------------------------------------------------
# composing and sending
# ---------------------------------------------------------------------------
def draft(
    db: Session,
    user: User,
    conv: WaConversation,
    *,
    text: str | None = None,
    template_id: int | None = None,
    custom: list[str] | None = None,
    purpose: str = "reply",
) -> WaMessage:
    contact = db.get(Contact, conv.contact_id) if conv.contact_id else None
    if template_id:
        t = db.get(WaTemplate, template_id)
        if t is None or t.status != "approved":
            raise HTTPException(422, "Choose a template that WhatsApp has approved")
        values = render(t, contact, user, custom or [])
        msg = WaMessage(
            conversation_id=conv.id, direction="out", kind="template", template_id=t.id, params=values,
            body=fill(t.body, values), purpose=purpose, created_by_id=user.id, idempotency_key=uuid.uuid4().hex,
        )  # fmt: skip
    else:
        if not text or not text.strip():
            raise HTTPException(422, "Write a message or choose a template")
        if not window_open(conv):
            raise HTTPException(
                422,
                "More than 24 hours since they last messaged: WhatsApp only allows an approved template now.",
            )
        msg = WaMessage(
            conversation_id=conv.id, direction="out", kind="text", body=text.strip(), purpose=purpose,
            created_by_id=user.id, idempotency_key=uuid.uuid4().hex,
        )  # fmt: skip
    db.add(msg)
    db.flush()
    return msg


def edit(db: Session, msg: WaMessage, user: User, text: str) -> WaMessage:
    if msg.direction != "out" or msg.status not in ("draft", "approved", "blocked"):
        raise HTTPException(409, f"A {msg.status} message can't be edited")
    if msg.kind == "template":
        raise HTTPException(409, "Template text is fixed by WhatsApp; create a new message to change the values")
    msg.body, msg.status, msg.approved_at, msg.approved_by_id = text.strip(), "draft", None, None
    db.commit()
    return msg


def approve_and_send(db: Session, msg: WaMessage, user: User, settings: Settings | None = None) -> WaMessage:
    """A person approves the message; it is checked and sent (or held back with the reason)."""
    if msg.direction != "out" or msg.status not in ("draft", "blocked"):
        raise HTTPException(409, f"This message is {msg.status}")
    problems = check_text(msg.body, kind=msg.kind)
    if problems:
        raise HTTPException(422, "Fix before sending: " + "; ".join(problems))
    claimed = db.execute(
        update(WaMessage)
        .where(WaMessage.id == msg.id, WaMessage.status.in_(("draft", "blocked")))
        .values(
            status="queued", approved_by_id=user.id, approved_at=datetime.now(UTC), scheduled_for=datetime.now(UTC), error=None
        )
    ).rowcount
    db.commit()
    if claimed != 1:
        raise HTTPException(409, "This message is already being sent")
    db.refresh(msg)
    _activity(db, msg.conversation, "WhatsApp message approved", msg=msg, direction="internal", user_id=user.id, typ="note")
    db.commit()
    dispatch(db, msg, settings)
    db.commit()
    db.refresh(msg)
    if msg.status == "blocked":
        raise HTTPException(409, f"Not sent: {msg.error}")
    return msg


def _limits(db: Session, now: datetime) -> str | None:
    pol = load_policy(db)
    minute = db.scalar(
        select(func.count())
        .select_from(WaMessage)
        .where(WaMessage.direction == "out", WaMessage.sent_at >= now - timedelta(minutes=1))
    )
    if (minute or 0) >= pol["whatsapp_per_minute"]:
        return "rate"
    start = now.astimezone(ZoneInfo("Asia/Kolkata")).replace(hour=0, minute=0, second=0, microsecond=0)
    day = db.scalar(
        select(func.count())
        .select_from(WaMessage)
        .where(WaMessage.direction == "out", WaMessage.sent_at >= start, WaMessage.status.in_(OUT_DONE))
    )
    if (day or 0) >= pol["whatsapp_daily_limit"]:
        return "daily"
    return None


def dispatch(db: Session, msg: WaMessage, settings: Settings | None = None, now: datetime | None = None) -> str:
    """Send one queued message. Idempotent: only a 'queued' message can be claimed for sending."""
    s = settings or get_settings()
    now = now or datetime.now(UTC)
    conv = msg.conversation
    contact = db.get(Contact, conv.contact_id) if conv.contact_id else None
    pol = load_policy(db)

    def hold(reason: str, *, status: str = "blocked", retry_at: datetime | None = None) -> str:
        msg.status, msg.error = status, reason[:500]
        msg.scheduled_for = retry_at
        event("whatsapp_held", message_id=msg.id, status=status, reason=reason)
        if status == "blocked":
            _activity(db, conv, f"WhatsApp message held back: {reason}", msg=msg, direction="internal", typ="note")
        return msg.status

    ok, why = sending_mode(db, s)
    if not ok:
        return hold(why)
    if C.phone_suppressed(db, conv.wa_number):
        return hold("This number has opted out")
    if msg.kind == "text" and not window_open(conv, now):
        return hold("The 24-hour window closed before sending: use an approved template")
    if msg.kind == "template":
        t = db.get(WaTemplate, msg.template_id) if msg.template_id else None
        if t is None or t.status != "approved":
            return hold("The template is no longer approved by WhatsApp")
    if contact:
        purpose = {"first_contact": "cold", "follow_up": "follow_up"}.get(msg.purpose or "", "reply")
        v = C.check_outreach(db, contact, "whatsapp", purpose, now=now, exclude_wa_id=msg.id)
        if v.blocking:
            return hold(v.summary())
        if v.deferring:
            return hold(v.summary(), status="queued", retry_at=now + timedelta(hours=pol["min_hours_between_touches"]))
    elif msg.purpose == "first_contact":
        return hold("Unknown number: business-initiated messages need a CRM contact with an opt-in")
    lim = _limits(db, now)
    if lim:
        return hold(
            "Rate limit reached; retrying in a minute" if lim == "rate" else "Daily WhatsApp limit reached; sends tomorrow",
            status="queued",
            retry_at=now + (timedelta(minutes=1) if lim == "rate" else timedelta(hours=12)),
        )
    claimed = db.execute(
        update(WaMessage)
        .where(WaMessage.id == msg.id, WaMessage.status == "queued")
        .values(status="sending", attempts=WaMessage.attempts + 1)
    ).rowcount
    db.commit()
    if claimed != 1:
        db.refresh(msg)
        return msg.status
    db.refresh(msg)
    t = db.get(WaTemplate, msg.template_id) if msg.template_id else None
    out = OutgoingWa(
        to=conv.wa_number, kind=msg.kind, body=msg.body, idempotency_key=msg.idempotency_key,
        template_name=t.name if t else None, language=t.language if t else "en", params=tuple(msg.params or []),
    )  # fmt: skip
    try:
        res = get_provider(db, s).send(out)
    except WaProviderError as e:
        if e.transient and msg.attempts < pol["whatsapp_max_attempts"]:
            msg.status, msg.error, msg.scheduled_for = "queued", str(e)[:500], now + timedelta(minutes=2 * 2**msg.attempts)
            event("whatsapp_retry", message_id=msg.id, attempt=msg.attempts, code=e.code)
            return msg.status
        msg.status, msg.error = "failed", str(e)[:500]
        _activity(db, conv, f"WhatsApp message failed: {e}", msg=msg, direction="out", typ="note")
        event("whatsapp_failed", message_id=msg.id, code=e.code)
        return msg.status
    except Exception as e:  # noqa: BLE001 - never leave a message stuck in 'sending'
        msg.status, msg.error = "failed", f"Unexpected error: {e}"[:500]
        event("whatsapp_failed", message_id=msg.id, error=str(e))
        return msg.status
    msg.status, msg.provider_message_id, msg.sent_at, msg.error = "sent", res.provider_message_id, now, None
    conv.last_outbound_at = now
    _activity(
        db, conv, f"WhatsApp sent{' (template ' + t.name + ')' if t else ''}", msg=msg, direction="out",
        user_id=msg.approved_by_id, body=msg.body,
    )  # fmt: skip
    if contact:
        F.complete_tasks(db, reasons=("reply_needed",), note="answered on WhatsApp", contact_id=contact.id)
    event("whatsapp_sent", message_id=msg.id, conversation_id=conv.id, kind=msg.kind)
    return msg.status


def dispatch_due(db: Session, settings: Settings | None = None, limit: int = 50) -> int:
    now = datetime.now(UTC)
    ids = list(
        db.scalars(
            select(WaMessage.id)
            .where(WaMessage.status == "queued", or_(WaMessage.scheduled_for.is_(None), WaMessage.scheduled_for <= now))
            .order_by(WaMessage.scheduled_for)
            .limit(limit)
        )
    )
    n = 0
    for mid in ids:
        msg = db.scalars(
            select(WaMessage).where(WaMessage.id == mid, WaMessage.status == "queued").with_for_update(skip_locked=True)
        ).first()
        if msg is None:
            db.rollback()
            continue
        dispatch(db, msg, settings, now)
        db.commit()
        n += 1
    return n


# ---------------------------------------------------------------------------
# webhook processing (inbound messages and delivery statuses)
# ---------------------------------------------------------------------------
def process_webhook(db: Session, payload: dict[str, Any], provider: str, settings: Settings | None = None) -> dict[str, int]:
    batch = parse_webhook(payload)
    counts = {"messages": 0, "statuses": 0, "duplicates": 0}
    for m in batch.messages:
        key = f"msg:{m.provider_message_id}"
        if db.scalar(select(WebhookEvent.id).where(WebhookEvent.event_key == key)):
            counts["duplicates"] += 1
            continue
        db.add(WebhookEvent(provider=provider, event_key=key, payload={"from": "masked", "id": m.provider_message_id}))
        _inbound(db, m.from_number, m.text, m.provider_message_id, m.received_at, m.profile_name, settings)
        counts["messages"] += 1
    for st in batch.statuses:
        key = f"status:{st.provider_message_id}:{st.status}"
        if db.scalar(select(WebhookEvent.id).where(WebhookEvent.event_key == key)):
            counts["duplicates"] += 1
            continue
        db.add(WebhookEvent(provider=provider, event_key=key, payload={"status": st.status, "code": st.error_code}))
        _status(db, st.provider_message_id, st.status, st.at, st.error, st.error_code)
        counts["statuses"] += 1
    for tu in batch.template_updates:
        t = db.scalar(select(WaTemplate).where(WaTemplate.name == tu.get("message_template_name")))
        if t:
            t.status = {"APPROVED": "approved", "REJECTED": "rejected", "PAUSED": "paused", "DISABLED": "disabled"}.get(
                str(tu.get("event", "")).upper(), t.status
            )
            t.rejection_reason = tu.get("reason")
    db.commit()
    event("whatsapp_webhook", provider=provider, **counts)
    return counts


def _status(db: Session, pid: str, status: str, at: datetime, error: str | None, code: str | None) -> None:
    msg = db.scalar(select(WaMessage).where(WaMessage.provider_message_id == pid))
    if msg is None:
        return
    if status == "failed":
        msg.status, msg.error = "failed", f"{error or 'Failed'}{f' (code {code})' if code else ''}"[:500]
        _activity(db, msg.conversation, f"WhatsApp message failed: {msg.error}", msg=msg, direction="out", typ="note")
        return
    if STATUS_ORDER.get(status, -1) > STATUS_ORDER.get(msg.status, -1):  # statuses only move forward
        msg.status = status
        if status == "delivered":
            msg.delivered_at = at
        if status == "read":
            msg.read_at = at
            msg.delivered_at = msg.delivered_at or at


def _inbound(
    db: Session, number: str, text: str, pid: str, at: datetime, name: str | None, settings: Settings | None
) -> WaMessage:
    conv = conversation_for(db, number, name=name)
    msg = WaMessage(
        conversation_id=conv.id, direction="in", kind="text", body=text, status="received",
        provider_message_id=pid, idempotency_key=f"in-{pid}"[:80], created_at=at,
    )  # fmt: skip
    db.add(msg)
    conv.last_inbound_at, conv.unread = at, conv.unread + 1
    db.flush()
    contact = db.get(Contact, conv.contact_id) if conv.contact_id else None
    if STOP_WORDS.match(text) or OPT_OUT.search(text):
        msg.label, msg.label_confidence = "not_interested", 95
        _opt_out(db, conv, msg, f"Sent '{text[:60]}' on WhatsApp")
        return msg
    c = classify_rules("", text)
    msg.label, msg.label_confidence = c.label, c.confidence
    src = _activity(
        db, conv, f"WhatsApp from {contact.full_name if contact else (name or 'unknown number')}: {c.label.replace('_', ' ')}",
        msg=msg, direction="in", body=text,
    )  # fmt: skip
    msg.suggestion = suggest(db, conv, msg)
    if conv.state == "closed" and not C.phone_suppressed(db, number):
        conv.state = "open"
    wants_person = bool(HANDOFF_WORDS.search(text)) or c.label == "unclear" and len(text) > 80
    if c.label == "out_of_office":
        return msg
    if contact:
        from app.outreach.service import stop_all_for_contact

        stop_all_for_contact(db, contact.id, "replied", "Replied on WhatsApp")
    lead = db.get(Lead, conv.lead_id) if conv.lead_id else None
    if lead and contact:
        F.on_reply(
            db, lead=lead, contact_id=contact.id, contact_name=contact.full_name, label=c.label, text=text,
            received_at=at, source=src, key=f"wa:{msg.id}", channel="WhatsApp",
        )  # fmt: skip
    if wants_person and conv.state != "handoff":
        handoff(
            db,
            conv,
            None,
            "They asked for a person" if HANDOFF_WORDS.search(text) else "Message needs a person to read it",
            source=src,
        )
    if lead and contact and c.label in ("meeting_request", "interested") and conv.state != "handoff":
        from app.meetings import service as M

        M.propose(
            db,
            lead=lead,
            contact=contact,
            source="whatsapp",
            source_activity=src,
            request_text=text,
            wa_message=msg,
            intent=c.label,
        )
    return msg


REPLY_SUGGESTIONS = {
    "meeting_request": "Thanks {name} — happy to set up a short call. Which day and time suit you this week or next?",
    "interested": "Thanks {name}. Would a short call help? Which day and time would suit you?",
    "needs_information": "Thanks for asking, {name}. [EDIT: answer their question using approved information]",
    "not_now": "Understood, {name} — thank you. I'll get back in touch later. Is there a better time?",
    "not_interested": "Thanks for letting me know, {name}. I won't follow up further.",
    "referral": "Thank you, {name}. Who would be the best person to speak to, and is it alright to mention you suggested it?",
    "unclear": "Thanks for your message, {name}. [EDIT: respond to what they said]",
}


def suggest(db: Session, conv: WaConversation, msg: WaMessage) -> str | None:
    """A starting point for a person to edit. Takes the meeting already in progress into account, so it never
    proposes a meeting that is booked or mentions times that aren't in the message."""
    if msg.label == "out_of_office":
        return None
    contact = db.get(Contact, conv.contact_id) if conv.contact_id else None
    name = contact.greeting_name if contact else "there"
    if contact and msg.label in ("meeting_request", "interested", "needs_information", "unclear"):
        from app.meetings.service import fmt_slot
        from app.models import Meeting

        m = db.scalars(
            select(Meeting).where(Meeting.contact_id == contact.id, Meeting.status.in_(C.ARRANGING)).order_by(Meeting.id.desc())
        ).first()
        if m and m.start_at and m.status in ("scheduled", "confirmed"):
            return (
                f"Thanks {name} — looking forward to our call on {fmt_slot(m.start_at, m.timezone)}. [EDIT: answer their message]"
            )
        if m and m.proposed_slots:
            times = "; ".join(fmt_slot(datetime.fromisoformat(s["start"]), m.timezone) for s in m.proposed_slots[:3])
            return f"Thanks {name} — happy to. Would one of these suit you? {times}"
    return REPLY_SUGGESTIONS.get(msg.label or "unclear", REPLY_SUGGESTIONS["unclear"]).format(name=name)


# ---------------------------------------------------------------------------
# handoff
# ---------------------------------------------------------------------------
def handoff(
    db: Session, conv: WaConversation, user: User | None, reason: str, *, assign_to: int | None = None, source: Any = None
) -> WaConversation:
    lead = db.get(Lead, conv.lead_id) if conv.lead_id else None
    owner = assign_to or F.task_owner(db, lead, user.id if user else None)
    conv.state, conv.assigned_to_id, conv.handoff_reason = "handoff", owner, reason[:300]
    src = source or _activity(
        db,
        conv,
        f"WhatsApp handed to a person: {reason}",
        msg=None,
        direction="internal",
        user_id=user.id if user else None,
        typ="note",
    )
    if source is not None:
        _activity(
            db,
            conv,
            f"WhatsApp handed to a person: {reason}",
            msg=None,
            direction="internal",
            user_id=user.id if user else None,
            typ="note",
        )
    F.ensure_task(
        db,
        auto_key=f"handoff:{conv.id}:{src.id}",
        title=f"Take over the WhatsApp conversation with {conv.profile_name or conv.wa_number}",
        reason="whatsapp_handoff",
        due_at=datetime.now(UTC) + timedelta(hours=2),
        lead=lead,
        source_activity=src,
        contact_id=conv.contact_id,
        priority="urgent",
        description=reason,
        actor_id=owner,
    )
    if user:
        audit.record(db, user, "handoff", "wa_conversation", conv.id, {"assigned_to": owner, "reason": reason})
    event("whatsapp_handoff", conversation_id=conv.id, assigned_to=owner)
    return conv


def release(db: Session, conv: WaConversation, user: User) -> WaConversation:
    conv.state, conv.assigned_to_id, conv.handoff_reason = "open", None, None
    _activity(
        db, conv, "WhatsApp conversation returned to the assistant", msg=None, direction="internal", user_id=user.id, typ="note"
    )
    audit.record(db, user, "release", "wa_conversation", conv.id, {})
    return conv


def load_conversation(db: Session, conv_id: int) -> WaConversation:
    conv = db.scalars(
        select(WaConversation)
        .where(WaConversation.id == conv_id)
        .options(
            selectinload(WaConversation.messages), selectinload(WaConversation.contact), selectinload(WaConversation.assigned_to)
        )
    ).first()
    if conv is None:
        raise HTTPException(404, "Conversation not found")
    return conv
