"""Hardening tests added after adversarial testing of Stage 3: things a careless edit, a misbehaving writer,
real-world replies or two people clicking at once could otherwise get through."""

import threading
from unittest import mock

import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Contact, MockMail, OutreachMessage, User
from app.outreach import service as S
from app.outreach import writer as W
from app.outreach.classify import classify_rules
from tests.conftest import new_contact
from tests.test_outreach_flow import adm, admin_user, enable_sending, mailbox_id, researched_prospect, start, step  # noqa: F401
from tests.test_outreach_unit import CTA, FACT_I, FACT_V, GOOD, VP, check, ctx, failed

# ---------------------------------------------------------------------------
# quality gate: phrasing that previously slipped through
# ---------------------------------------------------------------------------
YEAR_FACT = W.Fact("F3", "intake_period", "verified_fact", "Admissions open for 2026-27", "Admissions Open 2026-27", "https://x.example", "t", None)  # fmt: skip


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda b: b.replace("Dear Asha,", "Dear Asha,\n\nHope you remember me from the conference."), "relationship"),
        (lambda b: b.replace("I saw that", "Following up on my previous email, I saw that"), "relationship"),
        (lambda b: b.replace("I saw that", "Your colleague suggested I write. I saw that"), "relationship"),
        (lambda b: b.replace("I saw that", "Thanks for your interest in PlugDrop. I saw that"), "relationship"),
        (lambda b: b.replace("I saw that", "As discussed, I saw that"), "relationship"),
        (lambda b: b.replace("I saw that", "Given our existing relationship, I saw that"), "relationship"),
        (lambda b: b + " Amity University now answers every call with PlugDrop.", "case_study"),
        (lambda b: b + " Universities like Chitkara already use us.", "case_study"),
        (lambda b: b + " Over 25 institutions use PlugDrop.", "case_study"),
        (lambda b: b + " Colleges see 7x more enrolments.", "numbers"),
        (lambda b: b + " Most see forty percent more conversions.", "numbers"),
        (lambda b: b + " It pays for itself within one term.", "numbers"),
        (lambda b: b + " We integrate natively with Meritto and NoPaperForms.", "integrations"),
        (lambda b: b + " It integrates with your ERP out of the box.", "integrations"),
        (lambda b: b + " Our agent speaks every Indian language fluently.", "claims"),
        (lambda b: b.replace("I saw that", "This game changing tool! I saw that"), "hype"),
        (lambda b: b.replace("I saw that", "This will revo​lutionize admissions. I saw that"), "hype"),
        (lambda b: b.replace("I saw that", "Trigger: VC biodata dated Nov-2025 (verify). I saw that"), "internal"),
        (lambda b: b + " PlugDrop positioning -> AI layer on top of their CRM.", "internal"),
    ],
)
def test_gate_blocks_evasions(change, expected):
    q = check(ctx(facts=[FACT_V, FACT_I, YEAR_FACT]), "Admissions enquiries at Meridian College", change(GOOD))
    assert not q["passed"] and expected in failed(q), failed(q)


def test_gate_still_passes_normal_emails_and_follow_up_wording():
    assert check(ctx(), "Admissions enquiries at Meridian College", GOOD)["passed"]
    follow = "Dear Asha,\n\nFollowing up on my previous note about enquiries at Meridian College.\n\n" + CTA.body
    assert check(ctx(step=2), "Re: Admissions enquiries at Meridian College", follow, facts=())["passed"]


def test_subject_must_be_one_line():
    q = check(ctx(), "Admissions at Meridian College\r\nBcc: someone@example.com", GOOD)
    assert "subject" in failed(q)


# ---------------------------------------------------------------------------
# writer: research notes are never pasted into an email
# ---------------------------------------------------------------------------
NOTE = W.Fact("F7", "other", "source_inference", "Trigger: Recruited VC, Registrar, Deans (Jun-2025) - leadership turnover", None, "https://u.example", "t", None)  # fmt: skip


