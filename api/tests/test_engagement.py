"""Stage 4: coordinated engagement — WhatsApp, meeting assistant, follow-up tasks, channel coordination, LinkedIn.

All external services are mocks: the mock email provider, the mock WhatsApp provider (signed webhooks through the
real webhook endpoint) and the mock calendar.
"""

import json
import logging
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select, update

from app.db import SessionLocal
from app.engagement import coordination as CO
from app.engagement import followups as F
from app.engagement.logs import logger as engagement_logger
from app.meetings import service as M
from app.models import (
    Activity,
    CalendarConnection,
    Contact,
    Meeting,
    MockCalendarEvent,
    MockWhatsApp,
    Opportunity,
    OutreachMessage,
    Role,
    Task,
    User,
    WaMessage,
)
from app.seed import seed_engagement_demo
from app.whatsapp.providers.base import build_inbound_payload, build_status_payload, sign
from tests.conftest import login, make_user, new_contact
from tests.test_outreach_flow import adm, admin_user, enable_sending, mailbox_id, researched_prospect, start, step  # noqa: F401

IST = ZoneInfo("Asia/Kolkata")
SECRET = "mock-webhook-secret"


# ---------------------------------------------------------------------------
# fixtures and helpers
# ---------------------------------------------------------------------------
@pytest.fixture
def eng(adm, admin_user):
    """Admin client with Stage 4 demo set-up: approved WhatsApp templates, test calendars for everyone, WhatsApp on."""
    with SessionLocal() as db:
        users = {Role(u.role): u for u in db.scalars(select(User))}
        users.setdefault(Role.admin, db.get(User, admin_user.id))
        seed_engagement_demo(db, users)
        db.commit()
    p = adm.get("/api/engagement/policy").json()
    assert adm.put("/api/engagement/policy", json={**p, "whatsapp_enabled": True}).status_code == 200
    return adm


def calendars_for_everyone():
    with SessionLocal() as db:
        for u in db.scalars(select(User)):
            c = M.ensure_mock_calendar(db, u)
            assert c is not None
            c.status = "active"
        db.commit()


def sent_first_email(rep, adm, **kw):
    inst, lead, contact = researched_prospect(rep, **kw)
    enable_sending(adm)
    seq = start(rep, lead, contact)
    m = step(seq)
    assert rep.post(f"/api/outreach/messages/{m['id']}/approve").status_code == 200
    assert step(rep.post(f"/api/outreach/messages/{m['id']}/send-now").json())["status"] == "sent"
    return inst, lead, contact, seq


def wa_webhook(client, payload, secret=SECRET):
    raw = json.dumps(payload).encode()
    return client.post(
        "/api/whatsapp/webhook",
        content=raw,
        headers={"X-Hub-Signature-256": sign(secret, raw), "Content-Type": "application/json"},
    )


def wa_inbound(client, number, text, name="Asha Rao"):
    pid = f"wamid.TEST{time.time_ns()}"
    r = wa_webhook(client, build_inbound_payload("mock-phone", number, text, pid, name, int(time.time())))
    assert r.status_code == 200, r.text
    return pid


def opt_in(client, contact_id, number="+919812345678"):
    r = client.post(
        f"/api/whatsapp/contacts/{contact_id}/opt-in",
        json={"number": number, "method": "written", "evidence": "Replied to our email on 12 Oct asking for WhatsApp updates"},
    )
    assert r.status_code == 200, r.text
    return r.json()["whatsapp_number"]


def template_id(client, name="admissions_intro_v1"):
    return next(t["id"] for t in client.get("/api/whatsapp/templates").json() if t["name"] == name)


def tasks_for(lead_id):
    with SessionLocal() as db:
        return list(db.scalars(select(Task).where(Task.lead_id == lead_id).order_by(Task.id)))


def assert_task_complete(t: Task):
    """Module C: every task has an owner, a due date, a reason and a source activity."""
    assert t.owner_id and t.due_at and t.reason and t.source_activity_id, (t.title, t.owner_id, t.due_at, t.reason)


