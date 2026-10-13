"""Mock WhatsApp provider: messages are stored in ``mock_whatsapp`` and never leave the system.

Test numbers: ending in 0000 → 'not a WhatsApp user' (permanent failure); ending in 1111 → a temporary error on the
first attempt, then success.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import MockWhatsApp
from app.whatsapp.providers.base import OutgoingWa, WaProviderError, WaSendResult

_flaky_seen: set[str] = set()


class MockWaProvider:
    name = "mock"

    def __init__(self, db: Session, settings: Settings) -> None:
        self.db, self.s = db, settings

    def send(self, msg: OutgoingWa) -> WaSendResult:
        existing = self.db.scalar(select(MockWhatsApp).where(MockWhatsApp.idempotency_key == msg.idempotency_key))
        if existing:  # idempotent: the same message is never stored (sent) twice
            return WaSendResult(existing.provider_message_id)
        if msg.to.endswith("0000"):
            raise WaProviderError("Message failed: recipient is not a WhatsApp user", code="131026")
        if msg.to.endswith("1111") and msg.idempotency_key not in _flaky_seen:
            _flaky_seen.add(msg.idempotency_key)
            raise WaProviderError("Temporary error from the provider, retry later", transient=True, code="131000")
        pid = f"wamid.MOCK{uuid.uuid4().hex[:20]}"
        self.db.add(
            MockWhatsApp(
                to_number=msg.to,
                kind=msg.kind,
                template_name=msg.template_name,
                body=msg.body,
                provider_message_id=pid,
                idempotency_key=msg.idempotency_key,
            )
        )
        self.db.flush()
        return WaSendResult(pid)

    def submit_template(self, name: str, language: str, category: str, body: str, examples: list[str]) -> tuple[str, str]:
        if len(body) > 1024:
            return f"mock-tpl-{name}", "rejected"
        return f"mock-tpl-{name}-{language}", "approved"

    def template_statuses(self) -> dict[tuple[str, str], tuple[str, str | None]]:
        return {}

    def webhook_secret(self) -> str | None:
        return self.s.mock_webhook_secret
