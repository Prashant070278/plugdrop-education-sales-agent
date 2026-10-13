"""Outreach sequences: generate → edit → approve → schedule → send/draft → track → stop."""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app import audit
from app.config import Settings, get_settings
from app.models import (
    CLOSED_STAGES,
    Activity,
    ConsentRecord,
    Contact,
    ContentItem,
    EmailEvent,
    EmailReply,
    EmailTemplate,
    Lead,
    Mailbox,
    OutreachMessage,
    OutreachSequence,
    ResearchFinding,
    ResearchRun,
    Task,
    User,
)
from app.outreach import writer as W
from app.outreach.classify import classify
from app.outreach.policy import load_policy
from app.outreach.providers.base import EmailProviderError, OutgoingEmail, provider_for
from app.research.scoring import merged_config
from app.services import suppressed_contact_ids

log = logging.getLogger("plugdrop.outreach")
LIVE = ("draft", "active")
UNSENT = ("draft", "approved", "scheduled", "blocked")
SENT_LIKE = ("sent", "delivered", "drafted_in_mailbox")
EARLY_STAGES = ("new", "researched", "qualified", "outreach_ready")
STOP_LABELS = {"replied", "opted_out", "bounced", "suppressed", "not_a_fit", "cancelled", "lead_closed"}


# ---------------------------------------------------------------------------
# logging
# ---------------------------------------------------------------------------
def log_event(
    db: Session,
    seq: OutreachSequence | None,
    msg: OutreachMessage | None,
    event: str,
    actor: User | None = None,
    activity: tuple[str, str] | None = None,
    **data: Any,
) -> None:
    db.add(
        EmailEvent(
            sequence_id=seq.id if seq else None,
            message_id=msg.id if msg else None,
            event=event,
            actor_id=actor.id if actor else None,
            data=data,
        )
    )
    if activity and seq:
        typ, subject = activity
        db.add(
            Activity(
                activity_type=typ,
                subject=subject[:300],
                body=data.get("body"),
                lead_id=seq.lead_id,
                institution_id=seq.institution_id,
                contact_id=seq.contact_id,
                user_id=actor.id if actor else None,
                details={"outreach_sequence_id": seq.id, "message_id": msg.id if msg else None, "event": event},
                is_demo=seq.is_demo,
            )
        )
    log.info("outreach seq=%s msg=%s %s %s", seq.id if seq else None, msg.id if msg else None, event, data)


# ---------------------------------------------------------------------------
# eligibility and context
# ---------------------------------------------------------------------------
def approved_facts(db: Session, institution_id: int, stale_days: int) -> list[W.Fact]:
    cutoff = datetime.now(UTC) - timedelta(days=stale_days)
    rows = db.scalars(
        select(ResearchFinding)
        .where(
            ResearchFinding.institution_id == institution_id,
            ResearchFinding.research_evidence_id.is_not(None),
            ResearchFinding.evidence_level.in_(["verified_fact", "source_inference"]),
            or_(ResearchFinding.published_at.is_(None), ResearchFinding.published_at >= cutoff),
        )
        .order_by(ResearchFinding.evidence_level.desc(), ResearchFinding.confidence.desc())
    )
    from app.models import ResearchEvidence

    out = []
    for f in rows:
        ev = db.get(ResearchEvidence, f.research_evidence_id) if f.research_evidence_id else None
        if ev is None or ev.kind != "signal":
            continue
        out.append(
            W.Fact(
                id=f"F{f.id}",
                category=ev.category,
                level=f.evidence_level or "source_inference",
                text=f.summary,
                excerpt=f.excerpt,
                source_url=f.source_url,
                source_title=ev.source_title,
                published_at=f.published_at.date().isoformat() if f.published_at else None,
            )
        )
    return out


def approved_content(db: Session) -> list[W.Content]:
    return [
        W.Content(id=f"C{c.id}", kind=c.kind, title=c.title, body=c.body, use_cases=list(c.use_cases))
        for c in db.scalars(select(ContentItem).where(ContentItem.status == "approved").order_by(ContentItem.id))
    ]


def pushed_run(db: Session, institution_id: int) -> ResearchRun | None:
    return db.scalars(
        select(ResearchRun)
        .where(
            ResearchRun.institution_id == institution_id, ResearchRun.status == "succeeded", ResearchRun.pushed_at.is_not(None)
        )
        .order_by(ResearchRun.finished_at.desc())
    ).first()


def eligibility(db: Session, lead: Lead, contact: Contact) -> list[str]:
    problems = []
    if contact.institution_id != lead.institution_id:
        problems.append("Contact does not belong to the lead's institution")
    if lead.stage in {s.value for s in CLOSED_STAGES}:
        problems.append(f"Lead is {lead.stage}")
    if not contact.email:
        problems.append("Contact has no email address")
    if contact.verification_status in ("bounced", "invalid"):
        problems.append(f"Contact email is {contact.verification_status}")
    if contact.id in suppressed_contact_ids(db, [contact]):
        problems.append("Contact is opted out or suppressed")
    if pushed_run(db, lead.institution_id) is None:
        problems.append("No approved research has been pushed to the CRM for this institution")
    stale = merged_config(None)["evidence_stale_days"]
    if not approved_facts(db, lead.institution_id, stale):
        problems.append("No approved, current research facts for this institution")
    live = db.scalar(
        select(OutreachSequence.id).where(OutreachSequence.contact_id == contact.id, OutreachSequence.status.in_(LIVE))
    )
    if live:
        problems.append(f"Contact already has an outreach sequence in progress (#{live})")
    return problems


