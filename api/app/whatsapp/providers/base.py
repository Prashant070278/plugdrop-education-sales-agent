"""WhatsApp provider interface. Implementations: ``mock`` (tests/demos) and ``meta`` (WhatsApp Business Platform
Cloud API). Only the official platform is supported: no personal-account automation, no unofficial gateways.

Both providers speak the Cloud API webhook format, so inbound messages and delivery statuses go through the same
parsing and processing code whichever provider is active.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class OutgoingWa:
    to: str  # E.164, e.g. +919812345678
    kind: str  # template | text
    body: str  # rendered text (for logs/mock); the template itself is rendered by WhatsApp
    idempotency_key: str
    template_name: str | None = None
    language: str = "en"
    params: tuple[str, ...] = ()


@dataclass(frozen=True)
class WaSendResult:
    provider_message_id: str


class WaProviderError(Exception):
    def __init__(self, message: str, *, transient: bool = False, code: str | None = None) -> None:
        super().__init__(message)
        self.transient = transient
        self.code = code


@dataclass(frozen=True)
class InboundWa:
    provider_message_id: str
    from_number: str
    text: str
    received_at: datetime
    profile_name: str | None = None
    context_id: str | None = None  # the message they replied to, if any


@dataclass(frozen=True)
class StatusUpdate:
    provider_message_id: str
    status: str  # sent | delivered | read | failed
    at: datetime
    error: str | None = None
    error_code: str | None = None


@dataclass
class WebhookBatch:
    messages: list[InboundWa] = field(default_factory=list)
    statuses: list[StatusUpdate] = field(default_factory=list)
    template_updates: list[dict[str, Any]] = field(default_factory=list)


class WaProvider(Protocol):
    name: str

    def send(self, msg: OutgoingWa) -> WaSendResult: ...

    def submit_template(self, name: str, language: str, category: str, body: str, examples: list[str]) -> tuple[str, str]: ...

    def template_statuses(self) -> dict[tuple[str, str], tuple[str, str | None]]: ...

    def webhook_secret(self) -> str | None: ...


def signature_valid(secret: str | None, raw_body: bytes, header: str | None) -> bool:
    """X-Hub-Signature-256: 'sha256=' + HMAC-SHA256(app secret, raw request body). Constant-time comparison."""
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def sign(secret: str, raw_body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()


def _ts(v: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(v), UTC)
    except (TypeError, ValueError):
        return datetime.now(UTC)


def parse_webhook(payload: dict[str, Any]) -> WebhookBatch:
    """Parse a Cloud API webhook payload (entry → changes → value)."""
    out = WebhookBatch()
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            if change.get("field") == "message_template_status_update":
                out.template_updates.append(value)
                continue
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name") for c in value.get("contacts", []) or []}
            for m in value.get("messages", []) or []:
                typ = m.get("type")
                if typ == "text":
                    text = (m.get("text") or {}).get("body", "")
                elif typ == "button":
                    text = (m.get("button") or {}).get("text", "")
                elif typ == "interactive":
                    i = m.get("interactive") or {}
                    text = (i.get("button_reply") or i.get("list_reply") or {}).get("title", "")
                else:
                    text = f"[{typ} message — open WhatsApp to view]"
                frm = str(m.get("from", ""))
                out.messages.append(
                    InboundWa(
                        provider_message_id=m.get("id", ""),
                        from_number=frm if frm.startswith("+") else f"+{frm}",
                        text=text[:4000],
                        received_at=_ts(m.get("timestamp")),
                        profile_name=names.get(frm),
                        context_id=(m.get("context") or {}).get("id"),
                    )
                )
            for s in value.get("statuses", []) or []:
                err = (s.get("errors") or [{}])[0]
                out.statuses.append(
                    StatusUpdate(
                        provider_message_id=s.get("id", ""),
                        status=s.get("status", ""),
                        at=_ts(s.get("timestamp")),
                        error=err.get("title") or err.get("message"),
                        error_code=str(err["code"]) if err.get("code") else None,
                    )
                )
    return out


def build_inbound_payload(
    phone_number_id: str, from_number: str, text: str, msg_id: str, name: str | None, ts: int
) -> dict[str, Any]:
    wa_id = from_number.lstrip("+")
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "mock-waba",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": phone_number_id},
                            "contacts": [{"wa_id": wa_id, "profile": {"name": name or ""}}],
                            "messages": [
                                {"from": wa_id, "id": msg_id, "timestamp": str(ts), "type": "text", "text": {"body": text}}
                            ],
                        },
                    }
                ],
            }
        ],
    }


def build_status_payload(
    msg_id: str, status: str, ts: int, error_code: str | None = None, error: str | None = None
) -> dict[str, Any]:
    st: dict[str, Any] = {"id": msg_id, "status": status, "timestamp": str(ts)}
    if error_code:
        st["errors"] = [{"code": int(error_code), "title": error or "Error"}]
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "mock-waba", "changes": [{"field": "messages", "value": {"statuses": [st]}}]}],
    }
