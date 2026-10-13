"""Unit tests: writer + quality gate, reply classifier, send window, Gmail/Graph providers, token crypto."""

import base64
import json
from datetime import UTC, datetime, timedelta
from email import message_from_bytes
from types import SimpleNamespace

import httpx
import pytest

from app.config import Settings
from app.outreach import writer as W
from app.outreach.classify import classify_rules
from app.outreach.crypto import decrypt_tokens, encrypt_tokens
from app.outreach.policy import DEFAULT_POLICY
from app.outreach.providers import oauth_mail
from app.outreach.providers.base import EmailProviderError, OutgoingEmail
from app.outreach.service import effective_mode, in_window, next_window

FACT_V = W.Fact(
    "F1",
    "hiring_admissions",
    "verified_fact",
    "Hiring admissions counsellors",
    "We are hiring 7 Admissions Counsellors to respond to a high volume of phone and WhatsApp enquiries.",
    "https://jobs.example/u/1",
    "Job posting",
    "2026-09-01",
)
FACT_I = W.Fact(
    "F2",
    "response_delays",
    "source_inference",
    "Applicants report slow replies",
    "Students reported waiting several days for replies.",
    "https://news.example/u/2",
    "Forum",
    "2026-06-01",
)
VP = W.Content(
    "C1",
    "value_prop",
    "Voice AI",
    "PlugDrop's Voice AI agent can answer admissions enquiry calls and record each enquiry in your CRM.",
    ["voice_enquiry_automation"],
)
CTA = W.Content("C2", "cta", "Call", "Would a 20-minute call next week be useful?", [])
CTA2 = W.Content("C3", "cta", "Later", "Is there a better time for me to get back in touch?", [])


def ctx(**kw):
    base = dict(
        institution="Meridian College",
        first_name="Asha",
        contact_name="Asha Rao",
        designation="Director of Admissions",
        sender_name="Riya Rep",
        use_case="voice_enquiry_automation",
        facts=[FACT_V, FACT_I],
        content=[VP, CTA, CTA2],
        policy={**DEFAULT_POLICY},
    )
    base.update(kw)
    return W.Context(**base)


def check(c, subject, body, facts=("F1",), content=("C1", "C2")):
    return W.quality_check(c, subject, W.finalize(c, body), list(facts), list(content))


def failed(q):
    return {x["id"] for x in q["checks"] if not x["passed"]}


GOOD = "Dear Asha,\n\nI saw that you're hiring admissions counsellors (jobs.example).\n\n" + VP.body + "\n\n" + CTA.body


# ---------------- writer ----------------
def test_rule_writer_first_email_passes_quality_gate():
    c = ctx()
    d = W.RuleWriter().write(c)
    assert d.facts_used == ["F1"] and set(d.content_used) == {"C1", "C2"}
    assert "Meridian College" in d.subject
    q = W.quality_check(c, d.subject, W.finalize(c, d.body), d.facts_used, d.content_used)
    assert q["passed"], q
    full = W.finalize(c, d.body)
    assert full.endswith(DEFAULT_POLICY["opt_out_line"]) and "Riya Rep" in full


def test_rule_writer_inference_is_qualified_and_follow_ups_are_shorter():
    c = ctx(facts=[FACT_I])
    d = W.RuleWriter().write(c)
    assert "looks like" in d.body and "correct me" in d.body
    c2 = ctx(step=2, previous=[{"step": "1", "subject": "Admissions enquiries at Meridian College", "body": "x"}])
    f = W.RuleWriter().write(c2)
    assert f.subject.startswith("Re: Admissions enquiries")
    assert W.quality_check(c2, f.subject, W.finalize(c2, f.body), f.facts_used, f.content_used)["passed"]
    c3 = ctx(step=3, previous=[{"step": "1", "subject": "S", "body": "x"}])
    assert "last note" in W.RuleWriter().write(c3).body


def test_writer_refuses_without_facts_or_approved_content():
    with pytest.raises(W.WriterError, match="No approved research facts"):
        W.RuleWriter().write(ctx(facts=[]))
    with pytest.raises(W.WriterError, match="content library"):
        W.RuleWriter().write(ctx(content=[CTA]))