# ---------------------------------------------------------------------------
# ACCEPTANCE TEST
# ---------------------------------------------------------------------------
def test_acceptance_positive_email_reply_stops_sequence_proposes_meeting_creates_task_one_timeline(rep, eng, manager):
    calendars_for_everyone()
    inst, lead, contact, seq = sent_first_email(rep, eng)

    # The prospect replies positively to the email (their mail client quotes our email underneath)
    body = (
        "Thanks, this is interesting. Could we speak on Thursday at 3pm?\n\n"
        'On Tue, 13 Oct 2026 at 10:02, PlugDrop Sales wrote:\n> Dear Asha,\n> reply "unsubscribe" ...'
    )
    d = manager.post("/api/outreach/mock/reply", json={"sequence_id": seq["id"], "body": body}).json()

    # 1. the rest of the email sequence stops
    assert d["status"] == "stopped" and d["stop_reason"] == "replied"
    assert [m["status"] for m in d["messages"] if m["kind"] == "sequence" and m["step"] > 1] == ["cancelled", "cancelled"]
    assert d["replies"][0]["label"] == "meeting_request" and not d["replies"][0]["opt_out"]

    # 2. a meeting is proposed: agenda, times from the organiser's calendar (starting with the time they asked for),
    #    and a reply drafted for approval — nothing booked yet
    meetings = rep.get("/api/meetings", params={"lead_id": lead["id"]}).json()
    assert len(meetings) == 1
    mtg = meetings[0]
    assert mtg["status"] == "proposed" and mtg["source"] == "email_reply" and mtg["calendar_event_id"] is None
    assert "walkthrough" in mtg["agenda"] and len(mtg["proposed_slots"]) == 3
    first = datetime.fromisoformat(mtg["proposed_slots"][0]["start"]).astimezone(IST)
    assert (first.strftime("%A"), first.hour) == ("Thursday", 15) and mtg["proposed_slots"][0][
        "reason"
    ] == "the time they asked for"
    draft = step(rep.get(f"/api/outreach/sequences/{seq['id']}").json(), 1, "reply")
    assert draft["generated_by"] == "meeting-assistant" and draft["status"] == "draft" and draft["quality"]["passed"], draft
    assert "3:00 pm" in draft["body"] and "Would one of these times suit you?" in draft["body"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(MockCalendarEvent)) == 0

    # 3. follow-up tasks with owner, due date, reason and source activity
    tasks = tasks_for(lead["id"])
    reasons = {t.reason for t in tasks if t.status == "open"}
    assert {"reply_needed", "interested_no_meeting"} <= reasons
    for t in tasks:
        assert_task_complete(t)

    # The approved reply goes out by email; booking needs a separate approval
    assert rep.post(f"/api/outreach/messages/{draft['id']}/approve").status_code == 200
    assert step(rep.post(f"/api/outreach/messages/{draft['id']}/send-now").json(), 1, "reply")["status"] == "sent"
    assert not rep.get(f"/api/engagement/contacts/{contact['id']}/state").json()["awaiting_our_reply"]  # answered
    chosen = rep.post(f"/api/meetings/{mtg['id']}/choose", json={"start": mtg["proposed_slots"][0]["start"]}).json()
    assert chosen["status"] == "awaiting_approval"
    booked = rep.post(f"/api/meetings/{mtg['id']}/approve").json()
    assert booked["status"] == "scheduled" and booked["calendar_event_id"]
    with SessionLocal() as db:
        ev = db.scalars(select(MockCalendarEvent)).one()
        assert contact["email"] in ev.attendees
    assert rep.get(f"/api/leads/{lead['id']}").json()["stage"] == "meeting_booked"
    open_reasons = {t.reason for t in tasks_for(lead["id"]) if t.status == "open"}
    assert (
        "meeting_confirmation" in open_reasons
        and "interested_no_meeting" not in open_reasons
        and "reply_needed" not in open_reasons
    )

    # 4. one CRM timeline with every interaction, across channels
    tl = rep.get("/api/engagement/timeline", params={"lead_id": lead["id"]}).json()
    channels = {e["channel"] for e in tl}
    assert {"email", "meeting", "task", "research"} <= channels
    subjects = [e["subject"] for e in reversed(tl)]  # oldest first

    def at(fragment):
        return next(i for i, s in enumerate(subjects) if fragment in s)

    assert at("Email sent") < at("Reply from") < at("Meeting proposed") < at("Meeting booked")
    assert any(e["direction"] == "in" and e["channel"] == "email" for e in tl)
    assert any(e["subject"].startswith("Task created: Respond to") for e in tl)


