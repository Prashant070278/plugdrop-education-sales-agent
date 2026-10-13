"""Admin-controlled engagement policy and permissions (Stage 4)."""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app import audit
from app.deps import RANK
from app.models import AppSetting, User

POLICY_KEY = "engagement_policy"
PERMISSIONS_KEY = "permissions"

DEFAULT_POLICY: dict[str, Any] = {
    # WhatsApp: off until an admin turns it on (and, for the real provider, the server switch allows it).
    "whatsapp_enabled": False,
    "whatsapp_daily_limit": 100,  # outbound WhatsApp messages per day, all users
    "whatsapp_per_minute": 20,  # rate limit towards the provider
    "whatsapp_max_attempts": 3,  # retries for temporary provider errors
    # Channel coordination
    "min_hours_between_touches": 20,  # no two outbound messages to one person within this many hours, across channels
    "block_while_awaiting_our_reply": True,  # never send new outreach while the person's last message is unanswered
    # Meetings
    "meeting_duration_minutes": 30,
    "meeting_days_ahead": 7,
    "meeting_slots_to_propose": 3,
    "meeting_hours": {"start_hour": 10, "end_hour": 17, "timezone": "Asia/Kolkata", "weekdays_only": True},
    "meeting_min_notice_hours": 18,
    "video_link_template": "",  # e.g. a fixed Google Meet / Teams room; empty = the calendar provider adds one
    # Follow-up tasks (Module C)
    "interested_no_meeting_days": 2,
    "confirm_meeting_hours_before": 24,
    "post_meeting_follow_up_days": 1,
    "proposal_follow_up_days": 5,
    "reconnect_default_days": 90,
    "default_task_owner_id": None,  # used when a lead has no owner; falls back to the first active admin
}

# Minimum role for each action. Admins can raise or lower these (never below 'rep').
ACTIONS: dict[str, tuple[str, str]] = {
    "email_approve": ("rep", "Approve outreach emails"),
    "email_send": ("rep", "Send / place outreach emails now"),
    "whatsapp_send": ("rep", "Approve and send WhatsApp messages"),
    "whatsapp_templates": ("manager", "Create WhatsApp templates and submit them for approval"),
    "whatsapp_optin": ("rep", "Record a contact's WhatsApp opt-in"),
    "conversation_assign": ("rep", "Take over or hand off WhatsApp conversations"),
    "meeting_create": ("rep", "Approve a meeting and create the calendar event"),
    "linkedin_approve": ("rep", "Approve LinkedIn drafts"),
    "calendar_approve": ("admin", "Approve calendar connections"),
}
ROLES = ("rep", "manager", "admin")


def load_policy(db: Session) -> dict[str, Any]:
    row = db.get(AppSetting, POLICY_KEY)
    p = {**DEFAULT_POLICY, **(row.value if row else {})}
    p["meeting_hours"] = {**DEFAULT_POLICY["meeting_hours"], **p.get("meeting_hours", {})}
    return p


def save_policy(db: Session, value: dict[str, Any], user: User) -> dict[str, Any]:
    p = {**load_policy(db), **{k: v for k, v in value.items() if k in DEFAULT_POLICY}}
    for k, lo, hi in (
        ("whatsapp_daily_limit", 1, 5000),
        ("whatsapp_per_minute", 1, 600),
        ("whatsapp_max_attempts", 1, 6),
        ("min_hours_between_touches", 0, 168),
        ("meeting_duration_minutes", 15, 120),
        ("meeting_days_ahead", 1, 30),
        ("meeting_slots_to_propose", 1, 6),
        ("meeting_min_notice_hours", 0, 72),
        ("interested_no_meeting_days", 0, 30),
        ("confirm_meeting_hours_before", 1, 168),
        ("post_meeting_follow_up_days", 0, 30),
        ("proposal_follow_up_days", 1, 60),
        ("reconnect_default_days", 7, 365),
    ):
        if not isinstance(p[k], int) or isinstance(p[k], bool) or not lo <= p[k] <= hi:
            raise HTTPException(422, f"{k} must be a whole number between {lo} and {hi}")
    for k in ("whatsapp_enabled", "block_while_awaiting_our_reply"):
        if not isinstance(p[k], bool):
            raise HTTPException(422, f"{k} must be true or false")
    h = p["meeting_hours"]
    if not (0 <= h["start_hour"] < h["end_hour"] <= 24):
        raise HTTPException(422, "meeting_hours must satisfy 0 ≤ start < end ≤ 24")
    try:
        ZoneInfo(h["timezone"])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"Unknown timezone {h['timezone']}") from e
    _store(db, POLICY_KEY, p, user, "engagement_policy")
    return p


def load_permissions(db: Session) -> dict[str, str]:
    row = db.get(AppSetting, PERMISSIONS_KEY)
    stored = row.value if row else {}
    return {k: stored.get(k, default) for k, (default, _) in ACTIONS.items()}


def save_permissions(db: Session, value: dict[str, str], user: User) -> dict[str, str]:
    p = load_permissions(db)
    for k, v in value.items():
        if k not in ACTIONS:
            raise HTTPException(422, f"Unknown action: {k}")
        if v not in ROLES:
            raise HTTPException(422, f"{k} must be one of {', '.join(ROLES)}")
        p[k] = v
    _store(db, PERMISSIONS_KEY, p, user, "permissions")
    return p


def require(db: Session, user: User, action: str) -> None:
    """Raise 403 unless the user's role meets the admin-configured minimum for this action."""
    need = load_permissions(db)[action]
    if RANK.get(user.role, 0) < RANK[need]:
        raise HTTPException(403, f"Your role can't do this: '{ACTIONS[action][1]}' needs {need} or higher (set by an admin)")


def _store(db: Session, key: str, value: dict[str, Any], user: User, entity: str) -> None:
    row = db.get(AppSetting, key)
    before = row.value if row else {}
    if row is None:
        db.add(AppSetting(key=key, value=value, updated_by_id=user.id))
    else:
        row.value, row.updated_by_id = value, user.id
    audit.record(db, user, "update", entity, None, {"from": before, "to": value})
    db.commit()
