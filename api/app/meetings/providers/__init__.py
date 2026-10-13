"""Calendar providers for the meeting assistant: mock (tests/demos), Google Calendar and Microsoft 365 (Graph).

Least privilege: free/busy + create/update the events this app creates. Tokens are encrypted at rest; no passwords.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import CalendarConnection, MockCalendarEvent
from app.outreach.crypto import decrypt_tokens, encrypt_tokens
from app.outreach.providers.base import EmailProviderError
from app.outreach.providers.oauth_mail import GOOGLE_AUTH, GRAPH_API, MS_LOGIN, _token_request

GCAL_API = "https://www.googleapis.com/calendar/v3"
SCOPES = {
    "google": [
        "https://www.googleapis.com/auth/calendar.freebusy",
        "https://www.googleapis.com/auth/calendar.events",
        "openid",
        "email",
    ],
    "microsoft": ["offline_access", "User.Read", "Calendars.ReadWrite"],
}


class CalendarError(Exception):
    def __init__(self, message: str, *, transient: bool = False, needs_reauth: bool = False) -> None:
        super().__init__(message)
        self.transient, self.needs_reauth = transient, needs_reauth


@dataclass(frozen=True)
class NewEvent:
    title: str
    description: str
    start: datetime
    end: datetime
    timezone: str
    attendees: tuple[str, ...]
    idempotency_key: str
    location: str | None = None


@dataclass(frozen=True)
class CreatedEvent:
    event_id: str
    join_url: str | None = None


class CalendarProvider(Protocol):
    name: str

    def busy(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]: ...

    def create_event(self, ev: NewEvent) -> CreatedEvent: ...

    def cancel_event(self, event_id: str) -> None: ...


def redirect_uri(provider: str, s: Settings | None = None) -> str:
    s = s or get_settings()
    return f"{s.oauth_redirect_base_url.rstrip('/')}/api/meetings/oauth/{provider}/callback"


def configured(provider: str, s: Settings | None = None) -> bool:
    s = s or get_settings()
    if provider == "mock":
        return s.calendar_mock_enabled
    if provider == "google":
        return bool(s.google_client_id and s.google_client_secret and s.token_encryption_key)
    if provider == "microsoft":
        return bool(s.microsoft_client_id and s.microsoft_client_secret and s.token_encryption_key)
    return False


def authorization_url(provider: str, state: str, s: Settings | None = None) -> str:
    s = s or get_settings()
    if provider == "google":
        q = {
            "client_id": s.google_client_id, "redirect_uri": redirect_uri(provider, s), "response_type": "code",
            "scope": " ".join(SCOPES["google"]), "access_type": "offline", "prompt": "consent", "state": state,
        }  # fmt: skip
        return f"{GOOGLE_AUTH}?{urlencode(q)}"
    q = {
        "client_id": s.microsoft_client_id, "redirect_uri": redirect_uri(provider, s), "response_type": "code",
        "response_mode": "query", "scope": " ".join(SCOPES["microsoft"]), "state": state, "prompt": "select_account",
    }  # fmt: skip
    return f"{MS_LOGIN}/{s.microsoft_tenant}/oauth2/v2.0/authorize?{urlencode(q)}"


def exchange_code(
    provider: str, code: str, client: httpx.Client | None = None, s: Settings | None = None
) -> tuple[dict[str, Any], str]:
    s = s or get_settings()
    client = client or httpx.Client(timeout=30)
    try:
        tokens = _token_request(
            "gmail" if provider == "google" else "microsoft",
            {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(provider, s)},
            client,
            s,
        )
    except EmailProviderError as e:
        raise CalendarError(str(e), needs_reauth=True) from e
    h = {"Authorization": f"Bearer {tokens['access_token']}"}
    if provider == "google":
        info = client.get("https://openidconnect.googleapis.com/v1/userinfo", headers=h).json()
        return tokens, info.get("email", "")
    me = client.get(f"{GRAPH_API}/me", headers=h).json()
    return tokens, me.get("mail") or me.get("userPrincipalName", "")


class MockCalendar:
    name = "mock"

    def __init__(self, db: Session, owner_email: str) -> None:
        self.db, self.owner = db, owner_email.lower()

    def busy(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        rows = self.db.scalars(
            select(MockCalendarEvent).where(
                MockCalendarEvent.owner_email == self.owner, MockCalendarEvent.end_at > start, MockCalendarEvent.start_at < end
            )
        )
        return [(r.start_at, r.end_at) for r in rows]

    def create_event(self, ev: NewEvent) -> CreatedEvent:
        existing = self.db.scalar(select(MockCalendarEvent).where(MockCalendarEvent.idempotency_key == ev.idempotency_key))
        if existing:
            return CreatedEvent(f"mock-event-{existing.id}", "https://meet.example/plugdrop-demo")
        e = MockCalendarEvent(
            owner_email=self.owner, title=ev.title, start_at=ev.start, end_at=ev.end, attendees=list(ev.attendees),
            idempotency_key=ev.idempotency_key,
        )  # fmt: skip
        self.db.add(e)
        self.db.flush()
        return CreatedEvent(f"mock-event-{e.id}", ev.location or "https://meet.example/plugdrop-demo")

    def cancel_event(self, event_id: str) -> None:
        eid = event_id.removeprefix("mock-event-")
        if eid.isdigit() and (e := self.db.get(MockCalendarEvent, int(eid))):
            self.db.delete(e)


class _OAuthCalendar:
    name = ""

    def __init__(
        self, db: Session, conn: CalendarConnection, client: httpx.Client | None = None, s: Settings | None = None
    ) -> None:
        self.db, self.conn, self.s = db, conn, s or get_settings()
        self.c = client or httpx.Client(timeout=30)

    def _token(self) -> str:
        if not self.conn.token_ciphertext:
            raise CalendarError("Calendar is not connected", needs_reauth=True)
        t = decrypt_tokens(self.conn.token_ciphertext)
        if datetime.fromisoformat(t["expires_at"]) > datetime.now(UTC) + timedelta(seconds=30):
            return str(t["access_token"])
        if not t.get("refresh_token"):
            raise CalendarError("Sign-in expired; reconnect the calendar", needs_reauth=True)
        try:
            new = _token_request(
                "gmail" if self.conn.provider == "google" else "microsoft",
                {"grant_type": "refresh_token", "refresh_token": t["refresh_token"]},
                self.c,
                self.s,
            )
        except EmailProviderError as e:
            raise CalendarError(str(e), needs_reauth=True) from e
        new.setdefault("refresh_token", t["refresh_token"])
        self.conn.token_ciphertext = encrypt_tokens(new)
        self.conn.token_expires_at = datetime.fromisoformat(new["expires_at"])
        return str(new["access_token"])

    def _check(self, r: httpx.Response) -> dict[str, Any]:
        if r.status_code in (401, 403):
            raise CalendarError(f"Calendar access was refused ({r.status_code}); reconnect it", needs_reauth=True)
        if r.status_code == 429 or r.status_code >= 500:
            raise CalendarError(f"Calendar service busy ({r.status_code}); try again", transient=True)
        if r.status_code >= 400:
            raise CalendarError(f"Calendar error {r.status_code}: {r.text[:200]}")
        return r.json() if r.content else {}


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(v: str) -> datetime:
    d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


class GoogleCalendar(_OAuthCalendar):
    name = "google"

    def busy(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        r = self.c.post(
            f"{GCAL_API}/freeBusy",
            json={"timeMin": _iso(start), "timeMax": _iso(end), "items": [{"id": "primary"}]},
            headers={"Authorization": f"Bearer {self._token()}"},
        )
        d = self._check(r)
        return [(_parse(b["start"]), _parse(b["end"])) for b in d.get("calendars", {}).get("primary", {}).get("busy", [])]

    def create_event(self, ev: NewEvent) -> CreatedEvent:
        # A deterministic event id makes the insert idempotent: a retry gets 409 instead of a duplicate meeting.
        eid = base64.b32hexencode(hashlib.sha256(ev.idempotency_key.encode()).digest()).decode().lower().rstrip("=")[:40]
        body = {
            "id": eid,
            "summary": ev.title,
            "description": ev.description,
            "start": {"dateTime": _iso(ev.start), "timeZone": ev.timezone},
            "end": {"dateTime": _iso(ev.end), "timeZone": ev.timezone},
            "attendees": [{"email": a} for a in ev.attendees],
        }
        if ev.location:
            body["location"] = ev.location
        else:
            body["conferenceData"] = {"createRequest": {"requestId": ev.idempotency_key[:60]}}
        r = self.c.post(
            f"{GCAL_API}/calendars/primary/events",
            params={"sendUpdates": "all", "conferenceDataVersion": 1},
            json=body,
            headers={"Authorization": f"Bearer {self._token()}"},
        )
        if r.status_code == 409:
            got = self._check(
                self.c.get(f"{GCAL_API}/calendars/primary/events/{eid}", headers={"Authorization": f"Bearer {self._token()}"})
            )
        else:
            got = self._check(r)
        return CreatedEvent(got.get("id", eid), got.get("hangoutLink") or ev.location)

    def cancel_event(self, event_id: str) -> None:
        r = self.c.delete(
            f"{GCAL_API}/calendars/primary/events/{event_id}",
            params={"sendUpdates": "all"},
            headers={"Authorization": f"Bearer {self._token()}"},
        )
        if r.status_code not in (204, 404, 410):
            self._check(r)


class MicrosoftCalendar(_OAuthCalendar):
    name = "microsoft"

    def busy(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        r = self.c.post(
            f"{GRAPH_API}/me/calendar/getSchedule",
            json={
                "schedules": [self.conn.email_address],
                "startTime": {"dateTime": _iso(start)[:-1], "timeZone": "UTC"},
                "endTime": {"dateTime": _iso(end)[:-1], "timeZone": "UTC"},
                "availabilityViewInterval": 15,
            },
            headers={"Authorization": f"Bearer {self._token()}", "Prefer": 'outlook.timezone="UTC"'},
        )
        d = self._check(r)
        out = []
        for sched in d.get("value", []):
            for it in sched.get("scheduleItems", []):
                if it.get("status") in ("busy", "oof", "tentative", "workingElsewhere"):
                    out.append((_parse(it["start"]["dateTime"] + "Z"), _parse(it["end"]["dateTime"] + "Z")))
        return out

    def create_event(self, ev: NewEvent) -> CreatedEvent:
        body: dict[str, Any] = {
            "subject": ev.title,
            "body": {"contentType": "text", "content": ev.description},
            "start": {"dateTime": _iso(ev.start)[:-1], "timeZone": "UTC"},
            "end": {"dateTime": _iso(ev.end)[:-1], "timeZone": "UTC"},
            "attendees": [{"emailAddress": {"address": a}, "type": "required"} for a in ev.attendees],
            "transactionId": ev.idempotency_key,  # Graph de-duplicates creates with the same transactionId
        }
        if ev.location:
            body["location"] = {"displayName": ev.location}
        else:
            body["isOnlineMeeting"] = True
        d = self._check(self.c.post(f"{GRAPH_API}/me/events", json=body, headers={"Authorization": f"Bearer {self._token()}"}))
        return CreatedEvent(d["id"], (d.get("onlineMeeting") or {}).get("joinUrl") or ev.location)

    def cancel_event(self, event_id: str) -> None:
        r = self.c.post(
            f"{GRAPH_API}/me/events/{event_id}/cancel",
            json={"comment": "Cancelled"},
            headers={"Authorization": f"Bearer {self._token()}"},
        )
        if r.status_code not in (202, 204, 404):
            self._check(r)


def provider_for(db: Session, conn: CalendarConnection) -> CalendarProvider:
    if conn.provider == "mock":
        return MockCalendar(db, conn.email_address)
    if conn.provider == "google":
        return GoogleCalendar(db, conn)
    if conn.provider == "microsoft":
        return MicrosoftCalendar(db, conn)
    raise CalendarError(f"Unknown calendar provider {conn.provider}")