# ---------------------------------------------------------------------------
# Module D: channel coordination
# ---------------------------------------------------------------------------
def test_whatsapp_and_email_share_one_record_and_timeline(rep, eng, manager):
    calendars_for_everyone()
    inst, lead, contact, seq = sent_first_email(rep, eng)
    number = opt_in(rep, contact["id"])
    # WhatsApp can't start while the email sequence is running (would duplicate it)
    r = rep.post("/api/whatsapp/conversations", json={"contact_id": contact["id"], "template_id": template_id(rep)})
    assert r.status_code == 409 and "email sequence is running" in r.json()["detail"]
    # The prospect answers on WhatsApp instead of email: email follow-ups stop, the reply is tracked in one place
    wa_inbound(eng, number, "Got your email. Can we talk tomorrow at 11am?")
    d = rep.get(f"/api/outreach/sequences/{seq['id']}").json()
    assert d["status"] == "stopped" and d["stop_reason"] == "replied"
    mtg = rep.get("/api/meetings", params={"lead_id": lead["id"]}).json()[0]
    assert mtg["source"] == "whatsapp" and mtg["draft_wa_id"]
    tl = rep.get("/api/engagement/timeline", params={"lead_id": lead["id"]}).json()
    assert {"email", "whatsapp", "meeting", "task"} <= {e["channel"] for e in tl}
    state = rep.get(f"/api/engagement/contacts/{contact['id']}/state").json()
    assert state["awaiting_our_reply"] and state["meeting"]["status"] == "proposed" and state["conversation"]["window_open_until"]
    assert not state["checks"]["email_follow_up"]["allowed"]  # no automated email while they wait for our answer


def test_recent_whatsapp_defers_email_follow_up_and_meetings_block_outreach(rep, eng):
    calendars_for_everyone()
    inst, lead, contact, seq = sent_first_email(rep, eng)
    number = opt_in(rep, contact["id"])
    # Stop the sequence by hand, then message on WhatsApp (they have opted in)
    rep.post(f"/api/outreach/sequences/{seq['id']}/stop", json={"reason": "cancelled"})
    r = rep.post("/api/whatsapp/conversations", json={"contact_id": contact["id"], "template_id": template_id(rep)})
    assert r.status_code == 201  # drafting is allowed; the 20-hour gap is checked when it is sent
    out = next(m for m in r.json()["messages"] if m["direction"] == "out")
    held = rep.post(f"/api/whatsapp/messages/{out['id']}/send").json()
    held_msg = next(m for m in held["messages"] if m["id"] == out["id"])
    assert held_msg["status"] == "queued" and "Already messaged on email" in held_msg["error"]
    rep.post(f"/api/whatsapp/messages/{out['id']}/cancel")
    with SessionLocal() as db:  # pretend the email went out 3 days ago so WhatsApp is allowed
        db.execute(update(OutreachMessage).values(sent_at=datetime.now(UTC) - timedelta(days=3)))
        db.commit()
    conv = rep.post("/api/whatsapp/conversations", json={"contact_id": contact["id"], "template_id": template_id(rep)}).json()
    out = [m for m in conv["messages"] if m["direction"] == "out" and m["status"] == "draft"][-1]
    r = rep.post(f"/api/whatsapp/messages/{out['id']}/send")
    assert r.status_code == 200, r.text
    assert next(m for m in r.json()["messages"] if m["id"] == out["id"])["status"] == "sent"
    with SessionLocal() as db:
        c = db.get(Contact, contact["id"])
        v = CO.check_outreach(db, c, "email", "follow_up")
        assert not v.allowed and any(x.key == "recent_message" and x.kind == "defer" for x in v.checks)
    # A meeting being arranged blocks automated outreach on both channels
    rep.post("/api/meetings", json={"lead_id": lead["id"], "contact_id": contact["id"], "note": "Agreed on a call"})
    with SessionLocal() as db:
        c = db.get(Contact, contact["id"])
        for ch in ("email", "whatsapp"):
            assert any(x.key == "meeting" for x in CO.check_outreach(db, c, ch, "follow_up").blocking)
    assert number


# ---------------------------------------------------------------------------
# Module A: WhatsApp
# ---------------------------------------------------------------------------
def test_whatsapp_requires_opt_in_and_approved_template(rep, eng):
    inst, lead, contact = researched_prospect(rep)
    r = rep.post("/api/whatsapp/conversations", json={"contact_id": contact["id"], "template_id": template_id(rep)})
    assert r.status_code == 422  # no WhatsApp number / opt-in
    # A number from research is not consent: opt-in needs a method and evidence
    bad = rep.post(
        f"/api/whatsapp/contacts/{contact['id']}/opt-in",
        json={"number": "+919812345678", "method": "scraped", "evidence": "found on website"},
    )
    assert bad.status_code == 422
    opt_in(rep, contact["id"])
    conv = rep.post("/api/whatsapp/conversations", json={"contact_id": contact["id"], "template_id": template_id(rep)}).json()
    out = next(m for m in conv["messages"] if m["direction"] == "out")
    assert out["kind"] == "template" and "Dear" not in out["body"] and "Reply STOP" in out["body"]
    # Free text outside the 24-hour window is refused
    r = rep.post(f"/api/whatsapp/conversations/{conv['id']}/messages", json={"text": "Hello, any update?"})
    assert r.status_code == 422 and "24 hours" in r.json()["detail"]
    sent = rep.post(f"/api/whatsapp/messages/{out['id']}/send").json()
    msg = next(m for m in sent["messages"] if m["id"] == out["id"])
    assert msg["status"] == "sent"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(MockWhatsApp)) == 1
    # Sending the same message again does nothing
    assert rep.post(f"/api/whatsapp/messages/{out['id']}/send").status_code == 409
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(MockWhatsApp)) == 1