def _context(
    db: Session, seq: OutreachSequence, step: int, kind: str, sender: User | None, reply: EmailReply | None = None
) -> W.Context:
    policy = load_policy(db)
    contact = seq.contact
    previous = [
        {"step": str(m.step), "subject": m.subject, "body": m.body}
        for m in seq.messages
        if m.kind == "sequence" and m.step < step
    ]
    tmpl = (
        db.scalars(
            select(EmailTemplate)
            .where(
                EmailTemplate.status == "approved",
                EmailTemplate.step == step,
                or_(EmailTemplate.use_case == seq.use_case, EmailTemplate.use_case.is_(None)),
            )
            .order_by(EmailTemplate.use_case.is_(None))
        ).first()
        if kind == "sequence"
        else None
    )
    mailbox = seq.mailbox
    return W.Context(
        institution=seq.institution.name,
        first_name=contact.greeting_name,
        contact_name=contact.full_name,
        designation=contact.designation,
        sender_name=(mailbox.display_name if mailbox else None) or (sender.full_name if sender else "PlugDrop.AI"),
        use_case=seq.use_case,
        facts=[W.Fact(**f) for f in seq.facts],
        content=approved_content(db),
        policy=policy,
        step=step,
        kind=kind,
        previous=previous,
        template={"subject": tmpl.subject, "body": tmpl.body} if tmpl else None,
        reply={"label": reply.label, "subject": reply.subject, "body": reply.body[:3000]} if reply else None,
    )


def _write(
    db: Session,
    seq: OutreachSequence,
    msg: OutreachMessage,
    user: User | None,
    reply: EmailReply | None = None,
    settings: Settings | None = None,
    writer: Any = None,
) -> None:
    ctx = _context(db, seq, msg.step, msg.kind, user, reply)
    try:
        draft = (writer or W.get_writer(settings or get_settings())).write(ctx)
    except W.WriterError as e:
        raise HTTPException(422 if not e.transient else 503, str(e)) from e
    msg.subject, msg.body = draft.subject, W.finalize(ctx, draft.body)
    msg.generated_by, msg.edited = draft.generated_by, False
    msg.generation = {
        "facts_used": draft.facts_used,
        "content_used": draft.content_used,
        "rationale": draft.rationale,
        "input_tokens": draft.input_tokens,
        "output_tokens": draft.output_tokens,
        "cost_usd": round(draft.cost_usd, 6),
    }
    msg.quality = W.quality_check(ctx, msg.subject, msg.body, draft.facts_used, draft.content_used)


def recheck(db: Session, msg: OutreachMessage) -> dict[str, Any]:
    seq = msg.sequence
    ctx = _context(db, seq, msg.step, msg.kind, None)
    g = msg.generation or {}
    msg.quality = W.quality_check(ctx, msg.subject, msg.body, g.get("facts_used", []), g.get("content_used", []))
    return msg.quality


# ---------------------------------------------------------------------------
# sequences
# ---------------------------------------------------------------------------
def create_sequence(
    db: Session, user: User, lead_id: int, contact_id: int, mailbox_id: int, use_case: str | None = None, writer: Any = None
) -> OutreachSequence:
    lead = db.get(Lead, lead_id)
    contact = db.get(Contact, contact_id)
    mailbox = db.get(Mailbox, mailbox_id)
    if lead is None or contact is None:
        raise HTTPException(404, "Lead or contact not found")
    problems = eligibility(db, lead, contact)
    if problems:
        raise HTTPException(422, "Not ready for outreach: " + "; ".join(problems))
    check_mailbox_use(mailbox, user)
    assert mailbox is not None
    policy = load_policy(db)
    run = pushed_run(db, lead.institution_id)
    stale = merged_config(None)["evidence_stale_days"]
    facts = approved_facts(db, lead.institution_id, stale)
    seq = OutreachSequence(
        lead_id=lead.id,
        institution_id=lead.institution_id,
        contact_id=contact.id,
        research_run_id=run.id if run else None,
        mailbox_id=mailbox.id,
        use_case=use_case or (run.summary.get("primary_use_case") if run else None),
        max_attempts=policy["max_attempts"],
        spacing_days=list(policy["spacing_days"][: policy["max_attempts"]]),
        facts=[f.__dict__ for f in facts],
        created_by_id=user.id,
        is_demo=lead.is_demo,
    )
    db.add(seq)
    db.flush()
    db.refresh(seq)
    offset = 0
    for step in range(1, seq.max_attempts + 1):
        offset += seq.spacing_days[step - 1] if step > 1 else 0
        msg = OutreachMessage(
            sequence_id=seq.id,
            step=step,
            kind="sequence",
            to_email=contact.email or "",
            subject="",
            body="",
            generated_by="",
            idempotency_key=f"seq{seq.id}-sequence-{step}",
            planned_offset_days=offset,
        )
        db.add(msg)
        db.flush()
        db.refresh(seq)
        _write(db, seq, msg, user, writer=writer)
        log_event(db, seq, msg, "generated", user, generated_by=msg.generated_by, quality_passed=msg.quality.get("passed"))
    audit.record(
        db,
        user,
        "create",
        "outreach_sequence",
        seq.id,
        {"lead": lead.id, "contact": contact.id, "mailbox": mailbox.id, "use_case": seq.use_case, "steps": seq.max_attempts},
    )
    log_event(
        db,
        seq,
        None,
        "sequence_created",
        user,
        activity=("note", f"Outreach sequence drafted for {contact.full_name} ({seq.max_attempts} emails)"),
    )
    db.commit()
    return seq


