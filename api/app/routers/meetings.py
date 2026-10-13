from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import audit
from app.config import get_settings
from app.deps import DB, CurrentUser
from app.engagement import coordination as C
from app.engagement.policy import require
from app.meetings import service as M
from app.meetings.providers import authorization_url, configured, exchange_code
from app.models import Activity, CalendarConnection, Contact, Lead, Meeting, MockCalendarEvent, User
from app.outreach.crypto import TokenKeyMissing, encrypt_tokens
from app.schemas import UserRef
from app.services import get_or_404

router = APIRouter(prefix="/meetings", tags=["meetings"])


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MeetingOut(ORM):
    id: int
    lead_id: int
    institution_id: int
    contact_id: int | None
    organizer: UserRef | None
    source: str
    intent: str | None
    requested_text: str | None
    status: str
    title: str
    agenda: str
    duration_minutes: int
    timezone: str
    proposed_slots: list[dict[str, Any]]
    start_at: datetime | None
    end_at: datetime | None
    location: str | None
    attendees: list[dict[str, Any]]
    calendar_event_id: str | None
    approved_at: datetime | None
    confirmed_at: datetime | None
    notes: str | None
    outcome: str | None
    next_steps: str | None
    draft_email_id: int | None
    draft_wa_id: int | None
    is_demo: bool
    created_at: datetime
    institution_name: str = ""
    contact_name: str | None = None
    sequence_id: int | None = None
    conversation_id: int | None = None


def _out(m: Meeting) -> MeetingOut:
    o = MeetingOut.model_validate(m)
    o.institution_name = m.institution.name
    o.contact_name = m.contact.full_name if m.contact else None
    return o


def _load(db: DB, meeting_id: int) -> Meeting:
    m = db.scalars(
        select(Meeting)
        .where(Meeting.id == meeting_id)
        .options(
            selectinload(Meeting.institution),
            selectinload(Meeting.contact),
            selectinload(Meeting.organizer),
            selectinload(Meeting.lead),
        )
    ).first()
    if m is None:
        raise HTTPException(404, "Meeting not found")
    return m


def _detail(db: DB, meeting_id: int) -> MeetingOut:
    db.expire_all()
    m = _load(db, meeting_id)
    o = _out(m)
    if m.draft_email_id:
        from app.models import OutreachMessage

        msg = db.get(OutreachMessage, m.draft_email_id)
        o.sequence_id = msg.sequence_id if msg else None
    if m.draft_wa_id:
        from app.models import WaMessage

        wm = db.get(WaMessage, m.draft_wa_id)
        o.conversation_id = wm.conversation_id if wm else None
    return o


@router.get("", response_model=list[MeetingOut])
def list_meetings(
    _: CurrentUser, db: DB, status: str | None = None, lead_id: int | None = None, upcoming: bool = False
) -> list[MeetingOut]:
    q = select(Meeting).options(selectinload(Meeting.institution), selectinload(Meeting.contact), selectinload(Meeting.organizer))
    if status:
        q = q.where(Meeting.status.in_(status.split(",")))
    if lead_id:
        q = q.where(Meeting.lead_id == lead_id)
    if upcoming:
        q = q.where(Meeting.start_at >= datetime.now(UTC) - timedelta(hours=2))
    return [_out(m) for m in db.scalars(q.order_by(Meeting.start_at.asc().nullsfirst(), Meeting.id.desc()).limit(300))]


@router.get("/{meeting_id}", response_model=MeetingOut)
def get_meeting(meeting_id: int, _: CurrentUser, db: DB) -> MeetingOut:
    return _detail(db, meeting_id)


class ManualIn(BaseModel):
    lead_id: int
    contact_id: int
    note: str | None = Field(None, max_length=500)


@router.post("", response_model=MeetingOut, status_code=201)
def propose_manual(body: ManualIn, user: CurrentUser, db: DB) -> MeetingOut:
    """Start the assistant by hand (e.g. after a phone call where they agreed to meet)."""
    lead = get_or_404(db, Lead, body.lead_id, "Lead")
    contact = get_or_404(db, Contact, body.contact_id, "Contact")
    if contact.institution_id != lead.institution_id:
        raise HTTPException(422, "The contact doesn't belong to this lead's institution")
    if contact.id in C.suppressed_contact_ids(db, [contact]):
        raise HTTPException(409, "This person has opted out")
    act = C.record_activity(
        db, "meeting", f"Meeting requested (logged by {user.full_name})", lead_id=lead.id, institution_id=lead.institution_id,
        contact_id=contact.id, user_id=user.id, body=body.note, details={"direction": "internal"}, is_demo=lead.is_demo,
    )  # fmt: skip
    m = M.propose(
        db,
        lead=lead,
        contact=contact,
        source="manual",
        source_activity=act,
        request_text=body.note,
        intent="meeting_request",
        actor=user,
    )
    db.commit()
    return _detail(db, m.id)


class SlotsIn(BaseModel):
    refresh: bool = True