def test_whatsapp_delivery_status_inbound_window_and_suggestion(rep, eng):
    inst, lead, contact = researched_prospect(rep)
    number = opt_in(rep, contact["id"])
    conv = rep.post("/api/whatsapp/conversations", json={"contact_id": contact["id"], "template_id": template_id(rep)}).json()
    out = next(m for m in conv["messages"] if m["direction"] == "out")
    rep.post(f"/api/whatsapp/messages/{out['id']}/send")
    with SessionLocal() as db:
        pid = db.get(WaMessage, out["id"]).provider_message_id
    for st in ("delivered", "read", "delivered"):  # out-of-order status is ignored
        assert wa_webhook(eng, build_status_payload(pid, st, int(time.time()))).status_code == 200
    d = rep.get(f"/api/whatsapp/conversations/{conv['id']}").json()
    assert next(m for m in d["messages"] if m["id"] == out["id"])["status"] == "read"
    # Inbound message opens the 24-hour window, gets a label and a suggested reply (not sent)
    wa_inbound(eng, number, "Please share the brochure and pricing")
    d = rep.get(f"/api/whatsapp/conversations/{conv['id']}").json()
    inbound = next(m for m in d["messages"] if m["direction"] == "in")
    assert inbound["label"] == "needs_information" and inbound["suggestion"] and d["window_open"]
    r = rep.post(f"/api/whatsapp/conversations/{conv['id']}/messages", json={"text": "Thanks, Dr. Rao — sharing it now."})
    assert r.status_code == 200
    reply = next(m for m in r.json()["messages"] if m["direction"] == "out" and m["kind"] == "text")
    assert reply["status"] == "draft"  # a person still approves it
    assert any(t.reason == "reply_needed" for t in tasks_for(lead["id"]))


def test_whatsapp_stop_opts_out_everywhere(rep, eng, manager):
    inst, lead, contact, seq = sent_first_email(rep, eng)
    number = opt_in(rep, contact["id"])
    wa_inbound(eng, number, "STOP")
    with SessionLocal() as db:
        c = db.get(Contact, contact["id"])
        assert any(x.key == "suppressed" for x in CO.check_outreach(db, c, "email", "reply").blocking)
    d = rep.get(f"/api/outreach/sequences/{seq['id']}").json()
    assert d["stop_reason"] == "opted_out"
    convs = rep.get("/api/whatsapp/conversations").json()
    assert convs[0]["state"] == "closed"
    r = rep.post("/api/whatsapp/conversations", json={"contact_id": contact["id"], "template_id": template_id(rep)})
    assert r.status_code in (409, 422)
    # opting back in can't be done on their behalf while the opt-out stands
    r = rep.post(
        f"/api/whatsapp/contacts/{contact['id']}/opt-in",
        json={"number": number, "method": "written", "evidence": "Asked again in writing, 20 Oct"},
    )
    assert r.status_code == 409


def test_webhook_signature_verification_and_deduplication(rep, eng, anon):
    payload = build_inbound_payload("mock-phone", "+919800000001", "Hello", "wamid.DUP1", "Someone", int(time.time()))
    assert wa_webhook(anon, payload, secret="wrong-secret").status_code == 401
    raw = json.dumps(payload).encode()
    assert anon.post("/api/whatsapp/webhook", content=raw).status_code == 401  # no signature at all
    first = wa_webhook(anon, payload).json()
    again = wa_webhook(anon, payload).json()
    assert first["messages"] == 1 and again["duplicates"] == 1
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(WaMessage).where(WaMessage.direction == "in")) == 1
    # Meta's verification handshake
    assert (
        anon.get(
            "/api/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"}
        ).status_code
        == 403
    )


