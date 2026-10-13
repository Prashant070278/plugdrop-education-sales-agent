"""Meeting assistant (Module B).

When a prospect shows interest (email or WhatsApp) the assistant:
1. reads what they asked for (intent + any day/time they mentioned),
2. suggests an agenda from the approved research and use case,
3. finds free times in the organiser's connected calendar,
4. drafts a reply with those times for a person to approve,
5. creates the calendar event only after a person approves the meeting,
6. records attendees, notes, outcome and next steps in the CRM.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.config import get_settings
from app.engagement import coordination as C
from app.engagement import followups as F
from app.engagement.logs import event
from app.engagement.policy import load_policy, require
from app.meetings.providers import CalendarError, MockCalendar, NewEvent, provider_for
from app.models import (
    Activity,
    CalendarConnection,
    Contact,
    EmailReply,
    Lead,
    Meeting,
    OutreachSequence,
    User,
    WaMessage,
)
from app.research.reasoning import USE_CASES

ARRANGING = C.ARRANGING
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)\b|\b([01]?\d|2[0-3]):([0-5]\d)\b", re.I)
PART_OF_DAY = {"morning": time(10, 30), "noon": time(12, 0), "afternoon": time(15, 0), "evening": time(16, 30)}


# ---------------------------------------------------------------------------
# understanding the request
# ---------------------------------------------------------------------------
def requested_times(text: str, now: datetime, tz: str) -> list[datetime]:
    """Day/time mentions such as 'Thursday at 3pm', 'tomorrow morning', '11:30 on Monday' → candidate start times."""
    z = ZoneInfo(tz)
    local_now = now.astimezone(z)
    low = text.lower()
    days: list[date] = []
    if "day after tomorrow" in low:
        days.append(local_now.date() + timedelta(days=2))
    elif "tomorrow" in low:
        days.append(local_now.date() + timedelta(days=1))
    if re.search(r"\btoday\b", low):
        days.append(local_now.date())
    for i, name in enumerate(WEEKDAYS):
        if re.search(rf"\b{name}\b|\b{name[:3]}\b", low):
            ahead = (i - local_now.weekday()) % 7 or 7
            if "next week" in low and ahead < 7:
                ahead += 7
            days.append(local_now.date() + timedelta(days=ahead))
    times: list[time] = []
    for m in TIME_RE.finditer(text):
        if m.group(1):
            h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3).lower().replace(".", "")
            if h > 12:
                continue
            h = (h % 12) + (12 if ap == "pm" else 0)
            times.append(time(h, mi))
        else:
            times.append(time(int(m.group(4)), int(m.group(5))))
    for word, t in PART_OF_DAY.items():
        if word in low:
            times.append(t)
    if not days and times and re.search(r"\bnext week\b", low):
        days = [local_now.date() + timedelta(days=(7 - local_now.weekday()))]
    out = []
    for d in days or ([] if not times else [local_now.date() + timedelta(days=1)]):
        for t in times or [time(11, 0)]:
            out.append(datetime.combine(d, t, z).astimezone(UTC))
    return sorted({o for o in out if o > now})


def agenda_for(
    db: Session, lead: Lead, contact: Contact | None, use_case: str | None, intent: str, request_text: str | None
) -> str:
    from app.outreach.service import approved_facts

    facts = approved_facts(db, lead.institution_id, 365)
    uc = USE_CASES.get(use_case or "", "how PlugDrop could help your admissions team")
    lines = [
        "1. Introductions and your goals for this admissions cycle (5 min)",
        f"2. How enquiries reach {lead.institution.name} today — phone, WhatsApp, web forms — and where they get stuck (10 min)",
        f"3. A short walkthrough: {uc} (10 min)",
        "4. Questions, and whether a pilot would be useful (5 min)",
    ]
    if intent == "needs_information":
        lines.insert(3, "   - Answer the questions you sent beforehand")
    notes = [f"- {f.text}" for f in facts[:3]]
    prep = ("\n\nPrep (internal — not shared): research to check before the call\n" + "\n".join(notes)) if notes else ""
    ask = f"\n\nThey wrote: “{request_text.strip()[:300]}”" if request_text else ""
    return "\n".join(lines) + ask + prep


def _organizer(db: Session, lead: Lead, actor: User | None) -> User:
    uid = F.task_owner(db, lead, actor.id if actor else None)
    u = db.get(User, uid)
    assert u is not None
    return u


def calendar_for(db: Session, user: User) -> tuple[CalendarConnection | None, Any]:
    conn = db.scalars(
        select(CalendarConnection)
        .where(CalendarConnection.user_id == user.id, CalendarConnection.status == "active")
        .order_by(CalendarConnection.provider != "mock")  # prefer a real calendar when there is one
        .order_by(CalendarConnection.id.desc())
    ).first()
    if conn:
        return conn, provider_for(db, conn)
    return None, None


def free_slots(
    db: Session, organizer: User, now: datetime, *, preferred: list[datetime] | None = None, count: int | None = None
) -> tuple[list[dict[str, Any]], str | None]:
    """Free times in working hours over the next N days, starting with any time the prospect asked for."""
    pol = load_policy(db)
    hours = pol["meeting_hours"]
    z = ZoneInfo(hours["timezone"])
    dur = timedelta(minutes=pol["meeting_duration_minutes"])
    count = count or pol["meeting_slots_to_propose"]
    conn, cal = calendar_for(db, organizer)
    if cal is None:
        return [], "No approved calendar for the organiser: connect one in Settings → Channels to suggest times"
    earliest = now + timedelta(hours=pol["meeting_min_notice_hours"])
    horizon = now + timedelta(days=pol["meeting_days_ahead"] + 1)
    try:
        busy = cal.busy(now, horizon + timedelta(days=7))
    except CalendarError as e:
        if conn and e.needs_reauth:
            conn.status, conn.last_error = "needs_reauth", str(e)[:500]
        return [], f"Couldn't read the calendar: {e}"

    def free(s: datetime) -> bool:
        e = s + dur
        loc = s.astimezone(z)
        if hours["weekdays_only"] and loc.weekday() >= 5:
            return False
        if not (
            hours["start_hour"] <= loc.hour
            and (
                e.astimezone(z).hour < hours["end_hour"]
                or (e.astimezone(z).hour == hours["end_hour"] and e.astimezone(z).minute == 0)
            )
        ):
            return False
        return s >= earliest and not any(b0 < e and s < b1 for b0, b1 in busy)

    out: list[dict[str, Any]] = []
    for p in preferred or []:
        if free(p) and len(out) < count:
            out.append({"start": p.isoformat(), "end": (p + dur).isoformat(), "reason": "the time they asked for"})
    day = earliest.astimezone(z).date()
    tries = 0
    while len(out) < count and tries < pol["meeting_days_ahead"] + 14:
        tries += 1
        for hh, mm in ((11, 0), (15, 0), (12, 0), (16, 0), (10, 30)):
            s = datetime.combine(day, time(hh, mm), z).astimezone(UTC)
            if free(s) and all(abs(datetime.fromisoformat(x["start"]) - s) > timedelta(hours=20) for x in out):
                out.append({"start": s.isoformat(), "end": (s + dur).isoformat(), "reason": "free in the calendar"})
                break
        day += timedelta(days=1)
    return out[:count], None if out else "No free time found in the next days; pick a time manually"


def fmt_slot(start: datetime, tz: str) -> str:
    loc = start.astimezone(ZoneInfo(tz))
    return loc.strftime("%a %d %b, ") + loc.strftime("%I:%M %p").lstrip("0").lower() + f" ({loc.tzname()})"


# ---------------------------------------------------------------------------
# proposing
# ---------------------------------------------------------------------------
def propose(
    db: Session,
    *,
    lead: Lead,
    contact: Contact,
    source: str,
    source_activity: Activity,
    request_text: str | None,
    intent: str,
    email_reply: EmailReply | None = None,
    wa_message: WaMessage | None = None,
    actor: User | None = None,
) -> Meeting:
    """Create a proposed meeting with suggested times, an agenda and a reply draft. Books nothing."""
    existing = db.scalars(
        select(Meeting).where(Meeting.contact_id == contact.id, Meeting.status.in_(ARRANGING)).order_by(Meeting.id.desc())
    ).first()
    if existing:
        return existing
    pol = load_policy(db)
    tz = pol["meeting_hours"]["timezone"]
    now = datetime.now(UTC)
    organizer = _organizer(db, lead, actor)
    seq = db.get(OutreachSequence, email_reply.sequence_id) if email_reply and email_reply.sequence_id else None
    use_case = seq.use_case if seq else None
    preferred = requested_times(request_text or "", now, tz)
    slots, note = free_slots(db, organizer, now, preferred=preferred)
    m = Meeting(
        lead_id=lead.id,
        institution_id=lead.institution_id,
        contact_id=contact.id,
        organizer_id=organizer.id,
        source=source,
        source_activity_id=source_activity.id,
        email_reply_id=email_reply.id if email_reply else None,
        wa_message_id=wa_message.id if wa_message else None,
        intent=intent,
        requested_text=(request_text or "")[:500] or None,
        title=f"PlugDrop.AI × {lead.institution.name}: {USE_CASES.get(use_case or '', 'admissions enquiries')}"[:300],
        agenda=agenda_for(db, lead, contact, use_case, intent, request_text),
        duration_minutes=pol["meeting_duration_minutes"],
        timezone=tz,
        proposed_slots=slots,
        location=pol["video_link_template"] or None,
        attendees=[
            {"name": organizer.full_name, "email": organizer.email, "kind": "internal"},
            {"name": contact.full_name, "email": contact.email, "kind": "external", "contact_id": contact.id},
        ],
        idempotency_key=f"mtg-{uuid.uuid4().hex}",
        is_demo=lead.is_demo,
    )
    db.add(m)
    db.flush()
    _draft_reply(db, m, contact, slots, email_reply, wa_message, actor)
    act = C.record_activity(
        db,
        "meeting",
        f"Meeting proposed to {contact.full_name}: {len(slots)} time(s) suggested" + (f" — {note}" if note else ""),
        lead_id=lead.id,
        institution_id=lead.institution_id,
        contact_id=contact.id,
        body="\n".join(fmt_slot(datetime.fromisoformat(s["start"]), tz) for s in slots) or note,
        details={"meeting_id": m.id, "direction": "internal", "source": source},
        is_demo=lead.is_demo,
    )
    F.on_meeting_proposed(db, m, act)
    event("meeting_proposed", meeting_id=m.id, lead_id=lead.id, slots=len(slots), source=source)
    return m


def _draft_reply(
    db: Session,
    m: Meeting,
    contact: Contact,
    slots: list[dict[str, Any]],
    reply: EmailReply | None,
    wa: WaMessage | None,
    actor: User | None,
) -> None:
    times = "\n".join(f"• {fmt_slot(datetime.fromisoformat(s['start']), m.timezone)}" for s in slots)
    if not times:
        times = "• [EDIT: suggest two times]"
    if reply is not None and reply.sequence_id:
        from app.outreach import service as S
        from app.outreach import writer as W

        seq = S.load_sequence(db, reply.sequence_id)
        user = actor or db.get(User, m.organizer_id)
        assert user is not None
        try:
            msg = S.reply_draft(db, reply, user)
        except HTTPException:
            return
        ctx = S._context(db, seq, msg.step, "reply", user, reply)
        text = (
            f"Dear {contact.greeting_name},\n\nThank you — I'd be glad to set up a short call. Would one of these times suit you?\n\n"
            f"{times}\n\nI'd suggest we look at how enquiries reach your admissions team today and whether PlugDrop could help. "
            "If none of these work, which day would be better?"
        )
        msg.body = W.finalize(ctx, text)
        msg.generated_by = "meeting-assistant"
        msg.generation = {**(msg.generation or {}), "rationale": "Meeting assistant: times from the organiser's calendar."}
        S.recheck(db, msg)
        m.draft_email_id = msg.id
    elif wa is not None:
        from app.models import WaMessage as WM

        text = (
            f"Thank you, {contact.greeting_name}. Happy to set up a short call. Would one of these suit you?\n{times}\n"
            "If not, which day works better?"
        )
        d = WM(
            conversation_id=wa.conversation_id, direction="out", kind="text", body=text, purpose="meeting",
            created_by_id=actor.id if actor else None, idempotency_key=uuid.uuid4().hex,
        )  # fmt: skip
        db.add(d)
        db.flush()
        m.draft_wa_id = d.id


# ---------------------------------------------------------------------------
# choosing a time, approval, booking
# ---------------------------------------------------------------------------
def choose_time(db: Session, m: Meeting, user: User, start: datetime) -> Meeting:
    if m.status not in ("proposed", "awaiting_approval"):
        raise HTTPException(409, f"Meeting is {m.status}")
    if start.tzinfo is None:
        start = start.replace(tzinfo=ZoneInfo(m.timezone))
    if start < datetime.now(UTC):
        raise HTTPException(422, "That time is in the past")
    m.start_at, m.end_at = start.astimezone(UTC), start.astimezone(UTC) + timedelta(minutes=m.duration_minutes)
    m.status = "awaiting_approval"
    act = C.record_activity(
        db, "meeting", f"Meeting time chosen: {fmt_slot(m.start_at, m.timezone)} — waiting for approval",
        lead_id=m.lead_id, institution_id=m.institution_id, contact_id=m.contact_id, user_id=user.id,
        details={"meeting_id": m.id, "direction": "internal"}, is_demo=m.is_demo,
    )  # fmt: skip
    F.ensure_task(
        db,
        auto_key=f"approve:{m.id}:{m.start_at.isoformat()}",
        title=f"Approve the meeting with {m.contact.full_name if m.contact else m.institution.name}",
        reason="meeting_approval",
        due_at=datetime.now(UTC) + timedelta(hours=2),
        lead=m.lead,
        source_activity=act,
        contact_id=m.contact_id,
        meeting_id=m.id,
        priority="urgent",
        actor_id=user.id,
    )
    audit.record(db, user, "choose_time", "meeting", m.id, {"start": m.start_at.isoformat()})
    db.commit()
    return m


def approve(db: Session, m: Meeting, user: User) -> Meeting:
    """Create the calendar event. Only after approval, only once (idempotency key), only if the time is still free."""
    require(db, user, "meeting_create")
    if m.status != "awaiting_approval" or m.start_at is None or m.end_at is None:
        raise HTTPException(409, "Choose a time first")
    contact = m.contact
    if contact and contact.id in C.suppressed_contact_ids(db, [contact]):
        raise HTTPException(409, "This person has opted out; the meeting can't be booked")
    organizer = db.get(User, m.organizer_id) if m.organizer_id else user
    assert organizer is not None
    conn, cal = calendar_for(db, organizer)
    if cal is None:
        raise HTTPException(409, f"{organizer.full_name} has no approved calendar connection")
    try:
        if any(
            b0 < m.end_at and m.start_at < b1
            for b0, b1 in cal.busy(m.start_at - timedelta(hours=1), m.end_at + timedelta(hours=1))
        ):
            raise HTTPException(409, "That time is no longer free in the organiser's calendar; choose another")
        created = cal.create_event(
            NewEvent(
                title=m.title,
                description=m.agenda.split("\n\nPrep (internal")[0],  # internal prep notes never go into the invitation
                start=m.start_at,
                end=m.end_at,
                timezone=m.timezone,
                attendees=tuple(a["email"] for a in m.attendees if a.get("email") and a["email"] != organizer.email),
                idempotency_key=m.idempotency_key,
                location=m.location,
            )
        )
    except CalendarError as e:
        if e.needs_reauth and conn:
            conn.status, conn.last_error = "needs_reauth", str(e)[:500]
            db.commit()
        raise HTTPException(502 if e.transient else 409, f"Calendar: {e}") from e
    m.calendar_connection_id = conn.id if conn else None
    m.calendar_event_id, m.location = created.event_id, created.join_url or m.location
    m.status, m.approved_by_id, m.approved_at = "scheduled", user.id, datetime.now(UTC)
    lead = m.lead
    if lead.stage in ("new", "researched", "qualified", "outreach_ready", "contacted", "engaged"):
        old = lead.stage
        lead.stage, lead.stage_changed_at = "meeting_booked", datetime.now(UTC)
        C.record_activity(
            db, "status_change", f"Stage changed: {old.replace('_', ' ')} → meeting booked", lead_id=lead.id,
            institution_id=lead.institution_id, contact_id=m.contact_id, user_id=user.id,
            details={"from": old, "to": "meeting_booked"}, is_demo=lead.is_demo,
        )  # fmt: skip
    if contact:
        from app.outreach.service import stop_all_for_contact

        stop_all_for_contact(db, contact.id, "replied", "Meeting booked")
    act = C.record_activity(
        db, "meeting", f"Meeting booked: {fmt_slot(m.start_at, m.timezone)} with {contact.full_name if contact else ''}",
        lead_id=m.lead_id, institution_id=m.institution_id, contact_id=m.contact_id, user_id=user.id,
        body=m.agenda.split("\n\nPrep (internal")[0], details={"meeting_id": m.id, "direction": "out"}, is_demo=m.is_demo,
    )  # fmt: skip
    F.on_meeting_scheduled(db, m, act)
    audit.record(db, user, "approve", "meeting", m.id, {"event": m.calendar_event_id})
    event("meeting_booked", meeting_id=m.id, provider=conn.provider if conn else None)
    db.commit()
    return m


def confirm(db: Session, m: Meeting, user: User) -> Meeting:
    if m.status != "scheduled":
        raise HTTPException(409, f"Meeting is {m.status}")
    m.status, m.confirmed_at = "confirmed", datetime.now(UTC)
    F.complete_tasks(db, reasons=("meeting_confirmation",), note="confirmed", meeting_id=m.id)
    C.record_activity(
        db, "meeting", "Meeting confirmed by the prospect", lead_id=m.lead_id, institution_id=m.institution_id,
        contact_id=m.contact_id, user_id=user.id, details={"meeting_id": m.id, "direction": "in"}, is_demo=m.is_demo,
    )  # fmt: skip
    db.commit()
    return m


def record_outcome(
    db: Session,
    m: Meeting,
    user: User,
    *,
    outcome: str,
    notes: str | None,
    next_steps: str | None,
    attendees: list[dict[str, Any]] | None,
) -> Meeting:
    if m.status not in ("scheduled", "confirmed", "completed", "no_show"):
        raise HTTPException(409, "Only booked meetings can have an outcome")
    if outcome not in ("positive", "neutral", "negative", "no_show"):
        raise HTTPException(422, "Outcome must be positive, neutral, negative or no_show")
    m.outcome, m.notes, m.next_steps = outcome, notes, next_steps
    if attendees is not None:
        m.attendees = attendees
    m.status = "no_show" if outcome == "no_show" else "completed"
    act = C.record_activity(
        db, "meeting", f"Meeting {'missed (no-show)' if outcome == 'no_show' else 'held'} — outcome: {outcome}",
        lead_id=m.lead_id, institution_id=m.institution_id, contact_id=m.contact_id, user_id=user.id,
        body="\n\n".join(x for x in (f"Attendees: {', '.join(a.get('name') or a.get('email', '') for a in m.attendees)}", notes and f"Notes: {notes}", next_steps and f"Next steps: {next_steps}") if x),  # noqa: E501
        details={"meeting_id": m.id, "outcome": outcome, "direction": "internal"}, is_demo=m.is_demo,
    )  # fmt: skip
    F.on_meeting_outcome(db, m, act)
    audit.record(db, user, "outcome", "meeting", m.id, {"outcome": outcome})
    db.commit()
    return m


def cancel(db: Session, m: Meeting, user: User, reason: str) -> Meeting:
    if m.status in ("completed", "cancelled", "no_show"):
        raise HTTPException(409, f"Meeting is {m.status}")
    if m.calendar_event_id and m.calendar_connection_id:
        conn = db.get(CalendarConnection, m.calendar_connection_id)
        if conn:
            try:
                provider_for(db, conn).cancel_event(m.calendar_event_id)
            except CalendarError as e:
                raise HTTPException(502, f"Couldn't cancel in the calendar: {e}") from e
    m.status = "cancelled"
    F.complete_tasks(
        db,
        reasons=("interested_no_meeting", "meeting_approval", "meeting_confirmation"),
        note=f"meeting cancelled: {reason}",
        meeting_id=m.id,
    )
    C.record_activity(
        db, "meeting", f"Meeting cancelled: {reason}", lead_id=m.lead_id, institution_id=m.institution_id,
        contact_id=m.contact_id, user_id=user.id, details={"meeting_id": m.id, "direction": "internal"}, is_demo=m.is_demo,
    )  # fmt: skip
    audit.record(db, user, "cancel", "meeting", m.id, {"reason": reason})
    db.commit()
    return m


def ensure_mock_calendar(db: Session, user: User) -> CalendarConnection | None:
    """Demo/dev: give a user an approved mock calendar so the assistant can suggest times."""
    if not get_settings().calendar_mock_enabled:
        return None
    conn = db.scalar(
        select(CalendarConnection).where(CalendarConnection.user_id == user.id, CalendarConnection.provider == "mock")
    )
    if conn is None:
        conn = CalendarConnection(
            user_id=user.id,
            provider="mock",
            email_address=user.email,
            status="active",
            scopes=["mock"],
            approved_at=datetime.now(UTC),
        )
        db.add(conn)
        db.flush()
    return conn


__all__ = [
    "MockCalendar",
    "propose",
    "approve",
    "choose_time",
    "confirm",
    "record_outcome",
    "cancel",
    "free_slots",
    "requested_times",
]
