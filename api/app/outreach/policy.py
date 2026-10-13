"""Messaging policy: admin-configurable rules for every outreach email."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app import audit
from app.models import AppSetting, User

POLICY_KEY = "outreach_policy"

DEFAULT_POLICY: dict[str, Any] = {
    # "draft_only": approved emails are placed in the mailbox as drafts for a person to send.
    # "send": the system sends approved, scheduled emails itself (admin only).
    "sending_mode": "draft_only",
    "max_attempts": 3,  # first email + follow-ups
    "spacing_days": [0, 4, 7],  # days after the previous email for each step
    "global_daily_limit": 150,
    "per_domain_daily_limit": 3,  # protects one institution from receiving many emails in a day
    "send_window": {"start_hour": 9, "end_hour": 17, "timezone": "Asia/Kolkata", "weekdays_only": True},
    "max_words_first": 130,
    "max_words_follow_up": 90,
    "ooo_postpone_days": 7,
    "sender_company": "PlugDrop.AI",
    "signature": "{sender_name}\nPlugDrop.AI — Voice AI + CRM for education",
    "opt_out_line": "If you'd rather not hear from me, reply \"unsubscribe\" and I won't email you again.",
    "banned_phrases": [
        "revolutionize",
        "revolutionise",
        "game-changer",
        "game changer",
        "cutting-edge",
        "cutting edge",
        "disrupt",
        "10x",
        "guarantee",
        "guaranteed",
        "skyrocket",
        "best-in-class",
        "world-class",
        "as we discussed",
        "as promised",
        "following up on our conversation",
        "great speaking",
        "great to meet",
        "per our call",
        "you requested",
        "we spoke",
        "our partnership",
    ],
    # Product, vendor and integration names that may only appear when an approved claim or a verified
    # fact mentions them (prevents invented integrations).
    "restricted_terms": [
        "Salesforce",
        "HubSpot",
        "Zoho",
        "LeadSquared",
        "Dynamics",
        "Slate",
        "Ellucian",
        "Banner",
        "Freshworks",
        "Twilio",
        "Exotel",
        "Genesys",
        "Five9",
        "ROI",
    ],
}


def load_policy(db: Session) -> dict[str, Any]:
    row = db.get(AppSetting, POLICY_KEY)
    p = {**DEFAULT_POLICY, **(row.value if row else {})}
    p["send_window"] = {**DEFAULT_POLICY["send_window"], **p.get("send_window", {})}
    return p


def save_policy(db: Session, value: dict[str, Any], user: User) -> dict[str, Any]:
    p = {**load_policy(db), **value}
    if p["sending_mode"] not in ("draft_only", "send"):
        raise HTTPException(422, "sending_mode must be draft_only or send")
    if not isinstance(p["max_attempts"], int) or not 1 <= p["max_attempts"] <= 6:
        raise HTTPException(422, "max_attempts must be between 1 and 6")
    sd = p["spacing_days"]
    if not isinstance(sd, list) or len(sd) < p["max_attempts"] or any(not isinstance(x, int) or x < 0 or x > 60 for x in sd):
        raise HTTPException(422, "spacing_days needs one whole number (0-60) per attempt")
    if any(x < 2 for x in sd[1 : p["max_attempts"]]):
        raise HTTPException(422, "Follow-ups must be at least 2 days apart")
    for k, lo, hi in (
        ("global_daily_limit", 1, 2000),
        ("per_domain_daily_limit", 1, 50),
        ("max_words_first", 40, 250),
        ("max_words_follow_up", 30, 200),
        ("ooo_postpone_days", 1, 30),
    ):
        if not isinstance(p[k], int) or not lo <= p[k] <= hi:
            raise HTTPException(422, f"{k} must be between {lo} and {hi}")
    w = p["send_window"]
    if not (0 <= w["start_hour"] < w["end_hour"] <= 24):
        raise HTTPException(422, "send_window hours must satisfy 0 ≤ start < end ≤ 24")
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(w["timezone"])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"Unknown timezone {w['timezone']}") from e
    if "{sender_name}" not in p["signature"] and not p["signature"].strip():
        raise HTTPException(422, "signature cannot be empty")
    if not p["opt_out_line"].strip():
        raise HTTPException(422, "An opt-out line is required")
    row = db.get(AppSetting, POLICY_KEY)
    before = row.value if row else {}
    if row is None:
        db.add(AppSetting(key=POLICY_KEY, value=p, updated_by_id=user.id))
    else:
        row.value, row.updated_by_id = p, user.id
    audit.record(db, user, "update", "outreach_policy", None, {"from": before, "to": p})
    db.commit()
    return p
