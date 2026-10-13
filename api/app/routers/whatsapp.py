import json
import time
import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import SessionLocal
from app.deps import DB, CurrentUser, ManagerUser
from app.engagement import coordination as C
from app.engagement.logs import event
from app.engagement.policy import require
from app.models import Contact, User, WaConversation, WaMessage, WaTemplate
from app.schemas import UserRef
from app.services import get_or_404
from app.whatsapp import service as WS
from app.whatsapp.providers import get_provider
from app.whatsapp.providers.base import build_inbound_payload, build_status_payload, sign, signature_valid

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TemplateIn(BaseModel):
    name: str = Field(min_length=3, max_length=100)
    language: str = Field("en", max_length=10)
    category: Literal["marketing", "utility"] = "marketing"
    purpose: Literal["first_contact", "follow_up", "meeting", "reply"] = "first_contact"
    body: str = Field(min_length=10, max_length=1024)
    params: list[dict[str, str]] = Field(default_factory=list)


class TemplateOut(ORM):
    id: int
    name: str
    language: str
    category: str
    purpose: str
    body: str
    params: list[dict[str, Any]]
    status: str
    rejection_reason: str | None
    is_demo: bool
    updated_at: datetime


class MessageOut(ORM):
    id: int
    conversation_id: int
    direction: str
    kind: str
    template_id: int | None
    body: str
    purpose: str | None
    status: str
    error: str | None
    attempts: int
    label: str | None
    label_confidence: int | None
    opt_out: bool
    suggestion: str | None
    approved_at: datetime | None
    sent_at: datetime | None
    delivered_at: datetime | None
    read_at: datetime | None
    created_at: datetime


class ConversationOut(ORM):
    id: int
    wa_number: str
    contact_id: int | None
    lead_id: int | None
    institution_id: int | None
    profile_name: str | None
    state: str
    assigned_to: UserRef | None
    handoff_reason: str | None
    last_inbound_at: datetime | None
    last_outbound_at: datetime | None
    unread: int
    is_demo: bool
    contact_name: str | None = None
    institution_name: str | None = None
    window_open: bool = False
    last_message: str | None = None


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]
    contact_state: dict[str, Any] | None = None


def _conv_out(db: DB, c: WaConversation, last: str | None = None) -> ConversationOut:
    o = ConversationOut.model_validate(c)
    if c.contact:
        o.contact_name, o.institution_name = c.contact.full_name, c.contact.institution.name
    o.window_open = WS.window_open(c)
    o.last_message = last
    return o


def _detail(db: DB, conv_id: int) -> ConversationDetail:
    db.expire_all()
    c = WS.load_conversation(db, conv_id)
    if c.unread:
        c.unread = 0
        db.commit()
        c = WS.load_conversation(db, conv_id)
    d = ConversationDetail(**_conv_out(db, c).model_dump(), messages=[MessageOut.model_validate(m) for m in c.messages])
    if c.contact:
        d.contact_state = C.contact_state(db, c.contact)
    return d


# ---------------------------------------------------------------------------
# status, templates
# ---------------------------------------------------------------------------
@router.get("/status")
def status(_: CurrentUser, db: DB) -> dict[str, Any]:
    s = get_settings()
    ok, why = WS.sending_mode(db, s)
    from app.whatsapp.providers.meta import configured

    return {
        "provider": s.whatsapp_provider,
        "configured": s.whatsapp_provider == "mock" or configured(s),
        "sending": ok,
        "reason": why,
        "external_sending_allowed": s.whatsapp_external_sending_allowed,
        "webhook_url": f"{s.oauth_redirect_base_url.rstrip('/')}/api/whatsapp/webhook",
        "opt_in_methods": WS.OPT_IN_METHODS,
        "param_sources": WS.PARAM_SOURCES,
    }


@router.get("/templates", response_model=list[TemplateOut])
def list_templates(_: CurrentUser, db: DB) -> list[WaTemplate]:
    return list(db.scalars(select(WaTemplate).order_by(WaTemplate.status, WaTemplate.name)))


@router.post("/templates", response_model=TemplateOut, status_code=201)
def create_template(body: TemplateIn, user: CurrentUser, db: DB) -> WaTemplate:
    require(db, user, "whatsapp_templates")
    return WS.save_template(db, user, body.model_dump())


@router.put("/templates/{template_id}", response_model=TemplateOut)
def update_template(template_id: int, body: TemplateIn, user: CurrentUser, db: DB) -> WaTemplate:
    require(db, user, "whatsapp_templates")
    return WS.save_template(db, user, body.model_dump(), get_or_404(db, WaTemplate, template_id, "Template"))


@router.post("/templates/{template_id}/submit", response_model=TemplateOut)
def submit_template(template_id: int, user: CurrentUser, db: DB) -> WaTemplate:
    require(db, user, "whatsapp_templates")
    return WS.submit_template(db, user, get_or_404(db, WaTemplate, template_id, "Template"))


