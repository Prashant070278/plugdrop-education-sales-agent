from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app import audit
from app.config import get_settings
from app.deps import DB, AdminUser, CurrentUser, ManagerUser
from app.listing import ListParams, Page, paginate
from app.models import (
    CLOSED_STAGES,
    ConsentRecord,
    Contact,
    ContentItem,
    EmailEvent,
    EmailReply,
    EmailTemplate,
    Institution,
    Lead,
    Mailbox,
    OutreachMessage,
    OutreachSequence,
)
from app.outreach import service as S
from app.outreach.crypto import TokenKeyMissing, encrypt_tokens
from app.outreach.policy import load_policy, save_policy
from app.outreach.providers import oauth_mail
from app.outreach.providers.mock import simulate_inbound
from app.research.reasoning import USE_CASES
from app.schemas import UserRef
from app.services import get_or_404, suppressed_contact_ids

router = APIRouter(prefix="/outreach", tags=["outreach agent"])


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MailboxOut(ORM):
    id: int
    provider: str
    email_address: str
    display_name: str
    is_shared: bool
    owner: UserRef | None
    authorization_note: str
    status: str
    sending_enabled: bool
    daily_send_limit: int
    scopes: list[str]
    approved_at: datetime | None
    last_sync_at: datetime | None
    last_error: str | None
    effective_mode: str = "draft"
    effective_mode_reason: str = ""


class MessageOut(ORM):
    id: int
    sequence_id: int
    step: int
    kind: str
    status: str
    to_email: str
    subject: str
    body: str
    generated_by: str
    generation: dict[str, Any]
    quality: dict[str, Any]
    edited: bool
    planned_offset_days: int
    approved_at: datetime | None
    scheduled_for: datetime | None
    sent_at: datetime | None
    delivered_at: datetime | None
    provider: str | None
    provider_message_id: str | None
    last_error: str | None
    updated_at: datetime


class ReplyOut(ORM):
    id: int
    sequence_id: int | None
    message_id: int | None
    from_email: str
    subject: str
    body: str
    received_at: datetime
    label: str
    label_confidence: int
    label_source: str
    opt_out: bool
    summary: str | None
    handled: bool
    handled_at: datetime | None


class EventOut(ORM):
    id: int
    message_id: int | None
    event: str
    actor: UserRef | None
    data: dict[str, Any]
    created_at: datetime


class SeqRef(ORM):
    id: int
    name: str


class SequenceOut(ORM):
    id: int
    lead_id: int
    institution: SeqRef
    contact_id: int
    contact_name: str = ""
    contact_email: str | None = None
    contact_designation: str | None = None
    mailbox_id: int | None
    use_case: str | None
    use_case_label: str | None = None
    status: str
    stop_reason: str | None
    stopped_at: datetime | None
    max_attempts: int
    spacing_days: list[int]
    is_demo: bool
    created_at: datetime


class SequenceDetail(SequenceOut):
    facts: list[dict[str, Any]]
    messages: list[MessageOut]
    replies: list[ReplyOut]
    events: list[EventOut] = []
    mailbox: MailboxOut | None = None
    contact_suppressed: bool = False


class WorkItem(BaseModel):
    message: MessageOut
    sequence: SequenceOut
    due_at: datetime | None = None


class ReplyItem(BaseModel):
    reply: ReplyOut
    sequence: SequenceOut | None


def _seq_out(seq: OutreachSequence) -> SequenceOut:
    o = SequenceOut.model_validate(seq)
    o.institution = SeqRef(id=seq.institution.id, name=seq.institution.name)
    o.contact_name, o.contact_email, o.contact_designation = seq.contact.full_name, seq.contact.email, seq.contact.designation
    o.use_case_label = USE_CASES.get(seq.use_case or "")
    return o


def _mailbox_out(db: DB, mb: Mailbox) -> MailboxOut:
    o = MailboxOut.model_validate(mb)
    o.effective_mode, o.effective_mode_reason = S.effective_mode(mb, load_policy(db), get_settings())
    if mb.status != "active":
        o.effective_mode, o.effective_mode_reason = "none", f"Mailbox {mb.status.replace('_', ' ')}"
    return o