def test_reply_drafts_need_editing_before_approval():
    c = ctx(kind="reply", reply={"label": "meeting_request", "subject": "Re: X", "body": "Can we talk Tuesday?"})
    d = W.RuleWriter().write(c)
    q = W.quality_check(c, d.subject, W.finalize(c, d.body), d.facts_used, d.content_used)
    assert not q["passed"] and "placeholders" in failed(q)
    with pytest.raises(W.WriterError):
        W.RuleWriter().write(ctx(kind="reply", reply={"label": "not_interested", "subject": "", "body": ""}))


# ---------------- quality gate ----------------
def test_quality_gate_good_email_passes():
    assert check(ctx(), "Admissions enquiries at Meridian College", GOOD)["passed"]


@pytest.mark.parametrize(
    ("body_change", "expected"),
    [
        (lambda b: b.replace("I saw that", "This will revolutionize how"), "hype"),
        (lambda b: b.replace("Dear Asha,", "Dear Asha, great speaking with you last week."), "relationship"),
        (lambda b: b + " Most colleges cut costs by 40% in 3 months?", "numbers"),
        (lambda b: b + " We integrate with Salesforce out of the box.", "integrations"),
        (lambda b: b + " Our customers like Riverside University saw great results.", "case_study"),
        (lambda b: b.replace("?", "."), "cta"),
        (lambda b: b + " [EDIT: add times]", "placeholders"),
        (lambda b: b + " word" * 150, "length"),
    ],
)
def test_quality_gate_blocks_policy_violations(body_change, expected):
    q = check(ctx(), "Admissions enquiries at Meridian College", body_change(GOOD))
    assert not q["passed"] and expected in failed(q)


def test_numbers_from_facts_are_allowed_and_opt_out_cannot_be_removed():
    body = GOOD.replace("hiring admissions counsellors", "hiring 7 admissions counsellors")
    assert "numbers" not in failed(check(ctx(), "Admissions at Meridian College", body))
    c = ctx()
    q = W.quality_check(c, "Admissions at Meridian College", GOOD + "\n\n" + W.signature(c), ["F1"], ["C1", "C2"])
    assert "opt_out" in failed(q)


def test_restricted_term_allowed_when_in_verified_fact():
    f = W.Fact(
        "F9",
        "crm_initiative",
        "verified_fact",
        "Moving to HubSpot",
        "We are migrating admissions to HubSpot.",
        "https://u.example",
        "t",
        None,
    )
    q = check(
        ctx(facts=[f]), "Admissions at Meridian College", GOOD + " I understand the HubSpot move is under way.", facts=("F9",)
    )
    assert "integrations" not in failed(q)


def test_inference_without_hedge_is_a_warning_not_a_block():
    body = "Dear Asha,\n\nApplicants wait days for replies at Meridian College.\n\n" + VP.body + "\n\n" + CTA.body
    q = check(ctx(), "Admissions at Meridian College", body, facts=("F2",))
    assert q["passed"] and "qualified" in failed(q)


# ---------------- reply classification ----------------
@pytest.mark.parametrize(
    ("text", "label", "opt_out"),
    [
        ("Thanks, this sounds interesting — tell me more.", "interested", False),
        ("Can we schedule a call on Thursday afternoon?", "meeting_request", False),
        ("Could you send more information and pricing?", "needs_information", False),
        ("Not right now, please check back next quarter after our intake.", "not_now", False),
        ("We're not interested, thanks.", "not_interested", False),
        ("I'm not the right person — please contact my colleague Priya in admissions.", "referral", False),
        ("Automatic reply: I am out of the office until Monday.", "out_of_office", False),
        ("Hmm.", "unclear", False),
        ("Please unsubscribe me from these emails.", "not_interested", True),
        ("Remove me from your list", "not_interested", True),
    ],
)
def test_rule_classifier(text, label, opt_out):
    c = classify_rules("Re: Admissions", text)
    assert (c.label, c.opt_out) == (label, opt_out)