def test_writer_leaves_opening_to_a_person_when_no_fact_has_approved_wording():
    c = ctx(facts=[NOTE], content=[VP, CTA])
    d = W.RuleWriter().write(c)
    assert "Trigger" not in d.body and "leadership turnover" not in d.body
    assert "[EDIT" in d.body and "F7" in d.rationale and d.facts_used == ["F7"]
    q = W.quality_check(c, d.subject, W.finalize(c, d.body), d.facts_used, d.content_used)
    assert not q["passed"] and "placeholders" in failed(q)
    written = d.body.replace(
        d.body[d.body.index("[EDIT") : d.body.index("]") + 1], "I noticed Meridian College has a new leadership team this year."
    )
    assert W.quality_check(c, d.subject, W.finalize(c, written), d.facts_used, d.content_used)["passed"]
    follow = W.RuleWriter().write(ctx(facts=[NOTE], content=[VP, CTA], step=2, previous=[{"subject": d.subject}]))
    assert "Trigger" not in follow.body and "[EDIT" not in follow.body


def test_writer_prefers_phrased_facts_and_names_the_source_site():
    d = W.RuleWriter().write(ctx(facts=[NOTE, FACT_V]))
    assert d.facts_used == ["F1"] and "I saw on jobs.example that you're hiring admissions counsellors." in d.body


# ---------------------------------------------------------------------------
# reply classification on real-world replies
# ---------------------------------------------------------------------------
QUOTED = (
    "\n\nOn Tue, 13 Oct 2026 at 17:02, PlugDrop Sales <sales@plugdrop.ai> wrote:\n> Dear Dr. Sharma,\n>\n"
    "> If you'd rather not hear from me, reply \"unsubscribe\" and I won't email you again."
)
OUTLOOK = (
    "\n\n________________________________\nFrom: PlugDrop Sales <sales@plugdrop.ai>\nSent: Tuesday\n\n"
    "If you'd rather not hear from me, reply \"unsubscribe\" and I won't email you again."
)


@pytest.mark.parametrize(
    ("text", "label", "opt_out"),
    [
        ("Sounds interesting, tell me more." + QUOTED, "interested", False),
        ("Please send pricing." + OUTLOOK, "needs_information", False),
        ("Can we talk on Friday?" + QUOTED, "meeting_request", False),
        ("Kindly share the details and commercials.", "needs_information", False),
        ("Ok. Please call me tomorrow.", "meeting_request", False),
        ("Not required.", "not_interested", False),
        ("We are already using LeadSquared, thanks.", "not_interested", False),
        ("Not at present. Kindly connect after admissions close in December.", "not_now", False),
        ("I am out of station till 20th October with limited access to mail.", "out_of_office", False),
        ("Your query has been received and our team will get back to you within 48 hours.", "out_of_office", False),
        ("Please don't send such mails.", "not_interested", True),
        ("Kindly remove my email id from your mailing list.", "not_interested", True),
        ("Mujhe mail mat bhejiye.", "not_interested", True),
        ("Do not spam me.", "not_interested", True),
    ],
)
def test_real_world_replies(text, label, opt_out):
    c = classify_rules("Re: Admissions enquiries at X University", text)
    assert (c.label, c.opt_out) == (label, opt_out)


def test_quoted_unsubscribe_line_never_suppresses_an_interested_prospect(rep, adm, manager):
    _, lead, contact = researched_prospect(rep)
    enable_sending(adm)
    seq = start(rep, lead, contact)
    m = step(seq)
    rep.post(f"/api/outreach/messages/{m['id']}/approve")
    rep.post(f"/api/outreach/messages/{m['id']}/send-now")
    d = manager.post("/api/outreach/mock/reply", json={"sequence_id": seq["id"], "body": "Yes, interested." + QUOTED}).json()
    assert d["replies"][0]["label"] == "interested" and not d["replies"][0]["opt_out"]
    assert d["stop_reason"] == "replied" and not d["contact_suppressed"]


def test_automatic_acknowledgement_pauses_instead_of_stopping(rep, adm, manager):
    _, lead, contact = researched_prospect(rep)
    enable_sending(adm)
    seq = start(rep, lead, contact)
    m = step(seq)
    rep.post(f"/api/outreach/messages/{m['id']}/approve")
    rep.post(f"/api/outreach/messages/{m['id']}/send-now")
    body = "Thank you for writing to us. We have received your email and will respond within 2 working days. This is an auto-generated mail."
    d = manager.post("/api/outreach/mock/reply", json={"sequence_id": seq["id"], "body": body}).json()
    assert d["status"] == "active" and d["replies"][0]["label"] == "out_of_office"