def _detail(db: DB, seq: OutreachSequence) -> SequenceDetail:
    d = SequenceDetail(
        **_seq_out(seq).model_dump(),
        facts=seq.facts,
        messages=[MessageOut.model_validate(m) for m in seq.messages],
        replies=[ReplyOut.model_validate(r) for r in seq.replies],
    )
    d.events = [
        EventOut.model_validate(e)
        for e in db.scalars(
            select(EmailEvent)
            .options(selectinload(EmailEvent.actor))
            .where(EmailEvent.sequence_id == seq.id)
            .order_by(EmailEvent.id.desc())
            .limit(200)
        )
    ]
    d.mailbox = _mailbox_out(db, seq.mailbox) if seq.mailbox else None
    d.contact_suppressed = seq.contact.id in suppressed_contact_ids(db, [seq.contact])
    return d


def _msg(db: DB, message_id: int) -> OutreachMessage:
    m = get_or_404(db, OutreachMessage, message_id, "Email")
    S.load_sequence(db, m.sequence_id)  # warm relationships
    return m


# ---------------------------------------------------------------------------
# overview and work queues
# ---------------------------------------------------------------------------
@router.get("/overview")
def overview(_: CurrentUser, db: DB) -> dict[str, Any]:
    def count(*conds: Any) -> int:
        return db.scalar(select(func.count()).select_from(OutreachMessage).join(OutreachSequence).where(*conds)) or 0

    policy = load_policy(db)
    s = get_settings()
    return {
        "drafts": count(
            OutreachMessage.status == "draft", or_(OutreachSequence.status.in_(S.LIVE), OutreachMessage.kind == "reply")
        ),
        "scheduled": count(OutreachMessage.status.in_(["approved", "scheduled"])),
        "sent_7d": count(
            OutreachMessage.status.in_(["sent", "delivered", "bounced", "drafted_in_mailbox"]),
            OutreachMessage.sent_at >= datetime.now(UTC) - timedelta(days=7),
        ),
        "blocked": count(OutreachMessage.status.in_(["blocked", "failed"])),
        "replies_attention": db.scalar(select(func.count()).select_from(EmailReply).where(EmailReply.handled.is_(False))) or 0,
        "followups_due": len(_followups_due(db)),
        "live_sequences": db.scalar(select(func.count()).select_from(OutreachSequence).where(OutreachSequence.status.in_(S.LIVE)))
        or 0,
        "sending_mode": policy["sending_mode"],
        "external_sending_allowed": s.outreach_external_sending_allowed,
        "writer": s.outreach_writer if s.outreach_writer != "auto" else ("claude" if s.anthropic_api_key else "rules"),
        "mailboxes_active": db.scalar(select(func.count()).select_from(Mailbox).where(Mailbox.status == "active")) or 0,
    }


def _messages_query(view: str) -> Any:
    q = (
        select(OutreachMessage)
        .join(OutreachSequence)
        .options(
            selectinload(OutreachMessage.sequence).selectinload(OutreachSequence.institution),
            selectinload(OutreachMessage.sequence).selectinload(OutreachSequence.contact),
        )
    )
    if view == "drafts":
        return q.where(
            OutreachMessage.status == "draft",
            or_(OutreachSequence.status.in_(S.LIVE), OutreachMessage.kind == "reply"),
            or_(OutreachMessage.step == 1, OutreachMessage.kind == "reply"),
        ).order_by(OutreachMessage.updated_at.desc())
    if view == "scheduled":
        return q.where(OutreachMessage.status.in_(["approved", "scheduled", "blocked"])).order_by(
            OutreachMessage.scheduled_for.asc().nullslast()
        )
    if view == "sent":
        return q.where(
            OutreachMessage.status.in_(["sending", "sent", "delivered", "bounced", "failed", "drafted_in_mailbox"])
        ).order_by(OutreachMessage.sent_at.desc().nullslast(), OutreachMessage.id.desc())
    raise HTTPException(422, "view must be drafts, scheduled or sent")