def test_whatsapp_retries_failures_limits_and_switch(rep, eng):
    inst, lead, contact = researched_prospect(rep)
    opt_in(rep, contact["id"], number="+919800001111")  # mock: temporary error on first try
    conv = rep.post("/api/whatsapp/conversations", json={"contact_id": contact["id"], "template_id": template_id(rep)}).json()
    out = next(m for m in conv["messages"] if m["direction"] == "out")
    d = rep.post(f"/api/whatsapp/messages/{out['id']}/send").json()
    m = next(x for x in d["messages"] if x["id"] == out["id"])
    assert m["status"] == "queued" and m["attempts"] == 1 and "Temporary" in m["error"]
    with SessionLocal() as db:
        db.execute(update(WaMessage).where(WaMessage.id == out["id"]).values(scheduled_for=datetime.now(UTC)))
        db.commit()
        from app.whatsapp.service import dispatch_due

        dispatch_due(db)
    d = rep.get(f"/api/whatsapp/conversations/{conv['id']}").json()
    assert next(x for x in d["messages"] if x["id"] == out["id"])["status"] == "sent"

    c2 = new_contact(rep, inst["id"], first="Ravi", last="Kumar", email="ravi.kumar@meridian-college-commerce.example")
    opt_in(rep, c2["id"], number="+919800000000")  # mock: not a WhatsApp user
    conv2 = rep.post("/api/whatsapp/conversations", json={"contact_id": c2["id"], "template_id": template_id(rep)}).json()
    out2 = next(m for m in conv2["messages"] if m["direction"] == "out")
    d = rep.post(f"/api/whatsapp/messages/{out2['id']}/send").json()
    assert next(x for x in d["messages"] if x["id"] == out2["id"])["status"] == "failed"

    # Admin switch off: messages are held with the reason
    p = eng.get("/api/engagement/policy").json()
    eng.put("/api/engagement/policy", json={**p, "whatsapp_enabled": False})
    c3 = new_contact(rep, inst["id"], first="Meena", last="Iyer", email="meena.iyer@meridian-college-commerce.example")
    opt_in(rep, c3["id"], number="+919800000003")
    conv3 = rep.post("/api/whatsapp/conversations", json={"contact_id": c3["id"], "template_id": template_id(rep)}).json()
    out3 = next(m for m in conv3["messages"] if m["direction"] == "out")
    r = rep.post(f"/api/whatsapp/messages/{out3['id']}/send")
    assert r.status_code == 409 and "switched off" in r.json()["detail"]


def test_human_handoff(rep, eng, manager):
    inst, lead, contact = researched_prospect(rep)
    number = opt_in(rep, contact["id"])
    wa_inbound(eng, number, "I would like to speak to a person about this please")
    conv = rep.get("/api/whatsapp/conversations").json()[0]
    assert conv["state"] == "handoff" and conv["assigned_to"]
    assert any(t.reason == "whatsapp_handoff" for t in tasks_for(lead["id"]))
    # Someone who doesn't own the conversation can't send in it
    other = login(make_user(Role.rep, "other.rep@test.example"))
    r = other.post(f"/api/whatsapp/conversations/{conv['id']}/messages", json={"text": "Hi"})
    assert r.status_code == 409
    released = rep.post(f"/api/whatsapp/conversations/{conv['id']}/assign", json={"action": "release"}).json()
    assert released["state"] == "open"


def test_template_rules_and_permissions(rep, manager, eng):
    body = {"name": "promo_v2", "category": "marketing", "purpose": "first_contact", "body": "Hello {{1}}, we have news for you.", "params": [{"label": "Greeting", "source": "contact.greeting"}]}  # fmt: skip
    assert rep.post("/api/whatsapp/templates", json=body).status_code == 403  # managers manage templates by default
    r = manager.post("/api/whatsapp/templates", json=body)
    assert r.status_code == 422 and "STOP" in r.json()["detail"]
    r = manager.post("/api/whatsapp/templates", json={**body, "body": "Hello {{2}}, news. Reply STOP to opt out."})
    assert r.status_code == 422 and "numbered" in r.json()["detail"]
    r = manager.post("/api/whatsapp/templates", json={**body, "name": "Promo V2", "body": "Hello {{1}}. Reply STOP to opt out."})
    assert r.status_code == 422
    t = manager.post(
        "/api/whatsapp/templates", json={**body, "body": "Hello {{1}}, a short note from PlugDrop.AI. Reply STOP to opt out."}
    ).json()
    assert t["status"] == "draft"
    assert manager.post(f"/api/whatsapp/templates/{t['id']}/submit").json()["status"] == "approved"  # mock provider approves