# ---------------------------------------------------------------------------
# sending
# ---------------------------------------------------------------------------
def test_simultaneous_send_clicks_send_exactly_once(rep, adm, admin_user):
    _, lead, contact = researched_prospect(rep)
    enable_sending(adm)
    seq = start(rep, lead, contact)
    m = step(seq)
    rep.post(f"/api/outreach/messages/{m['id']}/approve")
    results: list[str] = []

    def click() -> None:
        with SessionLocal() as db:
            try:
                S.send_now(db, db.get(OutreachMessage, m["id"]), db.get(User, admin_user.id))
                results.append("sent")
            except Exception as e:  # noqa: BLE001
                results.append(type(e).__name__)

    def worker() -> None:
        with SessionLocal() as db:
            S.dispatch_due(db)

    threads = [threading.Thread(target=click) for _ in range(4)] + [threading.Thread(target=worker)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(MockMail).where(MockMail.folder == "sent")) == 1
        assert db.get(OutreachMessage, m["id"]).status == "sent"


def test_unexpected_provider_error_marks_failed_not_stuck(rep, adm):
    _, lead, contact = researched_prospect(rep)
    enable_sending(adm)
    seq = start(rep, lead, contact)
    m = step(seq)
    rep.post(f"/api/outreach/messages/{m['id']}/approve")
    with mock.patch("app.outreach.providers.mock.MockEmailProvider.send", side_effect=ValueError("boom")):
        rep.post(f"/api/outreach/messages/{m['id']}/send-now")
    got = step(rep.get(f"/api/outreach/sequences/{seq['id']}").json())
    assert got["status"] == "failed" and "boom" in got["last_error"]


def test_draft_only_follow_up_waits_until_someone_confirms_the_send(rep, adm):
    _, lead, contact = researched_prospect(rep)
    seq = start(rep, lead, contact)  # policy is draft-only by default
    m1, m2 = step(seq), step(seq, 2)
    rep.post(f"/api/outreach/messages/{m1['id']}/approve")
    assert step(rep.post(f"/api/outreach/messages/{m1['id']}/send-now").json())["status"] == "drafted_in_mailbox"
    rep.post(f"/api/outreach/messages/{m2['id']}/approve")
    d = rep.post(f"/api/outreach/messages/{m2['id']}/schedule", json={}).json()
    assert step(d, 2)["status"] == "scheduled" and step(d, 2)["scheduled_for"] is None  # waits; not dated yet
    with SessionLocal() as db:
        S.dispatch_due(db)
        assert db.scalar(select(func.count()).select_from(MockMail).where(MockMail.folder == "drafts")) == 1
    d = rep.post(f"/api/outreach/messages/{m1['id']}/mark-sent").json()
    assert step(d)["status"] == "sent" and step(d, 2)["scheduled_for"] is not None
    assert rep.get(f"/api/leads/{lead['id']}").json()["stage"] == "contacted"
    assert rep.post(f"/api/outreach/messages/{m1['id']}/mark-sent").status_code == 409


def test_one_live_sequence_per_institution_by_default(rep, adm):
    inst, lead, contact = researched_prospect(rep)
    start(rep, lead, contact)
    c2 = new_contact(rep, inst["id"], first="Ben", last="Shah", email="ben.shah@meridian-college-commerce.example")
    r = rep.post("/api/outreach/sequences", json={"lead_id": lead["id"], "contact_id": c2["id"], "mailbox_id": mailbox_id()})
    assert r.status_code == 422 and "already have outreach in progress" in r.json()["detail"]


def test_role_mailbox_greeting_and_one_line_subject(rep, adm):
    inst, lead, _ = researched_prospect(rep)
    office = new_contact(
        rep, inst["id"], first="Admissions Office", last="", email="admissions@meridian-college-commerce.example"
    )
    with SessionLocal() as db:
        assert db.get(Contact, office["id"]).greeting_name == "Sir/Madam"
    seq = start(rep, lead, office)
    assert step(seq)["body"].startswith("Dear Sir/Madam,")
    r = rep.patch(
        f"/api/outreach/messages/{step(seq)['id']}", json={"subject": "Hi\r\nBcc: x@example.com", "body": step(seq)["body"]}
    )
    assert r.status_code == 422