@router.get("/messages", response_model=Page[WorkItem])
def list_messages(
    _: CurrentUser, db: DB, params: Annotated[ListParams, Depends()], view: Literal["drafts", "scheduled", "sent"] = "drafts"
) -> Page[WorkItem]:
    q = _messages_query(view)
    if params.q:
        q = q.join(Institution, OutreachSequence.institution_id == Institution.id).where(
            or_(Institution.name.ilike(f"%{params.q}%"), OutreachMessage.to_email.ilike(f"%{params.q}%"))
        )
    items, total = paginate(db, q, params)
    return Page(
        items=[
            WorkItem(message=MessageOut.model_validate(m), sequence=_seq_out(m.sequence), due_at=m.scheduled_for) for m in items
        ],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


def _followups_due(db: DB) -> list[tuple[OutreachMessage, datetime]]:
    """Follow-ups whose previous email went out and that still need approval (or are due within a day)."""
    rows = db.scalars(
        select(OutreachMessage)
        .join(OutreachSequence)
        .where(
            OutreachMessage.kind == "sequence",
            OutreachMessage.step > 1,
            OutreachMessage.status.in_(["draft", "approved"]),
            OutreachSequence.status.in_(S.LIVE),
        )
        .options(selectinload(OutreachMessage.sequence).selectinload(OutreachSequence.messages))
    ).all()
    out = []
    horizon = datetime.now(UTC) + timedelta(days=1)
    for m in rows:
        prev = next((x for x in m.sequence.messages if x.kind == "sequence" and x.step == m.step - 1), None)
        if prev and prev.sent_at and prev.status in S.SENT_LIKE:
            due = prev.sent_at + timedelta(days=m.sequence.spacing_days[m.step - 1])
            if due <= horizon:
                out.append((m, due))
    return sorted(out, key=lambda x: x[1])


@router.get("/followups-due", response_model=list[WorkItem])
def followups_due(_: CurrentUser, db: DB) -> list[WorkItem]:
    return [
        WorkItem(message=MessageOut.model_validate(m), sequence=_seq_out(S.load_sequence(db, m.sequence_id)), due_at=due)
        for m, due in _followups_due(db)
    ]


@router.get("/replies", response_model=Page[ReplyItem])
def list_replies(
    _: CurrentUser, db: DB, params: Annotated[ListParams, Depends()], attention: bool = True, label: str | None = None
) -> Page[ReplyItem]:
    q = (
        select(EmailReply)
        .options(
            selectinload(EmailReply.sequence).selectinload(OutreachSequence.institution),
            selectinload(EmailReply.sequence).selectinload(OutreachSequence.contact),
        )
        .order_by(EmailReply.received_at.desc())
    )
    if attention:
        q = q.where(EmailReply.handled.is_(False))
    if label:
        q = q.where(EmailReply.label == label)
    items, total = paginate(db, q, params)
    return Page(
        items=[ReplyItem(reply=ReplyOut.model_validate(r), sequence=_seq_out(r.sequence) if r.sequence else None) for r in items],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


@router.get("/suppressed")
def suppressed(_: CurrentUser, db: DB) -> dict[str, Any]:
    consents = db.scalars(
        select(ConsentRecord)
        .where(ConsentRecord.status.in_(["opted_out", "suppressed"]))
        .order_by(ConsentRecord.recorded_at.desc())
        .limit(200)
    ).all()
    bounced = db.scalars(
        select(Contact)
        .options(selectinload(Contact.institution))
        .where(Contact.verification_status.in_(["bounced", "invalid"]))
        .limit(200)
    ).all()
    stopped = db.scalars(
        select(OutreachSequence)
        .options(selectinload(OutreachSequence.institution), selectinload(OutreachSequence.contact))
        .where(OutreachSequence.stop_reason.in_(["opted_out", "suppressed", "bounced", "not_a_fit"]))
        .order_by(OutreachSequence.stopped_at.desc())
        .limit(200)
    ).all()
    return {
        "consent_records": [
            {
                "id": c.id,
                "contact_id": c.contact_id,
                "email": c.email,
                "domain": c.domain,
                "status": c.status,
                "channel": c.channel,
                "reason": c.reason,
                "source": c.source,
                "recorded_at": c.recorded_at,
            }
            for c in consents
        ],
        "bounced_contacts": [
            {
                "id": c.id,
                "name": c.full_name,
                "email": c.email,
                "institution": c.institution.name,
                "status": c.verification_status,
            }
            for c in bounced
        ],
        "stopped_sequences": [_seq_out(s).model_dump() for s in stopped],
    }


# ---------------------------------------------------------------------------
# prospects and sequences
# ---------------------------------------------------------------------------
@router.get("/eligible")
def eligible(_: CurrentUser, db: DB, q: str | None = None, limit: int = Query(50, le=200)) -> list[dict[str, Any]]:
    """Open leads whose research was approved and pushed, with each contact's readiness."""
    stmt = (
        select(Lead)
        .join(Institution)
        .options(selectinload(Lead.institution))
        .where(Lead.stage.not_in([s.value for s in CLOSED_STAGES]), Institution.research_score.is_not(None))
        .order_by(Institution.research_score.desc())
        .limit(limit)
    )
    if q:
        stmt = stmt.where(Institution.name.ilike(f"%{q}%"))
    out = []
    for lead in db.scalars(stmt):
        if S.pushed_run(db, lead.institution_id) is None:
            continue
        contacts = db.scalars(
            select(Contact).where(Contact.institution_id == lead.institution_id).order_by(Contact.last_name)
        ).all()
        out.append(
            {
                "lead_id": lead.id,
                "institution": {
                    "id": lead.institution.id,
                    "name": lead.institution.name,
                    "score": lead.institution.research_score,
                    "tier": lead.institution.research_tier,
                },
                "stage": lead.stage,
                "contacts": [
                    {
                        "id": c.id,
                        "name": c.full_name,
                        "designation": c.designation,
                        "email": c.email,
                        "problems": S.eligibility(db, lead, c),
                    }
                    for c in contacts
                ],
            }
        )
    return out


class SequenceCreate(BaseModel):
    lead_id: int
    contact_id: int
    mailbox_id: int
    use_case: str | None = None


@router.post("/sequences", response_model=SequenceDetail, status_code=201)
def create_sequence(body: SequenceCreate, user: CurrentUser, db: DB) -> SequenceDetail:
    if body.use_case and body.use_case not in USE_CASES:
        raise HTTPException(422, f"Unknown use case. Choose one of: {', '.join(USE_CASES)}")
    seq = S.create_sequence(db, user, body.lead_id, body.contact_id, body.mailbox_id, body.use_case)
    return _detail(db, S.load_sequence(db, seq.id))


@router.get("/sequences", response_model=Page[SequenceOut])
def list_sequences(
    _: CurrentUser,
    db: DB,
    params: Annotated[ListParams, Depends()],
    status: str | None = None,
    lead_id: int | None = None,
    institution_id: int | None = None,
) -> Page[SequenceOut]:
    q = (
        select(OutreachSequence)
        .options(selectinload(OutreachSequence.institution), selectinload(OutreachSequence.contact))
        .order_by(OutreachSequence.id.desc())
    )
    if status:
        q = q.where(OutreachSequence.status == status)
    if lead_id:
        q = q.where(OutreachSequence.lead_id == lead_id)
    if institution_id:
        q = q.where(OutreachSequence.institution_id == institution_id)
    items, total = paginate(db, q, params)
    return Page(items=[_seq_out(s) for s in items], total=total, page=params.page, page_size=params.page_size)


@router.get("/sequences/{seq_id}", response_model=SequenceDetail)
def get_sequence(seq_id: int, _: CurrentUser, db: DB) -> SequenceDetail:
    return _detail(db, S.load_sequence(db, seq_id))


class StopIn(BaseModel):
    reason: Literal["not_a_fit", "cancelled"]
    note: str | None = Field(None, max_length=500)


@router.post("/sequences/{seq_id}/stop", response_model=SequenceDetail)
def stop(seq_id: int, body: StopIn, user: CurrentUser, db: DB) -> SequenceDetail:
    seq = S.load_sequence(db, seq_id)
    if seq.status not in S.LIVE:
        raise HTTPException(409, f"Sequence is already {seq.status}")
    S.stop_sequence(db, seq, body.reason, user, body.note)
    db.commit()
    db.expire_all()
    return _detail(db, S.load_sequence(db, seq_id))


class MessageEdit(BaseModel):
    subject: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=20, max_length=10000)


class ScheduleIn(BaseModel):
    when: datetime | None = None


def _after(db: DB, m: OutreachMessage) -> SequenceDetail:
    db.expire_all()
    return _detail(db, S.load_sequence(db, m.sequence_id))


@router.patch("/messages/{message_id}", response_model=SequenceDetail)
def edit(message_id: int, body: MessageEdit, user: CurrentUser, db: DB) -> SequenceDetail:
    m = _msg(db, message_id)
    S.edit_message(db, m, user, body.subject, body.body)
    return _after(db, m)


@router.post("/messages/{message_id}/regenerate", response_model=SequenceDetail)
def regenerate(message_id: int, user: CurrentUser, db: DB) -> SequenceDetail:
    m = _msg(db, message_id)
    S.regenerate(db, m, user)
    return _after(db, m)


@router.post("/messages/{message_id}/approve", response_model=SequenceDetail)
def approve(message_id: int, user: CurrentUser, db: DB) -> SequenceDetail:
    m = _msg(db, message_id)
    S.approve(db, m, user)
    return _after(db, m)


@router.post("/messages/{message_id}/schedule", response_model=SequenceDetail)
def schedule(message_id: int, body: ScheduleIn, user: CurrentUser, db: DB) -> SequenceDetail:
    m = _msg(db, message_id)
    S.schedule(db, m, user, body.when)
    return _after(db, m)


@router.post("/messages/{message_id}/send-now", response_model=SequenceDetail)
def send_now(message_id: int, user: CurrentUser, db: DB) -> SequenceDetail:
    m = _msg(db, message_id)
    S.send_now(db, m, user)
    return _after(db, m)


@router.post("/messages/{message_id}/cancel", response_model=SequenceDetail)
def cancel(message_id: int, user: CurrentUser, db: DB) -> SequenceDetail:
    m = _msg(db, message_id)
    S.cancel_message(db, m, user)
    return _after(db, m)


@router.post("/messages/{message_id}/recheck", response_model=SequenceDetail)
def recheck(message_id: int, _: CurrentUser, db: DB) -> SequenceDetail:
    m = _msg(db, message_id)
    S.recheck(db, m)
    db.commit()
    return _after(db, m)


# ---------------------------------------------------------------------------
# replies
# ---------------------------------------------------------------------------
class ReplyUpdate(BaseModel):
    label: (
        Literal[
            "interested",
            "meeting_request",
            "needs_information",
            "not_now",
            "not_interested",
            "referral",
            "out_of_office",
            "unclear",
        ]
        | None
    ) = None
    opt_out: bool | None = None
    handled: bool | None = None


@router.patch("/replies/{reply_id}", response_model=ReplyOut)
def update_reply(reply_id: int, body: ReplyUpdate, user: CurrentUser, db: DB) -> EmailReply:
    r = get_or_404(db, EmailReply, reply_id, "Reply")
    if body.label or body.opt_out:
        S.relabel(db, r, user, body.label or r.label, bool(body.opt_out))
    if body.handled is not None:
        r.handled, r.handled_by_id, r.handled_at = body.handled, user.id, datetime.now(UTC) if body.handled else None
        audit.record(db, user, "update", "email_reply", r.id, {"handled": body.handled})
        db.commit()
    return r


@router.post("/replies/{reply_id}/draft", response_model=SequenceDetail)
def draft_reply(reply_id: int, user: CurrentUser, db: DB) -> SequenceDetail:
    r = get_or_404(db, EmailReply, reply_id, "Reply")
    m = S.reply_draft(db, r, user)
    return _after(db, m)


# ---------------------------------------------------------------------------
# mailboxes and OAuth
# ---------------------------------------------------------------------------
@router.get("/mailboxes", response_model=list[MailboxOut])
def list_mailboxes(_: CurrentUser, db: DB) -> list[MailboxOut]:
    return [_mailbox_out(db, m) for m in db.scalars(select(Mailbox).options(selectinload(Mailbox.owner)).order_by(Mailbox.id))]


@router.get("/providers")
def providers(_: CurrentUser) -> list[dict[str, Any]]:
    s = get_settings()
    return [
        {
            "key": "mock",
            "name": "Mock mailbox (testing)",
            "available": s.outreach_mock_provider_enabled,
            "note": "Nothing leaves the system.",
        },
        {
            "key": "microsoft",
            "name": "Microsoft 365 / Outlook",
            "available": oauth_mail.configured("microsoft", s),
            "note": "Needs MICROSOFT_CLIENT_ID, MICROSOFT_CLIENT_SECRET and TOKEN_ENCRYPTION_KEY.",
        },
        {
            "key": "gmail",
            "name": "Google Workspace / Gmail",
            "available": oauth_mail.configured("gmail", s),
            "note": "Needs GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and TOKEN_ENCRYPTION_KEY.",
        },
    ]


class MockMailboxIn(BaseModel):
    email_address: EmailStr
    display_name: str = Field(min_length=2, max_length=200)
    is_shared: bool = True
    authorization_note: str = Field(min_length=10, max_length=500)
    confirm_authorized: Literal[True]


@router.post("/mailboxes/mock", response_model=MailboxOut, status_code=201)
def add_mock_mailbox(body: MockMailboxIn, user: CurrentUser, db: DB) -> MailboxOut:
    if not get_settings().outreach_mock_provider_enabled:
        raise HTTPException(403, "The mock provider is disabled")
    mb = Mailbox(
        provider="mock",
        email_address=str(body.email_address).lower(),
        display_name=body.display_name,
        is_shared=body.is_shared,
        owner_id=None if body.is_shared else user.id,
        connected_by_id=user.id,
        authorization_note=body.authorization_note,
        scopes=["mock"],
    )
    db.add(mb)
    db.flush()
    audit.record(db, user, "connect", "mailbox", mb.id, {"provider": "mock", "email": mb.email_address})
    db.commit()
    return _mailbox_out(db, mb)


@router.get("/oauth/{provider}/start")
def oauth_start(
    provider: Literal["gmail", "microsoft"],
    user: CurrentUser,
    db: DB,
    authorization_note: str = Query(min_length=10, max_length=500),
    is_shared: bool = False,
    confirm_authorized: bool = False,
) -> dict[str, str]:
    if not confirm_authorized:
        raise HTTPException(422, "Confirm that you are authorized to send from this mailbox")
    s = get_settings()
    if not oauth_mail.configured(provider, s):
        raise HTTPException(409, f"{provider} isn't configured on the server yet (client ID/secret and TOKEN_ENCRYPTION_KEY)")
    state = jwt.encode(
        {
            "uid": user.id,
            "p": provider,
            "shared": is_shared,
            "note": authorization_note,
            "exp": datetime.now(UTC) + timedelta(minutes=10),
        },
        s.jwt_secret,
        algorithm=s.jwt_algorithm,
    )
    return {"url": oauth_mail.authorization_url(provider, state, load_policy(db)["sending_mode"], s)}


@router.get("/oauth/{provider}/callback")
def oauth_callback(
    provider: Literal["gmail", "microsoft"],
    request: Request,
    user: CurrentUser,
    db: DB,
    code: str | None = None,
    state: str = "",
    error: str | None = None,
) -> RedirectResponse:
    s = get_settings()
    back = "/settings?tab=outreach"
    if error or not code:
        return RedirectResponse(f"{back}&mailbox_error={error or 'cancelled'}")
    try:
        st = jwt.decode(state, s.jwt_secret, algorithms=[s.jwt_algorithm])
    except jwt.PyJWTError as e:
        raise HTTPException(400, "Invalid or expired sign-in state; start again") from e
    if st.get("uid") != user.id or st.get("p") != provider:
        raise HTTPException(403, "This sign-in was started by a different user")
    try:
        tokens, email, name = oauth_mail.exchange_code(provider, code)
        cipher = encrypt_tokens(tokens)
    except TokenKeyMissing as e:
        raise HTTPException(409, str(e)) from e
    mb = db.scalar(select(Mailbox).where(Mailbox.provider == provider, func.lower(Mailbox.email_address) == email.lower()))
    if mb is None:
        mb = Mailbox(
            provider=provider,
            email_address=email.lower(),
            display_name=name or email,
            is_shared=bool(st.get("shared")),
            owner_id=None if st.get("shared") else user.id,
            connected_by_id=user.id,
            authorization_note=st["note"],
        )
        db.add(mb)
    elif not mb.is_shared and mb.owner_id != user.id:
        raise HTTPException(403, "This personal mailbox belongs to someone else")
    mb.token_ciphertext, mb.token_expires_at = cipher, datetime.fromisoformat(tokens["expires_at"])
    mb.scopes = oauth_mail.scopes_for(provider, load_policy(db)["sending_mode"])
    mb.status = "pending_approval" if mb.approved_at is None else "active"
    mb.last_error = None
    db.flush()
    audit.record(
        db, user, "connect", "mailbox", mb.id, {"provider": provider, "email": mb.email_address, "scopes": mb.scopes}, request
    )
    db.commit()
    return RedirectResponse(f"{back}&connected={mb.id}")


class MailboxUpdate(BaseModel):
    status: Literal["active", "disabled"] | None = None
    sending_enabled: bool | None = None
    daily_send_limit: int | None = Field(None, ge=1, le=500)


@router.patch("/mailboxes/{mailbox_id}", response_model=MailboxOut)
def update_mailbox(mailbox_id: int, body: MailboxUpdate, user: AdminUser, db: DB) -> MailboxOut:
    mb = get_or_404(db, Mailbox, mailbox_id, "Mailbox")
    before = audit.snapshot(mb)
    data = body.model_dump(exclude_unset=True)
    if data.get("status") == "active":
        if mb.provider != "mock" and not mb.token_ciphertext:
            raise HTTPException(409, "Connect the mailbox before approving it")
        mb.approved_by_id, mb.approved_at = user.id, datetime.now(UTC)
    if data.get("sending_enabled") and mb.provider != "mock" and "Mail.Send" not in mb.scopes and mb.provider == "microsoft":
        raise HTTPException(409, "Reconnect this mailbox after switching the policy to sending, so it can grant Mail.Send")
    for k, v in data.items():
        setattr(mb, k, v)
    audit.record(db, user, "update", "mailbox", mb.id, audit.diff(before, audit.snapshot(mb)))
    db.commit()
    return _mailbox_out(db, mb)


# ---------------------------------------------------------------------------
# policy, content library, templates
# ---------------------------------------------------------------------------
@router.get("/policy")
def get_policy(_: CurrentUser, db: DB) -> dict[str, Any]:
    return {**load_policy(db), "external_sending_allowed": get_settings().outreach_external_sending_allowed}


@router.put("/policy")
def put_policy(body: dict[str, Any], user: AdminUser, db: DB) -> dict[str, Any]:
    body.pop("external_sending_allowed", None)
    return save_policy(db, body, user)


class ContentIn(BaseModel):
    kind: Literal["case_study", "claim", "cta", "value_prop"]
    title: str = Field(min_length=2, max_length=200)
    body: str = Field(min_length=5, max_length=2000)
    use_cases: list[str] = Field(default_factory=list)
    source_note: str | None = Field(None, max_length=500)


class ContentOut(ORM):
    id: int
    kind: str
    title: str
    body: str
    use_cases: list[str]
    status: str
    source_note: str | None
    approved_at: datetime | None
    is_demo: bool
    updated_at: datetime


@router.get("/content", response_model=list[ContentOut])
def list_content(_: CurrentUser, db: DB) -> list[ContentItem]:
    return list(db.scalars(select(ContentItem).order_by(ContentItem.kind, ContentItem.id)))


@router.post("/content", response_model=ContentOut, status_code=201)
def create_content(body: ContentIn, user: ManagerUser, db: DB) -> ContentItem:
    bad = [u for u in body.use_cases if u not in USE_CASES]
    if bad:
        raise HTTPException(422, f"Unknown use cases: {bad}")
    c = ContentItem(**body.model_dump())
    db.add(c)
    db.flush()
    audit.record(db, user, "create", "content_item", c.id, audit.snapshot(c))
    db.commit()
    return c


@router.put("/content/{item_id}", response_model=ContentOut)
def update_content(item_id: int, body: ContentIn, user: ManagerUser, db: DB) -> ContentItem:
    c = get_or_404(db, ContentItem, item_id, "Content item")
    before = audit.snapshot(c)
    for k, v in body.model_dump().items():
        setattr(c, k, v)
    if c.status == "approved":  # any change needs re-approval
        c.status, c.approved_at, c.approved_by_id = "draft", None, None
    audit.record(db, user, "update", "content_item", c.id, audit.diff(before, audit.snapshot(c)))
    db.commit()
    return c


@router.post("/content/{item_id}/status", response_model=ContentOut)
def content_status(
    item_id: int, user: AdminUser, db: DB, status: Literal["approved", "retired", "draft"] = Query()
) -> ContentItem:
    c = get_or_404(db, ContentItem, item_id, "Content item")
    c.status = status
    c.approved_by_id, c.approved_at = (user.id, datetime.now(UTC)) if status == "approved" else (None, None)
    audit.record(db, user, status, "content_item", c.id, {"title": c.title})
    db.commit()
    return c


class TemplateIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    step: int = Field(1, ge=1, le=10)
    use_case: str | None = None
    subject: str = Field(min_length=3, max_length=300)
    body: str = Field("", max_length=5000)


class TemplateOut(ORM):
    id: int
    name: str
    step: int
    use_case: str | None
    subject: str
    body: str
    status: str
    is_demo: bool


@router.get("/templates", response_model=list[TemplateOut])
def list_templates(_: CurrentUser, db: DB) -> list[EmailTemplate]:
    return list(db.scalars(select(EmailTemplate).order_by(EmailTemplate.step, EmailTemplate.id)))


@router.post("/templates", response_model=TemplateOut, status_code=201)
def create_template(body: TemplateIn, user: ManagerUser, db: DB) -> EmailTemplate:
    if body.use_case and body.use_case not in USE_CASES:
        raise HTTPException(422, "Unknown use case")
    t = EmailTemplate(**body.model_dump())
    db.add(t)
    db.flush()
    audit.record(db, user, "create", "email_template", t.id, audit.snapshot(t))
    db.commit()
    return t


@router.post("/templates/{template_id}/status", response_model=TemplateOut)
def template_status(
    template_id: int, user: AdminUser, db: DB, status: Literal["approved", "retired", "draft"] = Query()
) -> EmailTemplate:
    t = get_or_404(db, EmailTemplate, template_id, "Template")
    t.status = status
    t.approved_by_id = user.id if status == "approved" else None
    audit.record(db, user, status, "email_template", t.id, {"name": t.name})
    db.commit()
    return t


# ---------------------------------------------------------------------------
# sync and mock tools
# ---------------------------------------------------------------------------
@router.post("/sync")
def sync_now(_: ManagerUser, db: DB) -> dict[str, int]:
    sent = S.dispatch_due(db)
    res = S.sync_all(db)
    return {"dispatched": sent, **res}


class MockInbound(BaseModel):
    sequence_id: int
    body: str = Field(min_length=1, max_length=5000)
    subject: str | None = None


@router.post("/mock/reply", response_model=SequenceDetail)
def mock_reply(body: MockInbound, _: ManagerUser, db: DB) -> SequenceDetail:
    """Simulate the prospect replying (mock mailboxes only)."""
    seq = S.load_sequence(db, body.sequence_id)
    mb = seq.mailbox
    if mb is None or mb.provider != "mock":
        raise HTTPException(409, "Simulated replies only work with a mock mailbox")
    last = next((m for m in reversed(seq.messages) if m.status in ("sent", "delivered")), None)
    if last is None:
        raise HTTPException(409, "Nothing has been sent in this sequence yet")
    simulate_inbound(
        db,
        mb,
        from_email=seq.contact.email or "",
        subject=body.subject or f"Re: {last.subject}",
        body=body.body,
        thread_id=last.thread_id,
        in_reply_to=last.provider_message_id,
    )
    db.commit()
    S.sync_mailbox(db, mb)
    db.expire_all()
    return _detail(db, S.load_sequence(db, seq.id))