def check_mailbox_use(mailbox: Mailbox | None, user: User) -> None:
    if mailbox is None:
        raise HTTPException(422, "Choose a connected mailbox")
    if mailbox.status != "active":
        raise HTTPException(
            422, f"Mailbox {mailbox.email_address} is {mailbox.status.replace('_', ' ')}; an admin must approve it first"
        )
    if not mailbox.is_shared and mailbox.owner_id != user.id:
        raise HTTPException(403, "This is someone else's personal mailbox. Only its owner can send from it.")


def edit_message(db: Session, msg: OutreachMessage, user: User, subject: str, body: str) -> OutreachMessage:
    if msg.status not in UNSENT:
        raise HTTPException(409, f"A {msg.status} email can't be edited")
    before = {"subject": msg.subject, "body": msg.body, "status": msg.status}
    msg.subject, msg.body, msg.edited = subject.strip(), body.strip(), True
    if msg.status != "draft":
        msg.status, msg.approved_at, msg.approved_by_id, msg.scheduled_for = "draft", None, None, None  # edits need re-approval
    recheck(db, msg)
    log_event(db, msg.sequence, msg, "edited", user, previous_status=before["status"], quality_passed=msg.quality["passed"])
    audit.record(
        db,
        user,
        "update",
        "outreach_message",
        msg.id,
        audit.diff(before, {"subject": msg.subject, "body": msg.body, "status": msg.status}),
    )
    db.commit()
    return msg


def regenerate(db: Session, msg: OutreachMessage, user: User, writer: Any = None) -> OutreachMessage:
    if msg.status not in UNSENT:
        raise HTTPException(409, f"A {msg.status} email can't be regenerated")
    reply = (
        db.scalars(
            select(EmailReply).where(EmailReply.sequence_id == msg.sequence_id).order_by(EmailReply.received_at.desc())
        ).first()
        if msg.kind == "reply"
        else None
    )
    _write(db, msg.sequence, msg, user, reply=reply, writer=writer)
    msg.status, msg.approved_at, msg.approved_by_id, msg.scheduled_for = "draft", None, None, None
    log_event(db, msg.sequence, msg, "regenerated", user, quality_passed=msg.quality["passed"])
    db.commit()
    return msg


def _assert_sendable_sequence(seq: OutreachSequence, msg: OutreachMessage) -> None:
    if msg.kind == "sequence" and seq.status not in LIVE:
        raise HTTPException(409, f"Sequence is {seq.status} ({seq.stop_reason or ''}); follow-ups are stopped")
    if msg.kind == "reply" and seq.stop_reason in ("opted_out", "suppressed", "bounced"):
        raise HTTPException(409, f"Can't reply: contact {seq.stop_reason.replace('_', ' ')}")


def approve(db: Session, msg: OutreachMessage, user: User) -> OutreachMessage:
    seq = msg.sequence
    _assert_sendable_sequence(seq, msg)
    if msg.status != "draft":
        raise HTTPException(409, f"Only drafts can be approved (this one is {msg.status})")
    q = recheck(db, msg)
    if not q["passed"]:
        failed = [c["label"] for c in q["checks"] if c["blocking"] and not c["passed"]]
        raise HTTPException(422, "Fix these before approving: " + "; ".join(failed))
    contact = seq.contact
    if contact.id in suppressed_contact_ids(db, [contact]):
        stop_sequence(db, seq, "suppressed", None, "Suppressed at approval")
        db.commit()
        raise HTTPException(409, "Contact is opted out or suppressed; the sequence was stopped")
    msg.status, msg.approved_by_id, msg.approved_at = "approved", user.id, datetime.now(UTC)
    if seq.status == "draft":
        seq.status = "active"
    log_event(db, seq, msg, "approved", user)
    audit.record(db, user, "approve", "outreach_message", msg.id, {"step": msg.step})
    db.commit()
    return msg