# ---------------- send window ----------------
def test_send_window():
    p = {**DEFAULT_POLICY, "send_window": {"start_hour": 9, "end_hour": 17, "timezone": "Asia/Kolkata", "weekdays_only": True}}
    sat_noon_ist = datetime(2026, 10, 17, 6, 30, tzinfo=UTC)  # Saturday 12:00 IST
    assert not in_window(sat_noon_ist, p)
    nxt = next_window(sat_noon_ist, p)
    assert nxt == datetime(2026, 10, 19, 3, 30, tzinfo=UTC)  # Monday 09:00 IST
    assert in_window(datetime(2026, 10, 19, 5, 0, tzinfo=UTC), p)


def test_effective_mode_requires_policy_mailbox_and_hard_switch():
    s = Settings()
    mb = SimpleNamespace(provider="gmail", sending_enabled=True)
    assert effective_mode(mb, {**DEFAULT_POLICY}, s)[0] == "draft"  # policy draft_only
    pol = {**DEFAULT_POLICY, "sending_mode": "send"}
    assert effective_mode(mb, pol, s) == ("draft", "External sending is switched off (OUTREACH_EXTERNAL_SENDING_ALLOWED=false)")
    assert effective_mode(mb, pol, Settings(outreach_external_sending_allowed=True))[0] == "send"
    assert effective_mode(SimpleNamespace(provider="mock", sending_enabled=False), pol, s)[0] == "draft"
    assert effective_mode(SimpleNamespace(provider="mock", sending_enabled=True), pol, s)[0] == "send"


# ---------------- providers ----------------
def _mailbox(provider, expired=False):
    exp = datetime.now(UTC) + (timedelta(minutes=-5) if expired else timedelta(hours=1))
    return SimpleNamespace(
        provider=provider,
        email_address="sales@plugdrop.example",
        token_expires_at=exp,
        token_ciphertext=encrypt_tokens({"access_token": "AT", "refresh_token": "RT", "expires_at": exp.isoformat()}),
    )


def _settings():
    return Settings(
        google_client_id="gid",
        google_client_secret="gs",
        microsoft_client_id="mid",
        microsoft_client_secret="ms",
        token_encryption_key="1Hn7wR1m0o3Zp4vB0yE5V9cXo6Lw5pQe8m3Jf2KdA7s=",
    )


EMAIL = OutgoingEmail(
    from_email="sales@plugdrop.example",
    from_name="Riya",
    to_email="asha@meridian.example",
    subject="Hello",
    body="Dear Asha,\n\nTest?",
    idempotency_key="seq1-sequence-1",
)


def test_gmail_send_refresh_and_sync():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path))
        if req.url.path == "/token":
            assert b"refresh_token=RT" in req.content
            return httpx.Response(200, json={"access_token": "AT2", "expires_in": 3600})
        assert req.headers["authorization"] == "Bearer AT2"
        if req.url.path.endswith("/messages/send"):
            raw = base64.urlsafe_b64decode(json.loads(req.content)["raw"])
            m = message_from_bytes(raw)
            assert m["To"] == "asha@meridian.example" and "unsubscribe" in m["List-Unsubscribe"]
            return httpx.Response(200, json={"id": "g1", "threadId": "t1"})
        if req.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "in1"}, {"id": "bn1"}]})
        if req.url.path.endswith("/in1"):
            raw = b"From: Asha <asha@meridian.example>\r\nSubject: Re: Hello\r\nIn-Reply-To: <x@y>\r\n\r\nSounds interesting."
            return httpx.Response(
                200,
                json={
                    "id": "in1",
                    "threadId": "t1",
                    "internalDate": "1790000000000",
                    "raw": base64.urlsafe_b64encode(raw).decode(),
                },
            )
        raw = b"From: Mail Delivery Subsystem <mailer-daemon@googlemail.com>\r\nSubject: Delivery Status Notification (Failure)\r\nX-Failed-Recipients: gone@meridian.example\r\n\r\nAddress not found."
        return httpx.Response(
            200,
            json={"id": "bn1", "threadId": "t9", "internalDate": "1790000001000", "raw": base64.urlsafe_b64encode(raw).decode()},
        )

    mb = _mailbox("gmail", expired=True)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    p = oauth_mail.GmailProvider(None, mb, client, _settings())
    r = p.send(EMAIL)
    assert (r.provider_message_id, r.thread_id) == ("g1", "t1")
    assert (
        decrypt_tokens(mb.token_ciphertext)["access_token"] == "AT2"
        and decrypt_tokens(mb.token_ciphertext)["refresh_token"] == "RT"
    )
    s = p.sync(None)
    assert s.inbound[0].from_email == "asha@meridian.example" and s.inbound[0].body.strip() == "Sounds interesting."
    assert s.delivery[0].status == "bounced" and s.delivery[0].to_email == "gone@meridian.example"
    assert int(s.cursor) >= 1790000001  # cursor never moves backwards


