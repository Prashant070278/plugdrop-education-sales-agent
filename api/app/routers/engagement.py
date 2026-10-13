from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.deps import DB, AdminUser, CurrentUser, ManagerUser
from app.engagement import coordination as C
from app.engagement import followups as F
from app.engagement import policy as P
from app.engagement.timeline import timeline
from app.models import Contact, Meeting, Task, WaConversation
from app.services import get_or_404

router = APIRouter(prefix="/engagement", tags=["engagement"])


@router.get("/timeline")
def get_timeline(
    _: CurrentUser,
    db: DB,
    lead_id: int | None = None,
    contact_id: int | None = None,
    channel: str | None = Query(None, pattern="^(email|whatsapp|meeting|task|linkedin|research|call|pipeline|note)$"),
    limit: int = Query(300, ge=1, le=1000),
) -> list[dict[str, Any]]:
    """One timeline across every channel for a lead (and its institution) or a contact."""
    return timeline(db, lead_id=lead_id, contact_id=contact_id, channel=channel, limit=limit)


@router.get("/contacts/{contact_id}/state")
def contact_state(contact_id: int, _: CurrentUser, db: DB) -> dict[str, Any]:
    """The shared contact record as the coordination rules see it, plus what each channel would allow right now."""
    c = get_or_404(db, Contact, contact_id, "Contact")
    st = C.contact_state(db, c)
    st["checks"] = {
        "email_follow_up": C.check_outreach(db, c, "email", "follow_up").as_dict(),
        "whatsapp_first_contact": C.check_outreach(db, c, "whatsapp", "cold").as_dict(),
        "reply": C.check_outreach(db, c, "email", "reply").as_dict(),
    }
    return st


@router.get("/overview")
def overview(_: CurrentUser, db: DB) -> dict[str, Any]:
    by_reason: dict[str, int] = {
        str(r): int(n)
        for r, n in db.execute(
            select(Task.reason, func.count()).where(Task.status == "open", Task.reason.is_not(None)).group_by(Task.reason)
        )
    }
    return {
        "open_tasks_by_reason": by_reason,
        "conversations_handoff": db.scalar(
            select(func.count()).select_from(WaConversation).where(WaConversation.state == "handoff")
        )
        or 0,
        "conversations_unread": db.scalar(select(func.coalesce(func.sum(WaConversation.unread), 0))) or 0,
        "meetings": {str(s): int(n) for s, n in db.execute(select(Meeting.status, func.count()).group_by(Meeting.status))},
    }


@router.get("/policy")
def get_policy(_: CurrentUser, db: DB) -> dict[str, Any]:
    return P.load_policy(db)


@router.put("/policy")
def put_policy(body: dict[str, Any], user: AdminUser, db: DB) -> dict[str, Any]:
    return P.save_policy(db, body, user)


@router.get("/permissions")
def get_permissions(_: CurrentUser, db: DB) -> list[dict[str, str]]:
    cur = P.load_permissions(db)
    return [{"action": k, "label": label, "default": default, "min_role": cur[k]} for k, (default, label) in P.ACTIONS.items()]


@router.put("/permissions")
def put_permissions(body: dict[str, str], user: AdminUser, db: DB) -> list[dict[str, str]]:
    P.save_permissions(db, body, user)
    return get_permissions(user, db)


@router.post("/followups/sweep")
def sweep(_: ManagerUser, db: DB) -> dict[str, int]:
    """Run the time-based follow-up rules now (the background worker also runs them)."""
    return F.sweep(db)