# ---------------------------------------------------------------------------
# Module B: meeting assistant
# ---------------------------------------------------------------------------
def test_requested_time_parsing():
    now = datetime(2026, 10, 13, 6, 0, tzinfo=UTC)  # Tuesday 11:30 IST
    got = [t.astimezone(IST) for t in M.requested_times("Can we talk Thursday at 3pm?", now, "Asia/Kolkata")]
    assert [(g.strftime("%a %d"), g.hour) for g in got] == [("Thu 15", 15)]
    got = [t.astimezone(IST) for t in M.requested_times("tomorrow morning works", now, "Asia/Kolkata")]
    assert [(g.strftime("%a"), g.hour, g.minute) for g in got] == [("Wed", 10, 30)]
    assert M.requested_times("Sounds interesting, tell me more", now, "Asia/Kolkata") == []


def test_free_slots_respect_calendar_hours_and_notice(rep, eng):
    calendars_for_everyone()
    with SessionLocal() as db:
        u = db.scalars(select(User).where(User.role == "rep")).first()
        now = datetime.now(UTC)
        thursday = (now.astimezone(IST) + timedelta(days=(3 - now.astimezone(IST).weekday()) % 7 or 7)).date()
        busy_start = datetime.combine(thursday, datetime.min.time(), IST).replace(hour=15)
        db.add(
            MockCalendarEvent(
                owner_email=u.email, title="Board meeting", start_at=busy_start, end_at=busy_start + timedelta(hours=1)
            )
        )
        db.commit()
        slots, note = M.free_slots(db, u, now, preferred=[busy_start.astimezone(UTC)])
        assert note is None and len(slots) == 3
        for s in slots:
            st = datetime.fromisoformat(s["start"])
            loc = st.astimezone(IST)
            assert loc.weekday() < 5 and 10 <= loc.hour < 17 and st >= now + timedelta(hours=18)
            assert not (st < busy_start + timedelta(hours=1) and busy_start < st + timedelta(minutes=30))


def test_meeting_needs_approval_books_once_and_keeps_prep_notes_internal(rep, eng, manager):
    calendars_for_everyone()
    inst, lead, contact = researched_prospect(rep)
    mtg = rep.post("/api/meetings", json={"lead_id": lead["id"], "contact_id": contact["id"], "note": "Agreed on a call"}).json()
    assert rep.post(f"/api/meetings/{mtg['id']}/approve").status_code == 409  # choose a time first
    slot = mtg["proposed_slots"][1]["start"]
    rep.post(f"/api/meetings/{mtg['id']}/choose", json={"start": slot})
    assert any(t.reason == "meeting_approval" for t in tasks_for(lead["id"]))
    # an admin can require a manager to approve meetings
    eng.put("/api/engagement/permissions", json={"meeting_create": "manager"})
    assert rep.post(f"/api/meetings/{mtg['id']}/approve").status_code == 403
    booked = manager.post(f"/api/meetings/{mtg['id']}/approve").json()
    assert booked["status"] == "scheduled"
    assert manager.post(f"/api/meetings/{mtg['id']}/approve").status_code == 409
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(MockCalendarEvent)) == 1
    tl = rep.get("/api/engagement/timeline", params={"lead_id": lead["id"]}).json()
    booked_entry = next(e for e in tl if e["subject"].startswith("Meeting booked"))
    assert "Prep (internal" not in (booked_entry["body"] or "")
    # outcome → post-meeting task; the confirmation task closes
    out = rep.post(
        f"/api/meetings/{mtg['id']}/outcome",
        json={"outcome": "positive", "notes": "Keen on a WhatsApp pilot", "next_steps": "Send pilot proposal by Friday"},
    ).json()
    assert out["status"] == "completed"
    open_tasks = [t for t in tasks_for(lead["id"]) if t.status == "open"]
    post = next(t for t in open_tasks if t.reason == "post_meeting")
    assert post.title.startswith("Send pilot proposal by Friday") and not any(
        t.reason == "meeting_confirmation" for t in open_tasks
    )
    for t in tasks_for(lead["id"]):
        assert_task_complete(t)