def schedule(db: Session, msg: OutreachMessage, user: User, when: datetime | None) -> OutreachMessage:
    seq = msg.sequence
    _assert_sendable_sequence(seq, msg)
    if msg.status not in ("approved", "scheduled", "blocked"):
        raise HTTPException(409, "Approve the email before scheduling it")
    if when and when < datetime.now(UTC) - timedelta(minutes=5):
        raise HTTPException(422, "Scheduled time is in the past")
    prev = _previous(seq, msg)
    if when is None and prev is not None and prev.status not in SENT_LIKE:
        when = None  # scheduled automatically once the previous email goes out
    elif when is None:
        anchor = prev.sent_at if prev and prev.sent_at else datetime.now(UTC)
        when = anchor + timedelta(days=seq.spacing_days[msg.step - 1] if (prev and msg.kind == "sequence") else 0)
    msg.status, msg.scheduled_for, msg.last_error = "scheduled", when, None
    log_event(db, seq, msg, "scheduled", user, scheduled_for=when.isoformat() if when else "after previous email")
    db.commit()
    return msg


def cancel_message(db: Session, msg: OutreachMessage, user: User) -> OutreachMessage:
    if msg.status not in UNSENT:
        raise HTTPException(409, f"A {msg.status} email can't be cancelled")
    msg.status = "cancelled"
    log_event(db, msg.sequence, msg, "cancelled", user)
    audit.record(db, user, "cancel", "outreach_message", msg.id, {})
    db.commit()
    return msg


def stop_sequence(db: Session, seq: OutreachSequence, reason: str, user: User | None, note: str | None = None) -> None:
    if reason not in STOP_LABELS:
        raise HTTPException(422, f"Unknown stop reason {reason}")
    if seq.status in ("stopped", "completed") and seq.stop_reason == reason:
        return
    seq.status, seq.stop_reason, seq.stopped_at = "stopped", reason, datetime.now(UTC)
    cancelled = 0
    for m in seq.messages:
        if m.status in UNSENT and m.kind == "sequence":
            m.status, cancelled = "cancelled", cancelled + 1
    log_event(
        db,
        seq,
        None,
        "sequence_stopped",
        user,
        activity=("note", f"Outreach stopped: {reason.replace('_', ' ')}"),
        reason=reason,
        note=note,
        cancelled_messages=cancelled,
    )
    if reason == "not_a_fit":
        lead = seq.lead
        if lead.stage not in {s.value for s in CLOSED_STAGES}:
            old = lead.stage
            lead.stage, lead.lost_reason, lead.stage_changed_at = "lost", "Not a fit (outreach)", datetime.now(UTC)
            db.add(
                Activity(
                    activity_type="status_change",
                    subject=f"Stage changed: {old.replace('_', ' ')} → lost",
                    body=note or "Marked not a fit from outreach",
                    lead_id=lead.id,
                    institution_id=lead.institution_id,
                    user_id=user.id if user else None,
                    details={"from": old, "to": "lost"},
                    is_demo=lead.is_demo,
                )
            )
    if user:
        audit.record(db, user, "stop", "outreach_sequence", seq.id, {"reason": reason, "note": note})


def stop_all_for_contact(db: Session, contact_id: int, reason: str, note: str) -> int:
    seqs = db.scalars(
        select(OutreachSequence).where(OutreachSequence.contact_id == contact_id, OutreachSequence.status.in_(LIVE))
    ).all()
    for s in seqs:
        stop_sequence(db, s, reason, None, note)
    return len(seqs)


def reply_draft(db: Session, reply: EmailReply, user: User, writer: Any = None) -> OutreachMessage:
    seq = reply.sequence
    if seq is None:
        raise HTTPException(422, "This reply isn't linked to a sequence")
    if reply.opt_out or reply.label in ("not_interested", "out_of_office"):
        raise HTTPException(
            422, f"No reply draft for a '{reply.label.replace('_', ' ')}' reply" + (" (opted out)" if reply.opt_out else "")
        )
    if seq.stop_reason in ("opted_out", "suppressed", "bounced"):
        raise HTTPException(409, f"Contact {seq.stop_reason.replace('_', ' ')}; replying is blocked")
    step = 1 + max((m.step for m in seq.messages if m.kind == "reply"), default=0)
    msg = OutreachMessage(
        sequence_id=seq.id,
        step=step,
        kind="reply",
        to_email=seq.contact.email or reply.from_email,
        subject="",
        body="",
        generated_by="",
        idempotency_key=f"seq{seq.id}-reply-{step}",
    )
    db.add(msg)
    db.flush()
    db.refresh(seq)
    _write(db, seq, msg, user, reply=reply, writer=writer)
    log_event(db, seq, msg, "reply_draft_generated", user, reply_id=reply.id, label=reply.label)
    db.commit()
    return msg


# ---------------------------------------------------------------------------
# dispatch (send / draft) with pre-send checks
# ---------------------------------------------------------------------------
def _previous(seq: OutreachSequence, msg: OutreachMessage) -> OutreachMessage | None:
    if msg.kind != "sequence" or msg.step == 1:
        return None
    return next((m for m in seq.messages if m.kind == "sequence" and m.step == msg.step - 1), None)


