"""LinkedIn assistant: research notes, connection-message drafts and content ideas.

Deliberately limited: there is no LinkedIn scraping, no automatic connection requests and no message automation.
A person reads each draft, copies it into LinkedIn themselves and marks it done; that is recorded on the timeline.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.engagement import coordination as C
from app.engagement.policy import require
from app.models import Contact, Lead, LinkedinItem, ResearchFinding, User
from app.outreach import writer as W
from app.research.reasoning import SIGNAL_CATEGORIES

CONNECTION_LIMIT = 300  # LinkedIn's limit for a connection note
MESSAGE_LIMIT = 700


def check(body: str, kind: str, facts_text: str = "") -> dict[str, Any]:
    text = W._normalise(body)
    low = text.lower()
    checks = []

    def add(cid: str, label: str, ok: bool, detail: str = "") -> None:
        checks.append({"id": cid, "label": label, "passed": ok, "detail": "" if ok else detail})

    limit = CONNECTION_LIMIT if kind == "connection_draft" else MESSAGE_LIMIT if kind == "message_draft" else 3000
    add("length", f"At most {limit} characters", len(text) <= limit, f"{len(text)} characters")
    rel = [m.group(0) for p in W.FALSE_RELATIONSHIP + W.FIRST_EMAIL_ONLY for m in [re.search(p, low)] if m]
    add("relationship", "Doesn't imply an existing relationship", not rel, ", ".join(rel))
    hype = W.HYPE.search(text)
    add("hype", "No hype", not hype, hype.group(0) if hype else "")
    add("internal", "No internal research notes", not W.INTERNAL_NOTES.search(text), "Rewrite in plain language")
    nums = sorted(W._nums(W.TIME_DATE_RE.sub(" ", text)) - W._nums(facts_text))
    add("numbers", "No unsupported numbers", not nums or kind in ("note", "content_suggestion"), ", ".join(nums))
    add("placeholders", "No unfinished placeholders", not re.search(r"\[EDIT", text), "Replace the [EDIT …] parts")
    return {"passed": all(c["passed"] for c in checks), "checks": checks}


def _facts(db: Session, institution_id: int) -> list[ResearchFinding]:
    return list(
        db.scalars(
            select(ResearchFinding)
            .where(ResearchFinding.institution_id == institution_id, ResearchFinding.research_evidence_id.is_not(None))
            .order_by(ResearchFinding.confidence.desc())
            .limit(8)
        )
    )


def _save(db: Session, user: User, item: LinkedinItem, facts_text: str = "") -> LinkedinItem:
    item.quality = check(item.body, item.kind, facts_text)
    item.created_by_id = user.id
    db.add(item)
    db.flush()
    audit.record(db, user, "create", "linkedin_item", item.id, {"kind": item.kind})
    db.commit()
    return item


def research_note(db: Session, user: User, contact: Contact) -> LinkedinItem:
    lead = _lead(db, contact)
    facts = _facts(db, contact.institution_id)
    lines = [
        f"{contact.full_name} — {contact.designation or 'role not recorded'}, {contact.institution.name}",
        "",
        "From approved research:",
        *([f"• {f.summary} ({f.evidence_level or 'finding'})" for f in facts[:5]] or ["• No approved findings yet"]),
        "",
        "Check on LinkedIn yourself before contacting (we don't collect LinkedIn data automatically):",
        "• Is this still their current role and institution?",
        "• Anything they've posted recently about admissions, enquiries or technology?",
        "• Shared connections who could introduce you?",
    ]
    if contact.linkedin_url:
        lines.append(f"\nProfile: {contact.linkedin_url}")
    return _save(
        db, user,
        LinkedinItem(kind="note", contact_id=contact.id, lead_id=lead.id if lead else None, institution_id=contact.institution_id,
                     title=f"Research note: {contact.full_name}", body="\n".join(lines), facts_used=[f"F{f.id}" for f in facts[:5]],
                     is_demo=contact.is_demo),
    )  # fmt: skip


def connection_draft(db: Session, user: User, contact: Contact) -> LinkedinItem:
    lead = _lead(db, contact)
    v = C.check_outreach(db, contact, "email", "cold")
    if any(c.key == "suppressed" for c in v.blocking):
        raise HTTPException(409, "This person has opted out; don't contact them on LinkedIn either")
    role = f" as {contact.designation}" if contact.designation and len(contact.designation) < 40 else ""
    body = (
        f"Hello {contact.greeting_name}, I work with admissions teams at Indian universities on how they handle "
        f"student enquiries. I'd be glad to connect and follow your work{role} at {contact.institution.name}."
    )
    if len(body) > CONNECTION_LIMIT:
        body = f"Hello {contact.greeting_name}, I work with university admissions teams on handling student enquiries. I'd be glad to connect."
    return _save(
        db, user,
        LinkedinItem(kind="connection_draft", contact_id=contact.id, lead_id=lead.id if lead else None,
                     institution_id=contact.institution_id, title=f"Connection note for {contact.full_name}", body=body,
                     is_demo=contact.is_demo),
    )  # fmt: skip


def message_draft(db: Session, user: User, contact: Contact) -> LinkedinItem:
    from app.outreach.service import approved_facts

    lead = _lead(db, contact)
    facts = W.usable_facts(approved_facts(db, contact.institution_id, 365))
    ctx_line = (
        "From what I've read, it looks like " + W.CATEGORY_PHRASES[facts[0].category][1] + "."
        if facts
        else "[EDIT: one specific, true observation about their institution]"
    )
    body = (
        f"Thank you for connecting, {contact.greeting_name}. {ctx_line} We help admissions teams answer and follow up "
        "enquiries on phone and WhatsApp. Would a short conversation be useful at some point?"
    )
    return _save(
        db, user,
        LinkedinItem(kind="message_draft", contact_id=contact.id, lead_id=lead.id if lead else None,
                     institution_id=contact.institution_id, title=f"Message after connecting: {contact.full_name}", body=body,
                     facts_used=[facts[0].id] if facts else [], is_demo=contact.is_demo),
        " ".join(f.text for f in facts),
    )  # fmt: skip


def content_ideas(db: Session, user: User) -> list[LinkedinItem]:
    """Post ideas based on the themes that come up most in approved research. No prospect is named."""
    rows = db.scalars(select(ResearchFinding.category).where(ResearchFinding.research_evidence_id.is_not(None))).all()
    counts = Counter(rows)
    labels = {v: k for k, v in SIGNAL_CATEGORIES.items()}
    ideas = {
        "intake_period": (
            "Answering every enquiry during intake season",
            "What happens to the calls and WhatsApp messages that arrive after hours in peak admissions weeks — and simple ways to make sure none are missed.",
        ),
        "multi_campus": (
            "One admissions experience across many campuses",
            "How multi-campus universities keep enquiries consistent when each campus has its own helpline.",
        ),
        "hiring_admissions": (
            "Before you hire more counsellors",
            "Which admissions questions repeat most, and how teams free counsellors for the conversations that need them.",
        ),
        "response_delays": (
            "Speed to first response in admissions",
            "Why the first reply matters to applicants and practical ways to shorten it.",
        ),
        "new_course": (
            "Launching a new programme without flooding the phones",
            "Preparing the admissions desk for questions about a new course.",
        ),
    }
    out: list[LinkedinItem] = []
    picked = [labels.get(c, c) for c, _ in counts.most_common(10)] + list(ideas)
    for key in dict.fromkeys(picked):
        if key in ideas and len(out) < 3:
            title, angle = ideas[key]
            body = (
                f"{title}\n\nAngle: {angle}\n\nOutline:\n1. A situation admissions teams recognise\n2. What we've learned (no client names "
                "or figures unless approved)\n3. One practical tip they can use this week\n4. A question to invite comments\n\n"
                f"Why now: this theme appears in {counts.get(next((k for k, v in labels.items() if v == key), key), 0)} approved research findings."
            )
            out.append(_save(db, user, LinkedinItem(kind="content_suggestion", title=f"Post idea: {title}", body=body)))
    return out


def approve(db: Session, item: LinkedinItem, user: User) -> LinkedinItem:
    require(db, user, "linkedin_approve")
    if item.status != "draft":
        raise HTTPException(409, f"Item is {item.status}")
    if not item.quality.get("passed", True):
        raise HTTPException(422, "Fix the failed checks first")
    item.status, item.approved_by_id, item.approved_at = "approved", user.id, datetime.now(UTC)
    audit.record(db, user, "approve", "linkedin_item", item.id, {})
    db.commit()
    return item


def edit(db: Session, item: LinkedinItem, user: User, body: str) -> LinkedinItem:
    if item.status not in ("draft", "approved"):
        raise HTTPException(409, f"Item is {item.status}")
    item.body, item.status, item.approved_at, item.approved_by_id = body.strip(), "draft", None, None
    item.quality = check(item.body, item.kind)
    db.commit()
    return item


def mark_done(db: Session, item: LinkedinItem, user: User, note: str | None) -> LinkedinItem:
    """The person did it themselves on LinkedIn. Recorded on the unified timeline."""
    if item.kind in ("connection_draft", "message_draft") and item.status != "approved":
        raise HTTPException(409, "Approve the draft before marking it sent")
    item.status, item.done_at = "done", datetime.now(UTC)
    verb = {
        "connection_draft": "Connection request sent on LinkedIn (by hand)",
        "message_draft": "LinkedIn message sent (by hand)",
    }.get(item.kind, f"LinkedIn: {item.title}")
    C.record_activity(
        db, "linkedin", verb + (f" — {note}" if note else ""), lead_id=item.lead_id, institution_id=item.institution_id,
        contact_id=item.contact_id, user_id=user.id, body=item.body, details={"linkedin_item_id": item.id, "direction": "out"},
        is_demo=item.is_demo,
    )  # fmt: skip
    audit.record(db, user, "done", "linkedin_item", item.id, {"note": note})
    db.commit()
    return item


def _lead(db: Session, contact: Contact) -> Lead | None:
    return db.scalars(select(Lead).where(Lead.institution_id == contact.institution_id).order_by(Lead.id.desc())).first()