@router.post("/{meeting_id}/slots", response_model=MeetingOut)
def refresh_slots(meeting_id: int, user: CurrentUser, db: DB) -> MeetingOut:
    m = _load(db, meeting_id)
    if m.status not in ("proposed", "awaiting_approval"):
        raise HTTPException(409, f"Meeting is {m.status}")
    organizer = db.get(User, m.organizer_id) if m.organizer_id else user
    assert organizer is not None
    slots, note = M.free_slots(
        db, organizer, datetime.now(UTC), preferred=M.requested_times(m.requested_text or "", datetime.now(UTC), m.timezone)
    )
    if not slots:
        raise HTTPException(409, note or "No free times found")
    m.proposed_slots = slots
    db.commit()
    return _detail(db, meeting_id)


class ChooseIn(BaseModel):
    start: datetime


@router.post("/{meeting_id}/choose", response_model=MeetingOut)
def choose(meeting_id: int, body: ChooseIn, user: CurrentUser, db: DB) -> MeetingOut:
    M.choose_time(db, _load(db, meeting_id), user, body.start)
    return _detail(db, meeting_id)


@router.post("/{meeting_id}/approve", response_model=MeetingOut)
def approve(meeting_id: int, user: CurrentUser, db: DB) -> MeetingOut:
    M.approve(db, _load(db, meeting_id), user)
    return _detail(db, meeting_id)


@router.post("/{meeting_id}/confirm", response_model=MeetingOut)
def confirm(meeting_id: int, user: CurrentUser, db: DB) -> MeetingOut:
    M.confirm(db, _load(db, meeting_id), user)
    return _detail(db, meeting_id)


class EditIn(BaseModel):
    title: str | None = Field(None, min_length=3, max_length=300)
    agenda: str | None = Field(None, max_length=5000)
    duration_minutes: int | None = Field(None, ge=15, le=120)
    location: str | None = Field(None, max_length=500)
    attendees: list[dict[str, Any]] | None = None


@router.patch("/{meeting_id}", response_model=MeetingOut)
def edit(meeting_id: int, body: EditIn, user: CurrentUser, db: DB) -> MeetingOut:
    m = _load(db, meeting_id)
    if m.status not in ("proposed", "awaiting_approval") and (body.duration_minutes or body.title):
        raise HTTPException(409, "A booked meeting's time and title change in the calendar; cancel and re-book instead")
    data = body.model_dump(exclude_unset=True)
    for a in data.get("attendees") or []:
        if not a.get("email") or "@" not in a["email"]:
            raise HTTPException(422, "Each attendee needs an email address")
    for k, v in data.items():
        setattr(m, k, v)
    audit.record(db, user, "update", "meeting", m.id, {k: str(v)[:200] for k, v in data.items()})
    db.commit()
    return _detail(db, meeting_id)


class OutcomeIn(BaseModel):
    outcome: Literal["positive", "neutral", "negative", "no_show"]
    notes: str | None = Field(None, max_length=10000)
    next_steps: str | None = Field(None, max_length=2000)
    attendees: list[dict[str, Any]] | None = None


@router.post("/{meeting_id}/outcome", response_model=MeetingOut)
def outcome(meeting_id: int, body: OutcomeIn, user: CurrentUser, db: DB) -> MeetingOut:
    M.record_outcome(
        db,
        _load(db, meeting_id),
        user,
        outcome=body.outcome,
        notes=body.notes,
        next_steps=body.next_steps,
        attendees=body.attendees,
    )
    return _detail(db, meeting_id)


class CancelIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


@router.post("/{meeting_id}/cancel", response_model=MeetingOut)
def cancel(meeting_id: int, body: CancelIn, user: CurrentUser, db: DB) -> MeetingOut:
    M.cancel(db, _load(db, meeting_id), user, body.reason)
    return _detail(db, meeting_id)


# ---------------------------------------------------------------------------
# calendar connections
# ---------------------------------------------------------------------------
class CalendarOut(ORM):
    id: int
    user_id: int
    provider: str
    email_address: str
    status: str
    scopes: list[str]
    approved_at: datetime | None
    last_error: str | None
    user_name: str = ""


@router.get("/calendars/connections", response_model=list[CalendarOut])
def list_calendars(_: CurrentUser, db: DB) -> list[CalendarOut]:
    out = []
    for c in db.scalars(
        select(CalendarConnection).options(selectinload(CalendarConnection.user)).order_by(CalendarConnection.id)
    ):
        o = CalendarOut.model_validate(c)
        o.user_name = c.user.full_name
        out.append(o)
    return out


@router.get("/calendars/providers")
def calendar_providers(_: CurrentUser) -> list[dict[str, Any]]:
    return [
        {
            "key": "mock",
            "name": "Test calendar",
            "available": configured("mock"),
            "note": "For testing: nothing leaves the system.",
        },
        {
            "key": "google",
            "name": "Google Calendar",
            "available": configured("google"),
            "note": "Needs GOOGLE_CLIENT_ID/SECRET and TOKEN_ENCRYPTION_KEY.",
        },
        {
            "key": "microsoft",
            "name": "Microsoft 365 / Outlook",
            "available": configured("microsoft"),
            "note": "Needs MICROSOFT_CLIENT_ID/SECRET and TOKEN_ENCRYPTION_KEY.",
        },
    ]