def in_window(dt: datetime, policy: dict[str, Any]) -> bool:
    w = policy["send_window"]
    local = dt.astimezone(ZoneInfo(w["timezone"]))
    return (not w["weekdays_only"] or local.weekday() < 5) and w["start_hour"] <= local.hour < w["end_hour"]


def next_window(dt: datetime, policy: dict[str, Any]) -> datetime:
    w = policy["send_window"]
    tz = ZoneInfo(w["timezone"])
    local = dt.astimezone(tz)
    for _ in range(14):
        if in_window(local, policy):
            return local.astimezone(UTC)
        if local.hour >= w["end_hour"] or (w["weekdays_only"] and local.weekday() >= 5):
            local = (local + timedelta(days=1)).replace(hour=w["start_hour"], minute=0, second=0, microsecond=0)
        else:
            local = local.replace(hour=w["start_hour"], minute=0, second=0, microsecond=0)
    return local.astimezone(UTC)


def effective_mode(mailbox: Mailbox, policy: dict[str, Any], s: Settings) -> tuple[str, str]:
    """Return ("send" | "draft", reason)."""
    if policy["sending_mode"] != "send":
        return "draft", "Policy is draft-only"
    if not mailbox.sending_enabled:
        return "draft", "Sending is not enabled for this mailbox"
    if mailbox.provider != "mock" and not s.outreach_external_sending_allowed:
        return "draft", "External sending is switched off (OUTREACH_EXTERNAL_SENDING_ALLOWED=false)"
    return "send", "Sending enabled"


def _sent_today(db: Session, *conds: Any) -> int:
    start = datetime.now(UTC) - timedelta(hours=24)
    return (
        db.scalar(
            select(func.count())
            .select_from(OutreachMessage)
            .join(OutreachSequence)
            .where(OutreachMessage.sent_at >= start, OutreachMessage.status.in_(SENT_LIKE), *conds)
        )
        or 0
    )


def presend(db: Session, msg: OutreachMessage, policy: dict[str, Any], now: datetime) -> tuple[str, str]:
    """Decide what to do with a due message: ("ok"|"defer"|"block"|"stop", reason)."""
    seq = msg.sequence
    contact = seq.contact
    if msg.kind == "sequence" and seq.status not in LIVE:
        return "block", f"Sequence {seq.status}"
    if contact.id in suppressed_contact_ids(db, [contact]):
        return "stop:suppressed", "Contact is opted out or suppressed"
    if contact.verification_status in ("bounced", "invalid"):
        return "stop:bounced", f"Contact email is {contact.verification_status}"
    if msg.kind == "sequence" and seq.lead.stage in {s.value for s in CLOSED_STAGES}:
        return "stop:lead_closed", f"Lead is {seq.lead.stage}"
    if msg.kind == "sequence" and any(r for r in seq.replies if r.label != "out_of_office"):
        return "stop:replied", "The prospect has replied"
    if msg.to_email.lower() != (contact.email or "").lower():
        return "block", "Recipient differs from the contact's current email; regenerate the draft"
    mb = seq.mailbox
    if mb is None or mb.status != "active":
        return "block", "Mailbox missing or not approved"
    prev = _previous(seq, msg)
    if prev is not None and prev.status not in SENT_LIKE:
        return "defer", "Waiting for the previous email"
    if not in_window(now, policy):
        return "defer", "Outside the sending window"
    if _sent_today(db, OutreachSequence.mailbox_id == mb.id) >= mb.daily_send_limit:
        return "defer", "Mailbox daily limit reached"
    if _sent_today(db) >= policy["global_daily_limit"]:
        return "defer", "Global daily limit reached"
    if msg.kind == "reply":
        # A reply answers the prospect; cold-outreach pacing (per-domain, 20-hour gap) does not apply.
        return "ok", ""
    domain = (contact.email or "").split("@")[-1].lower()
    if _sent_today(db, func.lower(OutreachMessage.to_email).like(f"%@{domain}")) >= policy["per_domain_daily_limit"]:
        return "defer", f"Daily limit for {domain} reached"
    if db.scalar(
        select(func.count())
        .select_from(OutreachMessage)
        .where(
            OutreachMessage.id != msg.id,
            func.lower(OutreachMessage.to_email) == msg.to_email.lower(),
            OutreachMessage.status.in_(SENT_LIKE),
            OutreachMessage.sent_at >= now - timedelta(hours=20),
        )
    ):
        return "defer", "This person already received an email from us in the last 20 hours"
    return "ok", ""


