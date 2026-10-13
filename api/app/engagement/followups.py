"""Sales follow-up tasks (Module C).

Every task created here has an owner, a due date, a reason and the activity that caused it. Each rule uses a stable
``auto_key`` so running the rules again (the worker sweeps every few minutes) never creates a duplicate.

Rules
- reply_needed            a person replied (email or WhatsApp) and is waiting for an answer
- interested_no_meeting   interested / meeting request, no meeting booked yet
- meeting_approval        a meeting time was chosen and needs approval before the calendar event is created
- meeting_confirmation    a booked meeting the prospect hasn't confirmed
- post_meeting            a meeting ended: record the outcome and send the follow-up
- proposal_follow_up      an opportunity sat in 'proposal' with no activity for N days
- reconnect_later         they asked to talk later ("not now", "after admissions", "in December")
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.engagement.coordination import record_activity
from app.engagement.logs import event
from app.engagement.policy import load_policy
from app.models import Activity, Lead, Meeting, Opportunity, Task, User

MONTHS = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]


def task_owner(db: Session, lead: Lead | None, fallback_user_id: int | None = None) -> int:
    """Lead owner → policy default owner → the person who acted → first active admin. Never empty."""
    if lead and lead.owner_id:
        return lead.owner_id
    pol = load_policy(db)
    for uid in (pol.get("default_task_owner_id"), fallback_user_id):
        if uid and (u := db.get(User, uid)) and u.is_active:
            return u.id
    admin = db.scalars(select(User).where(User.role == "admin", User.is_active.is_(True)).order_by(User.id)).first()
    if admin is None:
        raise RuntimeError("No active admin to own follow-up tasks")
    return admin.id


def ensure_task(
    db: Session,
    *,
    auto_key: str,
    title: str,
    reason: str,
    due_at: datetime,
    lead: Lead | None,
    source_activity: Activity,
    contact_id: int | None = None,
    meeting_id: int | None = None,
    opportunity_id: int | None = None,
    priority: str = "high",
    description: str | None = None,
    actor_id: int | None = None,
) -> Task:
    existing = db.scalar(select(Task).where(Task.auto_key == auto_key))
    if existing:
        return existing
    owner = task_owner(db, lead, actor_id)
    t = Task(
        title=title[:300],
        description=description,
        due_at=due_at,
        priority=priority,
        owner_id=owner,
        lead_id=lead.id if lead else None,
        institution_id=lead.institution_id if lead else source_activity.institution_id,
        contact_id=contact_id,
        opportunity_id=opportunity_id,
        meeting_id=meeting_id,
        reason=reason,
        source_activity_id=source_activity.id,
        auto_key=auto_key,
        is_demo=source_activity.is_demo,
    )
    db.add(t)
    db.flush()
    record_activity(
        db,
        "task",
        f"Task created: {t.title}",
        lead_id=t.lead_id,
        institution_id=t.institution_id,
        contact_id=contact_id,
        details={"task_id": t.id, "reason": reason, "source_activity_id": source_activity.id, "due_at": due_at.isoformat()},
        is_demo=t.is_demo,
    )
    event("task_created", task_id=t.id, reason=reason, owner_id=owner, auto_key=auto_key)
    return t


def complete_tasks(
    db: Session, *, reasons: tuple[str, ...], note: str, contact_id: int | None = None, meeting_id: int | None = None
) -> int:
    """Close open tasks that no longer apply (e.g. 'book a meeting' once the meeting is booked)."""
    q = select(Task).where(Task.status == "open", Task.reason.in_(reasons))
    if contact_id:
        q = q.where(Task.contact_id == contact_id)
    if meeting_id:
        q = q.where(Task.meeting_id == meeting_id)
    n = 0
    for t in db.scalars(q):
        t.status, t.completed_at = "done", datetime.now(UTC)
        t.description = ((t.description + "\n") if t.description else "") + f"Closed automatically: {note}"
        n += 1
    return n


def reconnect_date(text: str, received: datetime, default_days: int) -> tuple[datetime, str]:
    """When they asked to be contacted again, as best the words say; otherwise the policy default."""
    low = text.lower()
    for i, name in enumerate(MONTHS):
        if re.search(rf"\b(?:in|after|from|by|till|until)\s+{name[:3]}\w*", low):
            year = received.year + (1 if i + 1 <= received.month else 0)
            when = datetime(year, i + 1, 1, 10, 0, tzinfo=UTC) + timedelta(days=(14 if "after" in low else 0))
            return when, f"they mentioned {name.title()}"
    rules = [
        (r"next week", 7),
        (r"next month|in a month|few weeks", 30),
        (r"next quarter|three months|3 months", 90),
        (r"after (?:the |our )?(?:admissions?|intake|session|counselling)", 75),
        (r"next (?:year|session|academic year|admission cycle)", 180),
        (r"six months|6 months", 180),
    ]
    for pat, days in rules:
        if re.search(pat, low):
            return received + timedelta(days=days), f"they said '{re.search(pat, low).group(0)}'"  # type: ignore[union-attr]
    return received + timedelta(days=default_days), f"no date given; policy default of {default_days} days"


# ---------------------------------------------------------------------------
# event-driven rules (called when something happens)
# ---------------------------------------------------------------------------
def on_reply(
    db: Session,
    *,
    lead: Lead,
    contact_id: int,
    contact_name: str,
    label: str,
    text: str,
    received_at: datetime,
    source: Activity,
    key: str,
    channel: str,
) -> list[Task]:
    """A human reply arrived (email or WhatsApp). Returns the tasks created."""
    pol = load_policy(db)
    out: list[Task] = []
    if label in ("out_of_office",):
        return out
    hours = 4 if label in ("meeting_request", "interested") else 24
    out.append(
        ensure_task(
            db,
            auto_key=f"reply:{key}",
            title=f"Respond to {contact_name} ({label.replace('_', ' ')}, {channel})",
            reason="reply_needed",
            due_at=received_at + timedelta(hours=hours),
            lead=lead,
            source_activity=source,
            contact_id=contact_id,
            priority="urgent" if label == "meeting_request" else "high",
            description=f"They replied on {channel}. Review the reply and the suggested response.",
        )
    )
    if label == "not_now":
        when, why = reconnect_date(text, received_at, pol["reconnect_default_days"])
        out.append(
            ensure_task(
                db,
                auto_key=f"reconnect:{key}",
                title=f"Reconnect with {contact_name}",
                reason="reconnect_later",
                due_at=when,
                lead=lead,
                source_activity=source,
                contact_id=contact_id,
                priority="medium",
                description=f"They asked to talk later ({why}). Check the latest research before getting back in touch.",
            )
        )
    return out


def on_meeting_proposed(db: Session, meeting: Meeting, source: Activity) -> Task:
    pol = load_policy(db)
    return ensure_task(
        db,
        auto_key=f"interested:{meeting.id}",
        title=f"Book the meeting with {meeting.contact.full_name if meeting.contact else meeting.institution.name}",
        reason="interested_no_meeting",
        due_at=datetime.now(UTC) + timedelta(days=max(pol["interested_no_meeting_days"], 0), hours=4),
        lead=meeting.lead,
        source_activity=source,
        contact_id=meeting.contact_id,
        meeting_id=meeting.id,
        priority="urgent",
        description="They're interested. Approve and send the suggested times, then approve the meeting once a time is agreed.",
    )


def on_meeting_scheduled(db: Session, meeting: Meeting, source: Activity) -> Task | None:
    pol = load_policy(db)
    complete_tasks(db, reasons=("interested_no_meeting", "meeting_approval"), note="meeting booked", meeting_id=meeting.id)
    if meeting.start_at is None:
        return None
    due = max(datetime.now(UTC), meeting.start_at - timedelta(hours=pol["confirm_meeting_hours_before"]))
    return ensure_task(
        db,
        auto_key=f"confirm:{meeting.id}:{meeting.start_at.isoformat()}",
        title=f"Confirm the meeting with {meeting.contact.full_name if meeting.contact else meeting.institution.name}",
        reason="meeting_confirmation",
        due_at=due,
        lead=meeting.lead,
        source_activity=source,
        contact_id=meeting.contact_id,
        meeting_id=meeting.id,
        description="Check they've accepted the invitation; mark the meeting confirmed when they have.",
    )


def on_meeting_outcome(db: Session, meeting: Meeting, source: Activity) -> Task | None:
    pol = load_policy(db)
    complete_tasks(db, reasons=("meeting_confirmation", "post_meeting"), note="outcome recorded", meeting_id=meeting.id)
    if meeting.outcome in ("negative",) and not (meeting.next_steps or "").strip():
        return None
    title = (meeting.next_steps or "").strip().split("\n")[0][:200] or "Send the follow-up after the meeting"
    return ensure_task(
        db,
        auto_key=f"after:{meeting.id}",
        title=f"{title} — {meeting.institution.name}",
        reason="post_meeting",
        due_at=datetime.now(UTC) + timedelta(days=max(pol["post_meeting_follow_up_days"], 0), hours=2),
        lead=meeting.lead,
        source_activity=source,
        contact_id=meeting.contact_id,
        meeting_id=meeting.id,
        description=f"Outcome: {meeting.outcome or 'not recorded'}. Next steps agreed: {meeting.next_steps or '—'}",
    )


# ---------------------------------------------------------------------------
# time-driven rules (the worker sweeps these)
# ---------------------------------------------------------------------------
def sweep(db: Session, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(UTC)
    pol = load_policy(db)
    made = {"post_meeting": 0, "proposal_follow_up": 0}
    # Meetings that have ended without an outcome
    for m in db.scalars(
        select(Meeting).where(Meeting.status.in_(("scheduled", "confirmed")), Meeting.end_at.is_not(None), Meeting.end_at < now)
    ):
        src = db.get(Activity, m.source_activity_id) if m.source_activity_id else None
        src = src or record_activity(
            db, "meeting", f"Meeting time passed: {m.title}", lead_id=m.lead_id, institution_id=m.institution_id,
            contact_id=m.contact_id, details={"meeting_id": m.id}, is_demo=m.is_demo,
        )  # fmt: skip
        before = db.scalar(select(func.count()).select_from(Task).where(Task.auto_key == f"outcome:{m.id}"))
        ensure_task(
            db,
            auto_key=f"outcome:{m.id}",
            title=f"Record the outcome of the meeting with {m.institution.name} and send the follow-up",
            reason="post_meeting",
            due_at=(m.end_at or now) + timedelta(days=max(pol["post_meeting_follow_up_days"], 0)),
            lead=m.lead,
            source_activity=src,
            contact_id=m.contact_id,
            meeting_id=m.id,
        )
        made["post_meeting"] += 0 if before else 1
    # Proposals waiting too long
    cutoff = now - timedelta(days=pol["proposal_follow_up_days"])
    for opp in db.scalars(select(Opportunity).where(Opportunity.stage == "proposal")):
        last = db.scalars(
            select(Activity)
            .where(Activity.lead_id == opp.lead_id, Activity.activity_type != "task")
            .order_by(Activity.occurred_at.desc())
        ).first()
        if last is None or last.occurred_at > cutoff:
            continue
        key = f"proposal:{opp.id}:{last.id}"
        if db.scalar(select(Task.id).where(Task.auto_key == key)):
            continue
        ensure_task(
            db,
            auto_key=key,
            title=f"Follow up on the proposal: {opp.name}",
            reason="proposal_follow_up",
            due_at=now + timedelta(hours=4),
            lead=db.get(Lead, opp.lead_id),
            source_activity=last,
            opportunity_id=opp.id,
            description=f"No activity for {pol['proposal_follow_up_days']}+ days since: {last.subject}",
        )
        made["proposal_follow_up"] += 1
    db.commit()
    return made


def describe(t: Task) -> dict[str, Any]:
    return {"id": t.id, "title": t.title, "reason": t.reason, "due_at": t.due_at, "owner_id": t.owner_id}