def test_slot_taken_meanwhile_is_refused_and_cancel_removes_event(rep, eng):
    calendars_for_everyone()
    inst, lead, contact = researched_prospect(rep)
    mtg = rep.post("/api/meetings", json={"lead_id": lead["id"], "contact_id": contact["id"]}).json()
    slot = datetime.fromisoformat(mtg["proposed_slots"][0]["start"])
    rep.post(f"/api/meetings/{mtg['id']}/choose", json={"start": slot.isoformat()})
    rep.post(
        "/api/meetings/calendars/mock/busy", json={"start": slot.isoformat(), "end": (slot + timedelta(minutes=30)).isoformat()}
    )
    r = rep.post(f"/api/meetings/{mtg['id']}/approve")
    assert r.status_code == 409 and "no longer free" in r.json()["detail"]
    other = datetime.fromisoformat(mtg["proposed_slots"][1]["start"])
    rep.post(f"/api/meetings/{mtg['id']}/choose", json={"start": other.isoformat()})
    assert rep.post(f"/api/meetings/{mtg['id']}/approve").json()["status"] == "scheduled"
    assert (
        rep.post(f"/api/meetings/{mtg['id']}/cancel", json={"reason": "They asked to postpone"}).json()["status"] == "cancelled"
    )
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(MockCalendarEvent).where(MockCalendarEvent.title != "Busy")) == 0


def test_without_a_calendar_the_assistant_leaves_times_to_a_person(rep, eng):
    inst, lead, contact = researched_prospect(rep)
    with SessionLocal() as db:
        db.execute(update(CalendarConnection).values(status="disabled"))
        db.commit()
    mtg = rep.post("/api/meetings", json={"lead_id": lead["id"], "contact_id": contact["id"]}).json()
    assert mtg["proposed_slots"] == []
    tl = rep.get("/api/engagement/timeline", params={"lead_id": lead["id"]}).json()
    assert any("No approved calendar" in e["subject"] for e in tl)


# ---------------------------------------------------------------------------
# Module C: follow-up tasks
# ---------------------------------------------------------------------------
def test_reconnect_date_parsing():
    rec = datetime(2026, 10, 13, tzinfo=UTC)
    when, why = F.reconnect_date("Please connect after admissions close in December.", rec, 90)
    assert when.month == 12 and "December" in why
    when, _ = F.reconnect_date("Try us in March", rec, 90)
    assert (when.year, when.month) == (2027, 3)
    when, why = F.reconnect_date("Not now, thanks", rec, 90)
    assert (when - rec).days == 90 and "default" in why


def test_not_now_reply_creates_reconnect_task(rep, eng, manager):
    inst, lead, contact, seq = sent_first_email(rep, eng)
    manager.post(
        "/api/outreach/mock/reply", json={"sequence_id": seq["id"], "body": "Not at present. Kindly connect in December."}
    )
    t = next(t for t in tasks_for(lead["id"]) if t.reason == "reconnect_later")
    assert t.due_at.month == 12 and "December" in t.description
    assert_task_complete(t)


def test_sweep_creates_post_meeting_and_proposal_tasks_once(rep, eng):
    calendars_for_everyone()
    inst, lead, contact = researched_prospect(rep)
    mtg = rep.post("/api/meetings", json={"lead_id": lead["id"], "contact_id": contact["id"]}).json()
    rep.post(f"/api/meetings/{mtg['id']}/choose", json={"start": mtg["proposed_slots"][0]["start"]})
    rep.post(f"/api/meetings/{mtg['id']}/approve")
    opp = rep.post(
        "/api/opportunities",
        json={"lead_id": lead["id"], "name": "Voice AI pilot", "estimated_value": "500000", "currency": "INR"},
    ).json()
    rep.patch(f"/api/opportunities/{opp['id']}", json={"stage": "proposal"})
    with SessionLocal() as db:
        past = datetime.now(UTC) - timedelta(days=8)
        db.execute(update(Meeting).values(start_at=past, end_at=past + timedelta(minutes=30)))
        db.execute(update(Activity).where(Activity.lead_id == lead["id"]).values(occurred_at=past))
        db.commit()
        first = F.sweep(db)
        second = F.sweep(db)
    assert first == {"post_meeting": 1, "proposal_follow_up": 1} and second == {"post_meeting": 0, "proposal_follow_up": 0}
    reasons = [t.reason for t in tasks_for(lead["id"]) if t.status == "open"]
    assert "post_meeting" in reasons and "proposal_follow_up" in reasons
    with SessionLocal() as db:
        assert db.get(Opportunity, opp["id"]) is not None
    for t in tasks_for(lead["id"]):
        assert_task_complete(t)


def test_manual_tasks_also_get_reason_due_date_and_source(rep):
    inst, lead, contact = researched_prospect(rep)
    t = rep.post("/api/tasks", json={"title": "Call the registrar", "lead_id": lead["id"]}).json()
    assert t["reason"] == "manual" and t["due_at"] and t["source_activity_id"] and t["owner"]