def dispatch_message(db: Session, msg: OutreachMessage, settings: Settings | None = None, now: datetime | None = None) -> str:
    """Process one due message. Caller holds the row lock. Returns the resulting status."""
    s = settings or get_settings()
    now = now or datetime.now(UTC)
    policy = load_policy(db)
    seq = msg.sequence
    verdict, reason = presend(db, msg, policy, now)
    if verdict.startswith("stop:"):
        stop_sequence(db, seq, verdict.split(":", 1)[1], None, reason)
        if msg.kind == "reply":
            msg.status = "blocked"
        msg.last_error = reason
        log_event(db, seq, msg, "blocked", None, reason=reason)
        return msg.status
    if verdict == "block":
        msg.status, msg.last_error = "blocked", reason
        log_event(db, seq, msg, "blocked", None, reason=reason)
        return msg.status
    if verdict == "defer":
        msg.last_error = reason
        if reason == "Outside the sending window":
            msg.scheduled_for = next_window(now, policy)
        elif reason == "Waiting for the previous email":
            msg.scheduled_for = None
        else:
            msg.scheduled_for = (
                next_window(now + timedelta(hours=24), policy)
                if "limit" in reason or "20 hours" in reason
                else now + timedelta(hours=1)
            )
        log_event(
            db, seq, msg, "deferred", None, reason=reason, until=msg.scheduled_for.isoformat() if msg.scheduled_for else None
        )
        return msg.status
    mb = seq.mailbox
    assert mb is not None
    mode, why = effective_mode(mb, policy, s)
    # Duplicate-send protection: mark as sending and commit before talking to the provider.
    msg.status, msg.send_attempts = "sending", msg.send_attempts + 1
    db.commit()
    prev = _previous(seq, msg)
    out = OutgoingEmail(
        from_email=mb.email_address,
        from_name=mb.display_name,
        to_email=msg.to_email,
        subject=msg.subject,
        body=msg.body,
        thread_id=(prev.thread_id if prev else None) or _last_thread(seq),
        in_reply_to=prev.provider_message_id if prev and mb.provider == "mock" else None,
        idempotency_key=msg.idempotency_key,
    )
    try:
        prov = provider_for(db, mb)
        res = prov.send(out) if mode == "send" else prov.create_draft(out)
    except EmailProviderError as e:
        if e.needs_reauth:
            mb.status, mb.last_error = "needs_reauth", str(e)[:500]
        if e.transient and msg.send_attempts < 3:
            msg.status, msg.scheduled_for, msg.last_error = (
                "scheduled",
                now + timedelta(minutes=5 * 2**msg.send_attempts),
                str(e)[:500],
            )
            log_event(db, seq, msg, "send_retry", None, error=str(e))
        else:
            msg.status, msg.last_error = "failed", str(e)[:500]
            log_event(db, seq, msg, "send_failed", None, activity=("note", f"Email to {msg.to_email} failed"), error=str(e))
        return msg.status
    msg.provider, msg.provider_message_id, msg.thread_id = mb.provider, res.provider_message_id, res.thread_id
    msg.sent_at, msg.last_error = now, None
    if mode == "send":
        msg.status = "sent"
        log_event(
            db,
            seq,
            msg,
            "sent",
            None,
            activity=("email", f"Email sent: {msg.subject}"),
            body=msg.body,
            provider=mb.provider,
            to=msg.to_email,
            step=msg.step,
        )
        lead = seq.lead
        if msg.kind == "sequence" and lead.stage in EARLY_STAGES:
            old = lead.stage
            lead.stage, lead.stage_changed_at = "contacted", now
            db.add(
                Activity(
                    activity_type="status_change",
                    subject=f"Stage changed: {old.replace('_', ' ')} → contacted",
                    body="First outreach email sent",
                    lead_id=lead.id,
                    institution_id=lead.institution_id,
                    details={"from": old, "to": "contacted"},
                    is_demo=lead.is_demo,
                )
            )
    else:
        msg.status = "drafted_in_mailbox"
        log_event(
            db,
            seq,
            msg,
            "drafted_in_mailbox",
            None,
            activity=("note", f"Email placed in {mb.email_address} drafts for sending"),
            reason=why,
            provider=mb.provider,
        )
    nxt = next((m for m in seq.messages if m.kind == "sequence" and m.step == msg.step + 1), None)
    if nxt and nxt.status == "scheduled" and nxt.scheduled_for is None:
        nxt.scheduled_for = next_window(now + timedelta(days=seq.spacing_days[nxt.step - 1]), policy)
    finished = all(m.status in (*SENT_LIKE, "bounced", "cancelled") for m in seq.messages if m.kind == "sequence")
    if msg.kind == "sequence" and finished and seq.status == "active":
        seq.status = "completed"
    return msg.status


def _last_thread(seq: OutreachSequence) -> str | None:
    sent = [m for m in seq.messages if m.thread_id]
    return sent[-1].thread_id if sent else None


def dispatch_due(db: Session, settings: Settings | None = None, now: datetime | None = None, limit: int = 20) -> int:
    now = now or datetime.now(UTC)
    ids = list(
        db.scalars(
            select(OutreachMessage.id)
            .where(
                OutreachMessage.status == "scheduled",
                OutreachMessage.scheduled_for.is_not(None),
                OutreachMessage.scheduled_for <= now,
            )
            .order_by(OutreachMessage.scheduled_for)
            .limit(limit)
        )
    )
    done = 0
    for mid in ids:
        msg = db.scalars(
            select(OutreachMessage)
            .where(OutreachMessage.id == mid, OutreachMessage.status == "scheduled")
            .with_for_update(skip_locked=True)
        ).first()
        if msg is None:
            db.rollback()
            continue
        dispatch_message(db, msg, settings, now)
        db.commit()
        done += 1
    return done