@router.post("/templates/sync")
def sync_templates(user: CurrentUser, db: DB) -> dict[str, int]:
    require(db, user, "whatsapp_templates")
    return {"updated": WS.sync_templates(db)}


# ---------------------------------------------------------------------------
# opt-in
# ---------------------------------------------------------------------------
class OptInIn(BaseModel):
    number: str = Field(min_length=8, max_length=25)
    method: str
    evidence: str = Field(min_length=10, max_length=400)


@router.post("/contacts/{contact_id}/opt-in")
def opt_in(contact_id: int, body: OptInIn, user: CurrentUser, db: DB) -> dict[str, Any]:
    require(db, user, "whatsapp_optin")
    c = get_or_404(db, Contact, contact_id, "Contact")
    rec = WS.record_opt_in(db, c, body.number, body.method, body.evidence, user)
    return {"contact_id": c.id, "whatsapp_number": c.whatsapp_number, "consent_id": rec.id}


# ---------------------------------------------------------------------------
# conversations and messages
# ---------------------------------------------------------------------------
@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(
    user: CurrentUser, db: DB, state: str | None = None, q: str | None = None, mine: bool = False
) -> list[ConversationOut]:
    stmt = select(WaConversation).options(selectinload(WaConversation.contact), selectinload(WaConversation.assigned_to))
    if state:
        stmt = stmt.where(WaConversation.state == state)
    if mine:
        stmt = stmt.where(WaConversation.assigned_to_id == user.id)
    if q:
        stmt = stmt.outerjoin(Contact, Contact.id == WaConversation.contact_id).where(
            or_(
                WaConversation.wa_number.ilike(f"%{q}%"),
                WaConversation.profile_name.ilike(f"%{q}%"),
                Contact.last_name.ilike(f"%{q}%"),
            )
        )
    convs = db.scalars(
        stmt.order_by(func.coalesce(WaConversation.last_inbound_at, WaConversation.created_at).desc()).limit(200)
    ).all()
    lasts: dict[int, str] = (
        dict(
            db.execute(  # type: ignore[arg-type]
                select(WaMessage.conversation_id, WaMessage.body)
                .distinct(WaMessage.conversation_id)
                .where(WaMessage.conversation_id.in_([c.id for c in convs]))
                .order_by(WaMessage.conversation_id, WaMessage.id.desc())
            ).all()
        )
        if convs
        else {}
    )
    return [_conv_out(db, c, lasts.get(c.id)) for c in convs]


@router.get("/conversations/{conv_id}", response_model=ConversationDetail)
def get_conversation(conv_id: int, _: CurrentUser, db: DB) -> ConversationDetail:
    return _detail(db, conv_id)


class StartIn(BaseModel):
    contact_id: int
    template_id: int
    custom: list[str] = Field(default_factory=list)


@router.post("/conversations", response_model=ConversationDetail, status_code=201)
def start(body: StartIn, user: CurrentUser, db: DB) -> ConversationDetail:
    """Draft a first WhatsApp message (an approved template) to a contact who has opted in."""
    c = get_or_404(db, Contact, body.contact_id, "Contact")
    if not c.whatsapp_number:
        raise HTTPException(422, "Record the contact's WhatsApp number and opt-in first")
    v = C.check_outreach(db, c, "whatsapp", "cold")
    if v.blocking:
        raise HTTPException(409, "Can't start a WhatsApp conversation: " + v.summary())
    conv = WS.conversation_for(db, c.whatsapp_number, c)
    WS.draft(db, user, conv, template_id=body.template_id, custom=body.custom, purpose="first_contact")
    db.commit()
    return _detail(db, conv.id)


class DraftIn(BaseModel):
    text: str | None = Field(None, max_length=4000)
    template_id: int | None = None
    custom: list[str] = Field(default_factory=list)
    purpose: Literal["reply", "follow_up", "meeting"] = "reply"


@router.post("/conversations/{conv_id}/messages", response_model=ConversationDetail)
def draft_message(conv_id: int, body: DraftIn, user: CurrentUser, db: DB) -> ConversationDetail:
    conv = WS.load_conversation(db, conv_id)
    if conv.state == "handoff" and conv.assigned_to_id not in (None, user.id) and user.role != "admin":
        raise HTTPException(409, "Another person has taken over this conversation")
    WS.draft(db, user, conv, text=body.text, template_id=body.template_id, custom=body.custom, purpose=body.purpose)
    db.commit()
    return _detail(db, conv_id)


class EditIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


def _msg(db: DB, message_id: int) -> WaMessage:
    return get_or_404(db, WaMessage, message_id, "Message")


@router.patch("/messages/{message_id}", response_model=ConversationDetail)
def edit_message(message_id: int, body: EditIn, user: CurrentUser, db: DB) -> ConversationDetail:
    m = WS.edit(db, _msg(db, message_id), user, body.text)
    return _detail(db, m.conversation_id)


