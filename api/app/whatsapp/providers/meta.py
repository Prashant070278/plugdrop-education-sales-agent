"""WhatsApp Business Platform (Cloud API) provider — the official Meta API.

Needs WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_BUSINESS_ACCOUNT_ID, WHATSAPP_ACCESS_TOKEN (system-user token) and
WHATSAPP_APP_SECRET (webhook signatures). Sending also needs WHATSAPP_EXTERNAL_SENDING_ALLOWED=true.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings
from app.whatsapp.providers.base import OutgoingWa, WaProviderError, WaSendResult

# Cloud API error codes that are worth retrying (rate limits, temporary service problems).
TRANSIENT_CODES = {"4", "80007", "130429", "131000", "131016", "131048", "131056", "133004"}
REENGAGEMENT = "131047"  # more than 24 hours since the customer last replied: a template is required


def configured(s: Settings) -> bool:
    return bool(s.whatsapp_phone_number_id and s.whatsapp_access_token and s.whatsapp_business_account_id)


class MetaWaProvider:
    name = "meta"

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        if not configured(settings):
            raise WaProviderError(
                "WhatsApp Cloud API isn't configured (WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_BUSINESS_ACCOUNT_ID, WHATSAPP_ACCESS_TOKEN)"
            )
        self.s = settings
        self.c = client or httpx.Client(
            base_url=f"{settings.whatsapp_graph_base_url.rstrip('/')}/{settings.whatsapp_graph_version}", timeout=20.0
        )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.s.whatsapp_access_token}"}

    def _raise(self, r: httpx.Response) -> None:
        try:
            err = r.json().get("error", {})
        except ValueError:
            err = {}
        code = str(err.get("code", r.status_code))
        sub = str(err.get("error_subcode", ""))
        msg = err.get("error_user_msg") or err.get("message") or f"HTTP {r.status_code}"
        transient = r.status_code == 429 or r.status_code >= 500 or code in TRANSIENT_CODES or sub in TRANSIENT_CODES
        if code == REENGAGEMENT:
            msg = "The 24-hour window has closed: send an approved template instead"
        raise WaProviderError(f"WhatsApp error {code}: {msg}", transient=transient, code=code)

    def send(self, msg: OutgoingWa) -> WaSendResult:
        payload: dict[str, Any] = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": msg.to.lstrip("+")}
        if msg.kind == "template":
            payload |= {
                "type": "template",
                "template": {
                    "name": msg.template_name,
                    "language": {"code": msg.language},
                    "components": [{"type": "body", "parameters": [{"type": "text", "text": p} for p in msg.params]}]
                    if msg.params
                    else [],
                },
            }
        else:
            payload |= {"type": "text", "text": {"body": msg.body, "preview_url": False}}
        try:
            r = self.c.post(f"/{self.s.whatsapp_phone_number_id}/messages", json=payload, headers=self._headers())
        except httpx.HTTPError as e:
            raise WaProviderError(f"Network error: {e}", transient=True) from e
        if r.status_code >= 400:
            self._raise(r)
        return WaSendResult(r.json()["messages"][0]["id"])

    def submit_template(self, name: str, language: str, category: str, body: str, examples: list[str]) -> tuple[str, str]:
        comp: dict[str, Any] = {"type": "BODY", "text": body}
        if examples:
            comp["example"] = {"body_text": [examples]}
        r = self.c.post(
            f"/{self.s.whatsapp_business_account_id}/message_templates",
            json={"name": name, "language": language, "category": category.upper(), "components": [comp]},
            headers=self._headers(),
        )
        if r.status_code >= 400:
            self._raise(r)
        d = r.json()
        return str(d.get("id")), {"APPROVED": "approved", "REJECTED": "rejected", "PAUSED": "paused"}.get(
            str(d.get("status", "")).upper(), "submitted"
        )

    def template_statuses(self) -> dict[tuple[str, str], tuple[str, str | None]]:
        r = self.c.get(
            f"/{self.s.whatsapp_business_account_id}/message_templates",
            params={"fields": "name,language,status,rejected_reason", "limit": 200},
            headers=self._headers(),
        )
        if r.status_code >= 400:
            self._raise(r)
        out = {}
        for t in r.json().get("data", []):
            st = {"APPROVED": "approved", "REJECTED": "rejected", "PAUSED": "paused", "DISABLED": "disabled"}.get(
                str(t.get("status", "")).upper(), "submitted"
            )
            out[(t["name"], t.get("language", "en"))] = (st, t.get("rejected_reason"))
        return out

    def webhook_secret(self) -> str | None:
        return self.s.whatsapp_app_secret