def send_now(db: Session, msg: OutreachMessage, user: User, settings: Settings | None = None) -> OutreachMessage:
    if msg.status not in ("approved", "scheduled", "blocked"):
        raise HTTPException(409, "Approve the email first")
    _assert_sendable_sequence(msg.sequence, msg)
    msg.status, msg.scheduled_for = "scheduled", datetime.now(UTC)
    log_event(db, msg.sequence, msg, "send_now_requested", user)
    db.commit()
    locked = db.scalars(select(OutreachMessage).where(OutreachMessage.id == msg.id).with_for_update()).one()
    status = dispatch_message(db, locked, settings)
    db.commit()
    if status == "blocked":
        raise HTTPException(409, f"Not sent: {locked.last_error}")
    return locked


# ---------------------------------------------------------------------------
# sync: delivery results and replies
# ---------------------------------------------------------------------------
def sync_mailbox(db: Session, mb: Mailbox, settings: Settings | None = None) -> dict[str, int]:
    s = settings or get_settings()
    counts = {"delivered": 0, "bounced": 0, "replies": 0}
    try:
        res = provider_for(db, mb).sync(mb.sync_cursor)
    except EmailProviderError as e:
        mb.last_error = str(e)[:500]
        if e.needs_reauth:
            mb.status = "needs_reauth"
        db.commit()
        return counts
    for ev in res.delivery:
        msg = None
        if ev.provider_message_id:
            msg = db.scalar(select(OutreachMessage).where(OutreachMessage.provider_message_id == ev.provider_message_id))
        if msg is None and ev.to_email:
            msg = db.scalars(
                select(OutreachMessage)
                .where(
                    func.lower(OutreachMessage.to_email) == ev.to_email.lower(), OutreachMessage.status.in_(("sent", "delivered"))
                )
                .order_by(OutreachMessage.sent_at.desc())
            ).first()
        if msg is None or msg.status not in ("sent", "delivered"):
            continue
        seq = msg.sequence
        if ev.status == "delivered" and msg.status == "sent":
            msg.status, msg.delivered_at = "delivered", ev.at or datetime.now(UTC)
            log_event(db, seq, msg, "delivered", None)
            counts["delivered"] += 1
        elif ev.status == "bounced":
            msg.status, msg.last_error = "bounced", ev.detail[:500] or "Bounced"
            contact = seq.contact
            contact.verification_status = "bounced"
            log_event(db, seq, msg, "bounced", None, activity=("note", f"Email to {msg.to_email} bounced"), detail=ev.detail)
            stop_sequence(db, seq, "bounced", None, ev.detail)
            counts["bounced"] += 1
    for inbound in res.inbound:
        if db.scalar(select(EmailReply.id).where(EmailReply.provider_message_id == inbound.provider_message_id)):
            continue
        handle_inbound(
            db,
            mb,
            inbound.provider_message_id,
            inbound.from_email,
            inbound.subject,
            inbound.body,
            inbound.received_at,
            inbound.thread_id,
            inbound.in_reply_to,
            s,
        )
        counts["replies"] += 1
    mb.sync_cursor, mb.last_sync_at, mb.last_error = res.cursor, datetime.now(UTC), None
    db.commit()
    return counts


def handle_inbound(
    db: Session,
    mb: Mailbox,
    provider_message_id: str,
    from_email: str,
    subject: str,
    body: str,
    received_at: datetime,
    thread_id: str | None,
    in_reply_to: str | None,
    s: Settings,
) -> EmailReply | None:
    msg = None
    if in_reply_to:
        msg = db.scalar(select(OutreachMessage).where(OutreachMessage.provider_message_id == in_reply_to))
    if msg is None and thread_id:
        msg = db.scalars(
            select(OutreachMessage).where(OutreachMessage.thread_id == thread_id).order_by(OutreachMessage.step.desc())
        ).first()
    seq = msg.sequence if msg else None
    if seq is None:
        contact = db.scalar(select(Contact).where(func.lower(Contact.email) == from_email.lower()))
        if contact:
            seq = db.scalars(
                select(OutreachSequence).where(OutreachSequence.contact_id == contact.id).order_by(OutreachSequence.id.desc())
            ).first()
    if seq is None:
        return None  # not related to outreach; ignore (no personal mail is stored)
    c = classify(subject, body, s)
    reply = EmailReply(
        sequence_id=seq.id,
        message_id=msg.id if msg else None,
        contact_id=seq.contact_id,
        mailbox_id=mb.id,
        provider_message_id=provider_message_id,
        from_email=from_email,
        subject=subject[:300],
        body=body[:20000],
        received_at=received_at,
        label=c.label,
        label_confidence=c.confidence,
        label_source=c.source,
        opt_out=c.opt_out,
        summary=c.summary,
    )
    db.add(reply)
    db.flush()
    db.refresh(seq)
    apply_reply(db, seq, reply, None)
    return reply