@router.post("/messages/{message_id}/send", response_model=ConversationDetail)
def approve_and_send(message_id: int, user: CurrentUser, db: DB) -> ConversationDetail:
    require(db, user, "whatsapp_send")
    m = _msg(db, message_id)
    conv = m.conversation
    if conv.state == "handoff" and conv.assigned_to_id not in (None, user.id) and user.role != "admin":
        raise HTTPException(409, "Another person has taken over this conversation")
    WS.approve_and_send(db, m, user)
    return _detail(db, m.conversation_id)


@router.post("/messages/{message_id}/cancel", response_model=ConversationDetail)
def cancel_message(message_id: int, _: CurrentUser, db: DB) -> ConversationDetail:
    m = _msg(db, message_id)
    if m.direction != "out" or m.status not in ("draft", "approved", "queued", "blocked"):
        raise HTTPException(409, f"A {m.status} message can't be cancelled")
    m.status = "cancelled"
    db.commit()
    return _detail(db, m.conversation_id)


class AssignIn(BaseModel):
    action: Literal["take", "assign", "release", "close"]
    user_id: int | None = None
    reason: str | None = Field(None, max_length=300)


@router.post("/conversations/{conv_id}/assign", response_model=ConversationDetail)
def assign(conv_id: int, body: AssignIn, user: CurrentUser, db: DB) -> ConversationDetail:
    require(db, user, "conversation_assign")
    conv = WS.load_conversation(db, conv_id)
    if body.action == "take":
        WS.handoff(db, conv, user, body.reason or f"Taken over by {user.full_name}", assign_to=user.id)
    elif body.action == "assign":
        if not body.user_id or not db.get(User, body.user_id):
            raise HTTPException(422, "Choose who should take it")
        WS.handoff(db, conv, user, body.reason or "Assigned", assign_to=body.user_id)
    elif body.action == "release":
        WS.release(db, conv, user)
    else:
        conv.state = "closed"
    db.commit()
    return _detail(db, conv_id)


# ---------------------------------------------------------------------------
# webhook (no login: verified by signature) and mock tools
# ---------------------------------------------------------------------------
@router.get("/webhook", response_class=PlainTextResponse)
def verify_webhook(
    mode: str = Query("", alias="hub.mode"),
    token: str = Query("", alias="hub.verify_token"),
    challenge: str = Query("", alias="hub.challenge"),
) -> str:
    s = get_settings()
    if mode == "subscribe" and s.whatsapp_verify_token and token == s.whatsapp_verify_token:
        return challenge
    raise HTTPException(403, "Verification failed")


@router.post("/webhook")
async def webhook(request: Request) -> dict[str, Any]:
    raw = await request.body()
    s = get_settings()
    with SessionLocal() as db:
        provider = get_provider(db, s)
        if not signature_valid(provider.webhook_secret(), raw, request.headers.get("x-hub-signature-256")):
            event("whatsapp_webhook_rejected", reason="bad signature")
            raise HTTPException(401, "Invalid signature")
        try:
            payload = json.loads(raw or b"{}")
        except ValueError as e:
            raise HTTPException(400, "Invalid JSON") from e
        return WS.process_webhook(db, payload, provider.name, s)


class MockInboundIn(BaseModel):
    number: str
    text: str = Field(min_length=1, max_length=4000)
    name: str | None = None


def _post_signed(payload: dict[str, Any]) -> dict[str, Any]:
    """Feed a payload through the real webhook path (signature included) — what WhatsApp would do."""
    s = get_settings()
    if s.whatsapp_provider != "mock":
        raise HTTPException(409, "Simulation only works with the mock WhatsApp provider")
    raw = json.dumps(payload).encode()
    if not signature_valid(s.mock_webhook_secret, raw, sign(s.mock_webhook_secret, raw)):
        raise HTTPException(500, "Signing failed")
    with SessionLocal() as db:
        return WS.process_webhook(db, json.loads(raw), "mock", s)


@router.post("/mock/inbound")
def mock_inbound(body: MockInboundIn, _: ManagerUser) -> dict[str, Any]:
    number = WS.normalize_number(body.number)
    if not number:
        raise HTTPException(422, "Invalid number")
    return _post_signed(
        build_inbound_payload("mock-phone", number, body.text, f"wamid.IN{uuid.uuid4().hex[:20]}", body.name, int(time.time()))
    )


class MockStatusIn(BaseModel):
    message_id: int
    status: Literal["delivered", "read", "failed"]


@router.post("/mock/status")
def mock_status(body: MockStatusIn, _: ManagerUser, db: DB) -> dict[str, Any]:
    m = get_or_404(db, WaMessage, body.message_id, "Message")
    if not m.provider_message_id:
        raise HTTPException(409, "Message hasn't been sent")
    code = "131026" if body.status == "failed" else None
    return _post_signed(
        build_status_payload(
            m.provider_message_id, body.status, int(time.time()), code, "Message undeliverable" if code else None
        )
    )