def test_graph_draft_send_sync_and_reauth():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST" and req.url.path == "/v1.0/me/messages":
            body = json.loads(req.content)
            assert body["body"]["contentType"] == "Text"
            return httpx.Response(201, json={"id": "m1", "conversationId": "c1", "internetMessageId": "<m1@x>"})
        if req.url.path.endswith("/send"):
            return httpx.Response(202)
        if "inbox" in req.url.path:
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "r1",
                            "subject": "Re: Hello",
                            "from": {"emailAddress": {"address": "asha@meridian.example"}},
                            "receivedDateTime": "2026-10-13T10:00:00Z",
                            "conversationId": "c1",
                            "body": {"contentType": "html", "content": "<p>Not now</p>"},
                            "internetMessageHeaders": [{"name": "In-Reply-To", "value": "<m1@x>"}],
                        },
                        {
                            "id": "r2",
                            "subject": "Undeliverable: Hello",
                            "from": {"emailAddress": {"address": "postmaster@meridian.example"}},
                            "receivedDateTime": "2026-10-13T10:05:00Z",
                            "conversationId": "c2",
                            "body": {"contentType": "text", "content": "gone@meridian.example"},
                        },
                    ]
                },
            )
        return httpx.Response(404)

    p = oauth_mail.MicrosoftGraphProvider(
        None, _mailbox("microsoft"), httpx.Client(transport=httpx.MockTransport(handler)), _settings()
    )
    assert p.create_draft(EMAIL).thread_id == "c1"
    assert p.send(EMAIL).provider_message_id == "m1"
    s = p.sync(None)
    assert s.inbound[0].in_reply_to == "<m1@x>" and "Not now" in s.inbound[0].body
    assert s.delivery[0].status == "bounced" and s.cursor == "2026-10-13T10:05:00Z"
    bad = oauth_mail.MicrosoftGraphProvider(
        None, _mailbox("microsoft"), httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401))), _settings()
    )
    with pytest.raises(EmailProviderError) as e:
        bad.send(EMAIL)
    assert e.value.needs_reauth and not e.value.transient


def test_oauth_urls_and_least_privilege_scopes():
    s = _settings()
    assert "Mail.Send" not in oauth_mail.scopes_for("microsoft", "draft_only")
    assert "Mail.Send" in oauth_mail.scopes_for("microsoft", "send")
    url = oauth_mail.authorization_url("microsoft", "STATE", "draft_only", s)
    assert "Mail.ReadWrite" in url and "Mail.Send" not in url and "state=STATE" in url and "/organizations/" in url
    g = oauth_mail.authorization_url("gmail", "S2", "draft_only", s)
    assert "gmail.compose" in g and "access_type=offline" in g and "password" not in g.lower()


def test_exchange_code():
    def handler(req):
        if req.url.path.endswith("/token"):
            assert b"grant_type=authorization_code" in req.content and b"code=abc" in req.content
            return httpx.Response(200, json={"access_token": "A", "refresh_token": "R", "expires_in": 3600})
        return httpx.Response(200, json={"mail": "sales@plugdrop.example", "displayName": "Sales"})

    t, email, name = oauth_mail.exchange_code(
        "microsoft", "abc", httpx.Client(transport=httpx.MockTransport(handler)), _settings()
    )
    assert email == "sales@plugdrop.example" and t["refresh_token"] == "R" and "expires_at" in t


def test_token_encryption_roundtrip():
    c = encrypt_tokens({"access_token": "secret"})
    assert "secret" not in c and decrypt_tokens(c)["access_token"] == "secret"