# ---------------------------------------------------------------------------
# LinkedIn assistant
# ---------------------------------------------------------------------------
def test_linkedin_drafts_need_approval_and_are_done_by_hand(rep, eng):
    inst, lead, contact = researched_prospect(rep)
    note = rep.post("/api/linkedin/generate", json={"kind": "note", "contact_id": contact["id"]}).json()
    assert "we don't collect LinkedIn data automatically" in note["body"]
    conn = rep.post("/api/linkedin/generate", json={"kind": "connection_draft", "contact_id": contact["id"]}).json()
    assert len(conn["body"]) <= 300 and conn["quality"]["passed"]
    assert rep.post(f"/api/linkedin/items/{conn['id']}/done", json={}).status_code == 409  # approve first
    assert rep.post(f"/api/linkedin/items/{conn['id']}/approve").json()["status"] == "approved"
    rep.post(f"/api/linkedin/items/{conn['id']}/done", json={"note": "sent from my LinkedIn"})
    tl = rep.get("/api/engagement/timeline", params={"lead_id": lead["id"], "channel": "linkedin"}).json()
    assert tl and tl[0]["subject"].startswith("Connection request sent on LinkedIn (by hand)")
    edited = rep.patch(
        f"/api/linkedin/items/{note['id']}", json={"body": "As we discussed last week, great to reconnect!"}
    ).json()
    assert not edited["quality"]["passed"]
    ideas = rep.post("/api/linkedin/content-ideas").json()
    assert ideas and all(inst["name"] not in i["body"] for i in ideas)


def test_linkedin_respects_opt_out(rep, eng):
    inst, lead, contact = researched_prospect(rep)
    rep.post("/api/consent", json={"contact_id": contact["id"], "status": "opted_out"})
    assert rep.post("/api/linkedin/generate", json={"kind": "connection_draft", "contact_id": contact["id"]}).status_code == 409


# ---------------------------------------------------------------------------
# administration and logs
# ---------------------------------------------------------------------------
def test_admin_controls_permissions_and_policy(rep, manager, eng):
    assert rep.put("/api/engagement/permissions", json={"email_approve": "manager"}).status_code == 403
    perms = eng.put("/api/engagement/permissions", json={"email_approve": "manager"}).json()
    assert next(p for p in perms if p["action"] == "email_approve")["min_role"] == "manager"
    inst, lead, contact = researched_prospect(rep)
    seq = start(rep, lead, contact)
    assert rep.post(f"/api/outreach/messages/{step(seq)['id']}/approve").status_code == 403
    assert manager.post(f"/api/outreach/messages/{step(seq)['id']}/approve").status_code == 200
    assert eng.put("/api/engagement/permissions", json={"email_approve": "intern"}).status_code == 422
    p = eng.get("/api/engagement/policy").json()
    assert eng.put("/api/engagement/policy", json={**p, "whatsapp_per_minute": 0}).status_code == 422


def test_structured_logs_mask_numbers_and_never_log_message_text(rep, eng, caplog):
    inst, lead, contact = researched_prospect(rep)
    records: list[str] = []

    class H(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    h = H()
    engagement_logger.addHandler(h)
    try:
        number = opt_in(rep, contact["id"], number="+919876543210")
        wa_inbound(eng, number, "Secret budget is 40 lakh, call me")
    finally:
        engagement_logger.removeHandler(h)
    lines = [json.loads(r) for r in records]
    assert any(line["event"] == "whatsapp_webhook" for line in lines)
    text = "\n".join(records)
    assert "9876543210" not in text and "40 lakh" not in text


def test_whatsapp_suggestion_knows_the_meeting_is_booked(rep, eng):
    calendars_for_everyone()
    inst, lead, contact = researched_prospect(rep)
    number = opt_in(rep, contact["id"])
    mtg = rep.post("/api/meetings", json={"lead_id": lead["id"], "contact_id": contact["id"]}).json()
    rep.post(f"/api/meetings/{mtg['id']}/choose", json={"start": mtg["proposed_slots"][0]["start"]})
    rep.post(f"/api/meetings/{mtg['id']}/approve")
    wa_inbound(eng, number, "Looking forward to it. Can you share the agenda here?")
    conv = rep.get("/api/whatsapp/conversations").json()[0]
    inbound = next(m for m in rep.get(f"/api/whatsapp/conversations/{conv['id']}").json()["messages"] if m["direction"] == "in")
    assert "looking forward to our call on" in inbound["suggestion"] and "[EDIT" in inbound["suggestion"]
    assert "times below" not in inbound["suggestion"]
