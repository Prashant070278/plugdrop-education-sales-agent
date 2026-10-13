"""One interaction timeline per lead or contact, across email, WhatsApp, meetings, tasks, LinkedIn and research."""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import Activity, Lead

CHANNEL_OF_TYPE = {
    "email": "email",
    "reply": "email",
    "whatsapp": "whatsapp",
    "meeting": "meeting",
    "task": "task",
    "linkedin": "linkedin",
    "research": "research",
    "call": "call",
    "status_change": "pipeline",
    "note": "note",
}


def channel_of(a: Activity) -> str:
    d = a.details or {}
    if a.activity_type == "note" and d.get("outreach_sequence_id"):
        return "email"
    if a.activity_type == "note" and d.get("conversation_id"):
        return "whatsapp"
    if a.activity_type == "note" and d.get("meeting_id"):
        return "meeting"
    return CHANNEL_OF_TYPE.get(a.activity_type, "note")


def direction_of(a: Activity) -> str:
    d = a.details or {}
    if a.activity_type == "reply" or d.get("direction") == "in":
        return "in"
    if a.activity_type in ("email",) or d.get("direction") == "out":
        return "out"
    return "internal"


def timeline(
    db: Session, *, lead_id: int | None = None, contact_id: int | None = None, channel: str | None = None, limit: int = 300
) -> list[dict[str, Any]]:
    conds = []
    if lead_id:
        lead = db.get(Lead, lead_id)
        if lead is None:
            return []
        conds.append(
            or_(Activity.lead_id == lead.id, (Activity.institution_id == lead.institution_id) & Activity.lead_id.is_(None))
        )
    if contact_id:
        conds.append(Activity.contact_id == contact_id)
    rows = db.scalars(
        select(Activity)
        .options(selectinload(Activity.user), selectinload(Activity.contact))
        .where(*conds)
        .order_by(Activity.occurred_at.desc(), Activity.id.desc())
        .limit(limit * 2 if channel else limit)
    ).all()
    out = []
    for a in rows:
        ch = channel_of(a)
        if channel and ch != channel:
            continue
        d = a.details or {}
        out.append(
            {
                "id": a.id,
                "at": a.occurred_at,
                "channel": ch,
                "direction": direction_of(a),
                "type": a.activity_type,
                "subject": a.subject,
                "body": a.body,
                "actor": a.user.full_name if a.user else None,
                "contact": {"id": a.contact.id, "name": a.contact.full_name} if a.contact else None,
                "links": {
                    k: d[k]
                    for k in (
                        "outreach_sequence_id",
                        "message_id",
                        "conversation_id",
                        "wa_message_id",
                        "meeting_id",
                        "task_id",
                        "linkedin_item_id",
                        "research_run_id",
                    )
                    if d.get(k)
                },
                "is_demo": a.is_demo,
            }
        )
    return out[:limit]