def apply_reply(db: Session, seq: OutreachSequence, reply: EmailReply, user: User | None) -> None:
    policy = load_policy(db)
    log_event(
        db,
        seq,
        None,
        "reply_received",
        user,
        activity=("reply", f"Reply from {seq.contact.full_name}: {reply.label.replace('_', ' ')}"),
        body=reply.body[:2000],
        label=reply.label,
        opt_out=reply.opt_out,
        reply_id=reply.id,
    )
    if reply.opt_out:
        db.add(
            ConsentRecord(
                contact_id=seq.contact_id,
                email=seq.contact.email,
                channel="email",
                status="opted_out",
                reason="Asked to unsubscribe in an email reply",
                source=f"Outreach reply #{reply.id}",
                is_demo=seq.is_demo,
            )
        )
        stop_all_for_contact(db, seq.contact_id, "opted_out", "Unsubscribe request in reply")
        stop_sequence(db, seq, "opted_out", user, "Unsubscribe request in reply")
        reply.handled, reply.handled_at = True, datetime.now(UTC)
        return
    if reply.label == "out_of_office":
        shift = timedelta(days=policy["ooo_postpone_days"])
        for m in seq.messages:
            if m.status == "scheduled" and m.scheduled_for:
                m.scheduled_for = m.scheduled_for + shift
        reply.handled, reply.handled_at = True, datetime.now(UTC)
        log_event(db, seq, None, "follow_ups_postponed", user, days=policy["ooo_postpone_days"])
        return
    stop_sequence(db, seq, "replied", user, f"Reply classified as {reply.label}")
    lead = seq.lead
    if reply.label in ("interested", "meeting_request", "needs_information", "referral") and lead.stage in (
        *EARLY_STAGES,
        "contacted",
    ):
        old = lead.stage
        lead.stage, lead.stage_changed_at = "engaged", datetime.now(UTC)
        db.add(
            Activity(
                activity_type="status_change",
                subject=f"Stage changed: {old.replace('_', ' ')} → engaged",
                body=f"Reply: {reply.label.replace('_', ' ')}",
                lead_id=lead.id,
                institution_id=lead.institution_id,
                details={"from": old, "to": "engaged"},
                is_demo=lead.is_demo,
            )
        )
    due = datetime.now(UTC) + timedelta(hours=4 if reply.label in ("meeting_request", "interested") else 24)
    db.add(
        Task(
            title=f"Respond to {seq.contact.full_name} ({reply.label.replace('_', ' ')})",
            due_at=due,
            priority="urgent" if reply.label == "meeting_request" else "high",
            owner_id=lead.owner_id,
            lead_id=lead.id,
            institution_id=lead.institution_id,
            contact_id=seq.contact_id,
            is_demo=seq.is_demo,
            description=f"Reply received to outreach sequence #{seq.id}. Review it in Outreach → Replies.",
        )
    )


def relabel(db: Session, reply: EmailReply, user: User, label: str, opt_out: bool) -> EmailReply:
    before = {"label": reply.label, "opt_out": reply.opt_out}
    reply.label, reply.label_source, reply.label_confidence = label, "human", 100
    if opt_out and not reply.opt_out and reply.sequence:
        reply.opt_out = True
        apply_reply(db, reply.sequence, reply, user)
    audit.record(db, user, "relabel", "email_reply", reply.id, {"from": before, "to": {"label": label, "opt_out": opt_out}})
    db.commit()
    return reply


def sync_all(db: Session, settings: Settings | None = None) -> dict[str, int]:
    total = {"delivered": 0, "bounced": 0, "replies": 0}
    for mb in db.scalars(select(Mailbox).where(Mailbox.status == "active")).all():
        for k, v in sync_mailbox(db, mb, settings).items():
            total[k] += v
    return total


# ---------------------------------------------------------------------------
# embedded worker
# ---------------------------------------------------------------------------
_started = False


def start_embedded_worker(session_factory: Any, interval: float = 15.0) -> None:
    global _started
    if _started:
        return
    _started = True

    def loop() -> None:
        while True:
            try:
                with session_factory() as db:
                    dispatch_due(db)
                    sync_all(db)
            except Exception:  # noqa: BLE001
                log.exception("outreach worker error")
            time.sleep(interval)

    threading.Thread(target=loop, name="outreach-worker", daemon=True).start()


def load_sequence(db: Session, seq_id: int) -> OutreachSequence:
    seq = db.scalars(
        select(OutreachSequence)
        .where(OutreachSequence.id == seq_id)
        .options(
            selectinload(OutreachSequence.messages),
            selectinload(OutreachSequence.replies),
            selectinload(OutreachSequence.contact),
            selectinload(OutreachSequence.institution),
            selectinload(OutreachSequence.lead),
            selectinload(OutreachSequence.mailbox),
        )
    ).first()
    if seq is None:
        raise HTTPException(404, f"Outreach sequence {seq_id} not found")
    return seq
