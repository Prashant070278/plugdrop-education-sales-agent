"""Acceptance + integration tests for the outreach agent, using the mock email provider.

ACCEPTANCE (from the Stage 3 brief): a prospect is researched, an email is generated, edited, approved, sent through the
mock provider, recorded in the CRM, and stopped from receiving follow-ups after replying or opting out.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from app.db import SessionLocal
from app.models import Activity, ConsentRecord, Mailbox, MockMail, OutreachMessage, Role, Task, User
from app.outreach import service as S
from app.seed import seed_outreach_demo
from tests.conftest import login, make_user, new_contact, new_institution
from tests.test_research_flow import drain

OPEN_WINDOW = {"start_hour": 0, "end_hour": 24, "timezone": "UTC", "weekdays_only": False}


@pytest.fixture
def admin_user():
    return make_user(Role.admin, "boss@test.example")


@pytest.fixture
def adm(admin_user):
    with SessionLocal() as db:
        seed_outreach_demo(db, {Role.admin: db.get(User, admin_user.id)})
        db.commit()
    c = login(admin_user)
    p = c.get("/api/outreach/policy").json()
    assert c.put("/api/outreach/policy", json={**p, "send_window": OPEN_WINDOW}).status_code == 200
    return c


def mailbox_id():
    with SessionLocal() as db:
        return db.scalar(select(Mailbox.id).where(Mailbox.email_address == "sales-demo@plugdrop.example"))


def enable_sending(adm, per_domain=3):
    p = adm.get("/api/outreach/policy").json()
    assert (
        adm.put("/api/outreach/policy", json={**p, "sending_mode": "send", "per_domain_daily_limit": per_domain}).status_code
        == 200
    )
    assert adm.patch(f"/api/outreach/mailboxes/{mailbox_id()}", json={"sending_enabled": True}).json()["effective_mode"] == "send"


def researched_prospect(rep, slug="meridian-college-commerce", name="Meridian College of Commerce", email_local="asha.rao"):
    """Research an institution, approve and push the verified facts, add a contact with a business email."""
    inst = new_institution(rep, name=name, website=f"https://www.{slug}.example", student_size_range="5k_20k")
    lead = rep.post("/api/leads", json={"institution_id": inst["id"]}).json()
    rep.post("/api/research/jobs", json={"institution_ids": [inst["id"]]})
    drain()
    run = rep.get(f"/api/research/institutions/{inst['id']}").json()["result"]
    approve = {e["id"]: "approved" for e in run["evidence"] if e["evidence_level"] != "hypothesis"}
    assert rep.post(f"/api/research/runs/{run['id']}/review", json={"evidence": approve}).status_code == 200
    assert rep.post(f"/api/research/runs/{run['id']}/push").status_code == 200
    contact = new_contact(
        rep, inst["id"], first="Asha", last="Rao", email=f"{email_local}@{slug}.example", designation="Director of Admissions"
    )
    return inst, lead, contact


def start(rep, lead, contact):
    r = rep.post("/api/outreach/sequences", json={"lead_id": lead["id"], "contact_id": contact["id"], "mailbox_id": mailbox_id()})
    assert r.status_code == 201, r.text
    return r.json()


def step(seq, n=1, kind="sequence"):
    return next(m for m in seq["messages"] if m["step"] == n and m["kind"] == kind)


# ---------------------------------------------------------------------------
# Acceptance test
# ---------------------------------------------------------------------------
def test_acceptance_research_generate_edit_approve_send_record_and_stop(rep, adm, manager):
    inst, lead, contact = researched_prospect(rep)

    # 1. generate: a proposed sequence with spacing and the policy's maximum attempts
    seq = start(rep, lead, contact)
    assert seq["status"] == "draft" and seq["max_attempts"] == 3 and seq["spacing_days"] == [0, 4, 7]
    assert [m["planned_offset_days"] for m in seq["messages"]] == [0, 4, 11]
    first = step(seq)
    assert first["quality"]["passed"], first["quality"]
    assert first["generation"]["facts_used"] and first["generation"]["content_used"]
    assert inst["name"] in first["subject"] and "unsubscribe" in first["body"]

    # 2. edit: a non-compliant edit blocks approval; a compliant edit passes
    bad = rep.patch(
        f"/api/outreach/messages/{first['id']}",
        json={
            "subject": first["subject"],
            "body": first["body"].replace(
                "Dear Asha,", "Dear Asha, great speaking with you! This will revolutionize admissions."
            ),
        },
    ).json()
    assert not step(bad)["quality"]["passed"]
    r = rep.post(f"/api/outreach/messages/{first['id']}/approve")
    assert r.status_code == 422 and "existing relationship" in r.json()["detail"]
    good_body = first["body"].replace("Dear Asha,", "Dear Asha,\n\nI lead education partnerships at PlugDrop.AI.")
    edited = step(
        rep.patch(f"/api/outreach/messages/{first['id']}", json={"subject": first["subject"], "body": good_body}).json()
    )
    assert edited["edited"] and edited["quality"]["passed"]

    # 3. approve + send through the mock provider (admin has enabled sending for the mock mailbox)
    enable_sending(adm)
    assert step(rep.post(f"/api/outreach/messages/{first['id']}/approve").json())["status"] == "approved"
    sent = step(rep.post(f"/api/outreach/messages/{first['id']}/send-now").json())
    assert sent["status"] == "sent" and sent["provider"] == "mock" and sent["provider_message_id"]
    with SessionLocal() as db:
        out = db.scalars(select(MockMail).where(MockMail.folder == "sent")).all()
        assert len(out) == 1 and out[0].to_email == contact["email"] and "I lead education partnerships" in out[0].body

    # 4. recorded in the CRM: timeline, lead stage, events, audit
    tl = rep.get("/api/activities", params={"lead_id": lead["id"]}).json()["items"]
    assert any(a["activity_type"] == "email" and a["subject"].startswith("Email sent") for a in tl)
    assert rep.get(f"/api/leads/{lead['id']}").json()["stage"] == "contacted"
    detail = rep.get(f"/api/outreach/sequences/{seq['id']}").json()
    events = [e["event"] for e in detail["events"]]
    assert {"generated", "edited", "approved", "send_now_requested", "sent"} <= set(events)
    assert {"approve"} <= {
        a["action"] for a in manager.get("/api/audit", params={"entity_type": "outreach_message"}).json()["items"]
    }

    # duplicate-send protection: the same email can't be sent twice
    assert rep.post(f"/api/outreach/messages/{first['id']}/send-now").status_code == 409
    S.dispatch_due(SessionLocal())
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(MockMail).where(MockMail.folder == "sent")) == 1

    # delivery tracking
    adm.post("/api/outreach/sync")
    assert step(rep.get(f"/api/outreach/sequences/{seq['id']}").json())["status"] == "delivered"

    # follow-up approved and scheduled for after the spacing interval
    f2 = step(detail, 2)
    rep.post(f"/api/outreach/messages/{f2['id']}/approve")
    f2 = step(rep.post(f"/api/outreach/messages/{f2['id']}/schedule", json={}).json(), 2)
    assert f2["status"] == "scheduled" and datetime.fromisoformat(f2["scheduled_for"]) > datetime.now(UTC) + timedelta(days=3)

    # 5. the prospect replies -> follow-ups stop
    after = manager.post(
        "/api/outreach/mock/reply",
        json={"sequence_id": seq["id"], "body": "Sounds interesting. Could you send more information?"},
    ).json()
    assert after["status"] == "stopped" and after["stop_reason"] == "replied"
    assert after["replies"][0]["label"] == "needs_information"
    assert step(after, 2)["status"] == "cancelled" and step(after, 3)["status"] == "cancelled"
    assert rep.get(f"/api/leads/{lead['id']}").json()["stage"] == "engaged"
    with SessionLocal() as db:
        assert db.scalar(select(Task).where(Task.lead_id == lead["id"], Task.title.like("Respond to%"))) is not None
        # even if the clock jumps forward, nothing more is sent
        S.dispatch_due(db, now=datetime.now(UTC) + timedelta(days=60))
        assert db.scalar(select(func.count()).select_from(MockMail).where(MockMail.folder == "sent")) == 1
    replies = rep.get("/api/outreach/replies").json()["items"]
    assert replies and replies[0]["reply"]["label"] == "needs_information"

    # a human-approved reply draft can still be prepared after a reply
    d = rep.post(f"/api/outreach/replies/{replies[0]['reply']['id']}/draft").json()
    assert step(d, 1, "reply")["status"] == "draft"


def test_acceptance_opt_out_stops_and_blocks_future_outreach(rep, adm, manager):
    _, lead, contact = researched_prospect(rep)
    enable_sending(adm)
    seq = start(rep, lead, contact)
    m = step(seq)
    rep.post(f"/api/outreach/messages/{m['id']}/approve")
    rep.post(f"/api/outreach/messages/{m['id']}/send-now")
    after = manager.post("/api/outreach/mock/reply", json={"sequence_id": seq["id"], "body": "Please unsubscribe me."}).json()
    assert after["stop_reason"] == "opted_out" and after["contact_suppressed"]
    assert after["replies"][0]["opt_out"] is True
    with SessionLocal() as db:
        assert db.scalar(
            select(ConsentRecord).where(ConsentRecord.contact_id == contact["id"], ConsentRecord.status == "opted_out")
        )
    assert rep.get(f"/api/contacts/{contact['id']}").json()["suppressed"] is True
    again = rep.post(
        "/api/outreach/sequences", json={"lead_id": lead["id"], "contact_id": contact["id"], "mailbox_id": mailbox_id()}
    )
    assert again.status_code == 422 and "opted out" in again.json()["detail"]
    sup = rep.get("/api/outreach/suppressed").json()
    assert any(c["contact_id"] == contact["id"] for c in sup["consent_records"])


# ---------------------------------------------------------------------------
# Other stop rules and safeguards
# ---------------------------------------------------------------------------
def test_draft_only_mode_puts_email_in_mailbox_drafts(rep, adm):
    _, lead, contact = researched_prospect(rep)
    seq = start(rep, lead, contact)
    m = step(seq)
    rep.post(f"/api/outreach/messages/{m['id']}/approve")
    out = step(rep.post(f"/api/outreach/messages/{m['id']}/send-now").json())
    assert out["status"] == "drafted_in_mailbox"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(MockMail).where(MockMail.folder == "drafts")) == 1
        assert db.scalar(select(func.count()).select_from(MockMail).where(MockMail.folder == "sent")) == 0
    assert rep.get(f"/api/leads/{lead['id']}").json()["stage"] != "contacted"


def test_bounce_stops_sequence_and_marks_contact(rep, adm):
    _, lead, contact = researched_prospect(rep, email_local="bounce.test")
    enable_sending(adm)
    seq = start(rep, lead, contact)
    m = step(seq)
    rep.post(f"/api/outreach/messages/{m['id']}/approve")
    rep.post(f"/api/outreach/messages/{m['id']}/send-now")
    adm.post("/api/outreach/sync")
    d = rep.get(f"/api/outreach/sequences/{seq['id']}").json()
    assert step(d)["status"] == "bounced" and d["stop_reason"] == "bounced"
    assert rep.get(f"/api/contacts/{contact['id']}").json()["verification_status"] == "bounced"


def test_suppression_added_after_approval_blocks_send(rep, adm):
    _, lead, contact = researched_prospect(rep)
    enable_sending(adm)
    seq = start(rep, lead, contact)
    m = step(seq)
    rep.post(f"/api/outreach/messages/{m['id']}/approve")
    rep.post(f"/api/outreach/messages/{m['id']}/schedule", json={})
    rep.post("/api/consent", json={"domain": "meridian-college-commerce.example", "status": "suppressed"})
    with SessionLocal() as db:
        S.dispatch_due(db)
    d = rep.get(f"/api/outreach/sequences/{seq['id']}").json()
    assert d["stop_reason"] == "suppressed" and step(d)["status"] == "cancelled"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(MockMail)) == 0


def test_not_a_fit_stops_and_closes_lead(rep, adm):
    _, lead, contact = researched_prospect(rep)
    seq = start(rep, lead, contact)
    d = rep.post(f"/api/outreach/sequences/{seq['id']}/stop", json={"reason": "not_a_fit", "note": "Too small"}).json()
    assert d["stop_reason"] == "not_a_fit" and all(m["status"] == "cancelled" for m in d["messages"])
    lead_now = rep.get(f"/api/leads/{lead['id']}").json()
    assert lead_now["stage"] == "lost" and "Not a fit" in lead_now["lost_reason"]


def test_out_of_office_postpones_but_does_not_stop(rep, adm, manager):
    _, lead, contact = researched_prospect(rep)
    enable_sending(adm)
    seq = start(rep, lead, contact)
    m1, m2 = step(seq), step(seq, 2)
    rep.post(f"/api/outreach/messages/{m1['id']}/approve")
    rep.post(f"/api/outreach/messages/{m1['id']}/send-now")
    rep.post(f"/api/outreach/messages/{m2['id']}/approve")
    before = step(rep.post(f"/api/outreach/messages/{m2['id']}/schedule", json={}).json(), 2)["scheduled_for"]
    after = manager.post(
        "/api/outreach/mock/reply",
        json={"sequence_id": seq["id"], "body": "Automatic reply: I am out of the office until next week."},
    ).json()
    assert after["status"] == "active" and after["replies"][0]["label"] == "out_of_office"
    delta = datetime.fromisoformat(step(after, 2)["scheduled_for"]) - datetime.fromisoformat(before)
    assert delta == timedelta(days=7)


def test_sending_limits_and_window_defer(rep, adm):
    _, lead, contact = researched_prospect(rep)
    c2 = new_contact(
        rep,
        contact["institution_id"],
        first="Ben",
        last="Shah",
        email="ben.shah@meridian-college-commerce.example",
        designation="Head of Student Recruitment",
    )
    enable_sending(adm, per_domain=1)
    s1, s2 = start(rep, lead, contact), start(rep, lead, c2)
    for s in (s1, s2):
        rep.post(f"/api/outreach/messages/{step(s)['id']}/approve")
    assert step(rep.post(f"/api/outreach/messages/{step(s1)['id']}/send-now").json())["status"] == "sent"
    r = rep.post(f"/api/outreach/messages/{step(s2)['id']}/send-now")
    d = rep.get(f"/api/outreach/sequences/{s2['id']}").json()
    assert (
        r.status_code == 200
        and step(d)["status"] == "scheduled"
        and "Daily limit for meridian-college-commerce.example" in step(d)["last_error"]
    )
    # window: closed all day -> deferred to the next window
    p = adm.get("/api/outreach/policy").json()
    adm.put(
        "/api/outreach/policy",
        json={
            **p,
            "per_domain_daily_limit": 10,
            "send_window": {"start_hour": 0, "end_hour": 1, "timezone": "UTC", "weekdays_only": False},
        },
    )
    with SessionLocal() as db:
        msg = db.get(OutreachMessage, step(d)["id"])
        S.load_sequence(db, msg.sequence_id)
        status = S.dispatch_message(db, msg, now=datetime(2026, 10, 14, 12, 0, tzinfo=UTC))
        assert status == "scheduled" and msg.scheduled_for == datetime(2026, 10, 15, 0, 0, tzinfo=UTC)
        db.rollback()


def test_mailbox_rules(rep, adm, manager):
    _, lead, contact = researched_prospect(rep)
    # someone else's personal mailbox
    personal = manager.post(
        "/api/outreach/mailboxes/mock",
        json={
            "email_address": "mohan@plugdrop.example",
            "display_name": "Mohan",
            "is_shared": False,
            "authorization_note": "My own mailbox, I authorise its use",
            "confirm_authorized": True,
        },
    ).json()
    assert personal["status"] == "pending_approval"
    r = rep.post(
        "/api/outreach/sequences", json={"lead_id": lead["id"], "contact_id": contact["id"], "mailbox_id": personal["id"]}
    )
    assert r.status_code == 422 and "approve" in r.json()["detail"]
    assert rep.patch(f"/api/outreach/mailboxes/{personal['id']}", json={"status": "active"}).status_code == 403
    adm.patch(f"/api/outreach/mailboxes/{personal['id']}", json={"status": "active"})
    r = rep.post(
        "/api/outreach/sequences", json={"lead_id": lead["id"], "contact_id": contact["id"], "mailbox_id": personal["id"]}
    )
    assert r.status_code == 403 and "personal mailbox" in r.json()["detail"]
    # explicit authorization is mandatory
    assert (
        manager.post(
            "/api/outreach/mailboxes/mock",
            json={
                "email_address": "x@plugdrop.example",
                "display_name": "X",
                "authorization_note": "shared team inbox",
                "confirm_authorized": False,
            },
        ).status_code
        == 422
    )
    # OAuth providers aren't usable until configured
    assert (
        rep.get(
            "/api/outreach/oauth/gmail/start", params={"authorization_note": "Team inbox for sales", "confirm_authorized": True}
        ).status_code
        == 409
    )


def test_eligibility_requires_pushed_research(rep, adm):
    inst = new_institution(rep, name="Unresearched U", website="https://unresearched.example")
    lead = rep.post("/api/leads", json={"institution_id": inst["id"]}).json()
    c = new_contact(rep, inst["id"], email="a@unresearched.example")
    r = rep.post("/api/outreach/sequences", json={"lead_id": lead["id"], "contact_id": c["id"], "mailbox_id": mailbox_id()})
    assert r.status_code == 422 and "No approved research" in r.json()["detail"]


def test_policy_and_content_permissions(rep, manager, adm):
    p = rep.get("/api/outreach/policy").json()
    assert rep.put("/api/outreach/policy", json=p).status_code == 403
    assert manager.put("/api/outreach/policy", json=p).status_code == 403
    assert adm.put("/api/outreach/policy", json={**p, "max_attempts": 9}).status_code == 422
    assert adm.put("/api/outreach/policy", json={**p, "spacing_days": [0, 1, 7]}).status_code == 422
    item = manager.post("/api/outreach/content", json={"kind": "claim", "title": "New claim", "body": "We help with X."}).json()
    assert item["status"] == "draft"
    assert manager.post(f"/api/outreach/content/{item['id']}/status", params={"status": "approved"}).status_code == 403
    assert adm.post(f"/api/outreach/content/{item['id']}/status", params={"status": "approved"}).json()["status"] == "approved"
    edited = manager.put(
        f"/api/outreach/content/{item['id']}", json={"kind": "claim", "title": "New claim", "body": "Changed."}
    ).json()
    assert edited["status"] == "draft"  # edits need re-approval


def test_meeting_request_reply_draft_needs_edit_before_approval(rep, adm, manager):
    _, lead, contact = researched_prospect(rep)
    enable_sending(adm)
    seq = start(rep, lead, contact)
    m = step(seq)
    rep.post(f"/api/outreach/messages/{m['id']}/approve")
    rep.post(f"/api/outreach/messages/{m['id']}/send-now")
    after = manager.post(
        "/api/outreach/mock/reply", json={"sequence_id": seq["id"], "body": "Happy to talk — can we schedule a call on Thursday?"}
    ).json()
    reply = after["replies"][0]
    assert reply["label"] == "meeting_request"
    d = rep.post(f"/api/outreach/replies/{reply['id']}/draft").json()
    draft = step(d, 1, "reply")
    assert rep.post(f"/api/outreach/messages/{draft['id']}/approve").status_code == 422
    body = (
        draft["body"]
        .replace("[EDIT: time option 1]", "Thursday at 3pm IST")
        .replace("[EDIT: time option 2]", "Friday at 11am IST")
    )
    rep.patch(f"/api/outreach/messages/{draft['id']}", json={"subject": draft["subject"], "body": body})
    assert step(rep.post(f"/api/outreach/messages/{draft['id']}/approve").json(), 1, "reply")["status"] == "approved"
    sent = step(rep.post(f"/api/outreach/messages/{draft['id']}/send-now").json(), 1, "reply")
    assert sent["status"] == "sent"
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(Activity)
                .where(Activity.lead_id == lead["id"], Activity.activity_type == "reply")
            )
            == 1
        )
        db.execute(update(Mailbox).values(sending_enabled=False))
        db.commit()


def test_workspace_queues(rep, adm, manager):
    _, lead, contact = researched_prospect(rep)
    enable_sending(adm)
    seq = start(rep, lead, contact)
    assert rep.get("/api/outreach/messages", params={"view": "drafts"}).json()["total"] == 1
    m = step(seq)
    rep.post(f"/api/outreach/messages/{m['id']}/approve")
    assert rep.get("/api/outreach/messages", params={"view": "scheduled"}).json()["total"] == 1
    rep.post(f"/api/outreach/messages/{m['id']}/send-now")
    assert rep.get("/api/outreach/messages", params={"view": "sent"}).json()["total"] == 1
    # make the first email old enough that follow-up 2 is due
    with SessionLocal() as db:
        db.execute(
            update(OutreachMessage).where(OutreachMessage.id == m["id"]).values(sent_at=datetime.now(UTC) - timedelta(days=5))
        )
        db.commit()
    due = rep.get("/api/outreach/followups-due").json()
    assert len(due) == 1 and due[0]["message"]["step"] == 2
    ov = rep.get("/api/outreach/overview").json()
    assert ov["followups_due"] == 1 and ov["sending_mode"] == "send" and ov["external_sending_allowed"] is False