@router.post("/calendars/mock", response_model=CalendarOut, status_code=201)
def connect_mock(user: CurrentUser, db: DB) -> CalendarOut:
    if not configured("mock"):
        raise HTTPException(403, "The test calendar is disabled")
    existing = db.scalar(
        select(CalendarConnection).where(CalendarConnection.user_id == user.id, CalendarConnection.provider == "mock")
    )
    c = existing or CalendarConnection(user_id=user.id, provider="mock", email_address=user.email, scopes=["mock"])
    if not existing:
        db.add(c)
    db.flush()
    audit.record(db, user, "connect", "calendar", c.id, {"provider": "mock"})
    db.commit()
    o = CalendarOut.model_validate(c)
    o.user_name = user.full_name
    return o


class CalendarUpdate(BaseModel):
    status: Literal["active", "disabled"]


@router.patch("/calendars/connections/{conn_id}", response_model=CalendarOut)
def update_calendar(conn_id: int, body: CalendarUpdate, user: CurrentUser, db: DB) -> CalendarOut:
    require(db, user, "calendar_approve")
    c = get_or_404(db, CalendarConnection, conn_id, "Calendar connection")
    if body.status == "active":
        if c.provider != "mock" and not c.token_ciphertext:
            raise HTTPException(409, "Connect the calendar first")
        c.approved_by_id, c.approved_at = user.id, datetime.now(UTC)
    c.status = body.status
    audit.record(db, user, "update", "calendar", c.id, {"status": body.status})
    db.commit()
    o = CalendarOut.model_validate(c)
    o.user_name = c.user.full_name
    return o


@router.get("/oauth/{provider}/start")
def oauth_start(provider: Literal["google", "microsoft"], user: CurrentUser) -> dict[str, str]:
    s = get_settings()
    if not configured(provider, s):
        raise HTTPException(409, f"{provider} calendar isn't configured on the server yet")
    state = jwt.encode(
        {"uid": user.id, "p": provider, "k": "cal", "exp": datetime.now(UTC) + timedelta(minutes=10)},
        s.jwt_secret,
        algorithm=s.jwt_algorithm,
    )
    return {"url": authorization_url(provider, state, s)}


@router.get("/oauth/{provider}/callback")
def oauth_callback(
    provider: Literal["google", "microsoft"],
    user: CurrentUser,
    db: DB,
    code: str | None = None,
    state: str = "",
    error: str | None = None,
) -> RedirectResponse:
    s = get_settings()
    back = "/settings?tab=channels"
    if error or not code:
        return RedirectResponse(f"{back}&calendar_error={error or 'cancelled'}")
    try:
        st = jwt.decode(state, s.jwt_secret, algorithms=[s.jwt_algorithm])
    except jwt.PyJWTError as e:
        raise HTTPException(400, "Invalid or expired sign-in state; start again") from e
    if st.get("uid") != user.id or st.get("p") != provider or st.get("k") != "cal":
        raise HTTPException(403, "This sign-in was started by a different user")
    try:
        tokens, email = exchange_code(provider, code)
        cipher = encrypt_tokens(tokens)
    except TokenKeyMissing as e:
        raise HTTPException(409, str(e)) from e
    c = db.scalar(
        select(CalendarConnection).where(CalendarConnection.user_id == user.id, CalendarConnection.provider == provider)
    )
    if c is None:
        c = CalendarConnection(user_id=user.id, provider=provider, email_address=email)
        db.add(c)
    c.email_address, c.token_ciphertext, c.token_expires_at = email, cipher, datetime.fromisoformat(tokens["expires_at"])
    from app.meetings.providers import SCOPES

    c.scopes = SCOPES[provider]
    c.status = "active" if c.approved_at else "pending_approval"
    c.last_error = None
    db.flush()
    audit.record(db, user, "connect", "calendar", c.id, {"provider": provider, "email": email})
    db.commit()
    return RedirectResponse(f"{back}&calendar_connected={c.id}")


class BusyIn(BaseModel):
    start: datetime
    end: datetime
    title: str = "Busy"


@router.post("/calendars/mock/busy", status_code=201)
def add_mock_busy(body: BusyIn, user: CurrentUser, db: DB) -> dict[str, int]:
    """Test calendar only: block out a time so the assistant avoids it."""
    e = MockCalendarEvent(owner_email=user.email.lower(), title=body.title, start_at=body.start, end_at=body.end)
    db.add(e)
    db.commit()
    return {"id": e.id}


@router.get("/availability/preview")
def availability(user: CurrentUser, db: DB, days: int = Query(7, ge=1, le=30)) -> dict[str, Any]:
    slots, note = M.free_slots(db, user, datetime.now(UTC), count=6)
    return {"slots": slots, "note": note}


_ = (Activity,)
