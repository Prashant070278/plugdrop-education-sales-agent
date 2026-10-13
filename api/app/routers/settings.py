from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import delete, func, select

from app import audit
from app.config import get_settings
from app.deps import DB, AdminUser, CurrentUser
from app.models import (
    Activity,
    Campaign,
    ConsentRecord,
    Contact,
    Institution,
    Lead,
    Opportunity,
    ResearchFinding,
    Task,
)

router = APIRouter(prefix="/settings", tags=["settings"])


class Integration(BaseModel):
    key: str
    name: str
    description: str
    status: str  # "connected" | "not_configured" | "planned"
    env_vars: list[str]


class SystemInfo(BaseModel):
    environment: str
    database: str
    integrations: list[Integration]
    demo_counts: dict[str, int]


DEMO_MODELS: dict[str, Any] = {
    "institutions": Institution,
    "contacts": Contact,
    "leads": Lead,
    "opportunities": Opportunity,
    "activities": Activity,
    "research_findings": ResearchFinding,
    "tasks": Task,
    "campaigns": Campaign,
    "consent_records": ConsentRecord,
}


@router.get("/system", response_model=SystemInfo)
def system(_: CurrentUser, db: DB) -> SystemInfo:
    s = get_settings()

    def st(*vals: str | None) -> str:
        return "connected" if all(vals) else "not_configured"

    integrations = [
        Integration(
            key="email",
            name="Email mailboxes (Gmail / Microsoft 365)",
            description=(
                "OAuth sign-in for outreach mailboxes (Settings → Outreach). "
                + ("External sending is ON." if s.outreach_external_sending_allowed else "External sending is OFF: drafts only.")
            ),
            status="connected" if s.token_encryption_key and (s.google_client_id or s.microsoft_client_id) else "not_configured",
            env_vars=["TOKEN_ENCRYPTION_KEY", "GOOGLE_CLIENT_ID", "MICROSOFT_CLIENT_ID", "OUTREACH_EXTERNAL_SENDING_ALLOWED"],
        ),
        Integration(
            key="voice",
            name="PlugDrop Voice AI",
            description="Place and log AI voice calls.",
            status=st(s.voice_ai_api_key),
            env_vars=["VOICE_AI_API_KEY"],
        ),
        Integration(
            key="whatsapp",
            name="WhatsApp Business",
            description="Send WhatsApp follow-ups.",
            status=st(s.whatsapp_api_token),
            env_vars=["WHATSAPP_API_TOKEN"],
        ),
        Integration(
            key="calendar",
            name="Calendar",
            description="Book meetings from the lead page.",
            status=st(s.calendar_api_key),
            env_vars=["CALENDAR_API_KEY"],
        ),
        Integration(
            key="research",
            name=f"Research source: {s.research_provider}",
            description=(
                "Fictional demo data for .example institutions only. Add a licensed provider for real prospects."
                if s.research_provider == "mock"
                else "Licensed research data provider."
            ),
            status="connected",
            env_vars=["RESEARCH_PROVIDER"],
        ),
        Integration(
            key="claude",
            name="Claude API (research reasoning)",
            description="Summarises and classifies retrieved sources. Without a key, rule-based reasoning is used.",
            status=st(s.anthropic_api_key),
            env_vars=["ANTHROPIC_API_KEY", "ANTHROPIC_MODEL"],
        ),
    ]
    db_name = s.database_url.rsplit("/", 1)[-1].split("?")[0]
    counts = {
        k: int(db.scalar(select(func.count()).select_from(m).where(m.is_demo.is_(True))) or 0) for k, m in DEMO_MODELS.items()
    }
    return SystemInfo(environment=s.environment, database=db_name, integrations=integrations, demo_counts=counts)


@router.delete("/demo-data", response_model=dict[str, int])
def purge_demo(user: AdminUser, db: DB, request: Request) -> dict[str, int]:
    """Delete every row flagged is_demo. Order respects foreign keys."""
    removed: dict[str, int] = {}
    from app.models import ContentItem, EmailTemplate, OutreachSequence

    for m in (OutreachSequence, ContentItem, EmailTemplate):
        res0 = db.execute(delete(m).where(m.is_demo.is_(True)))
        removed[m.__tablename__] = res0.rowcount or 0
    for key in [
        "activities",
        "research_findings",
        "tasks",
        "consent_records",
        "opportunities",
        "leads",
        "contacts",
        "campaigns",
        "institutions",
    ]:
        m = DEMO_MODELS[key]
        res = db.execute(delete(m).where(m.is_demo.is_(True)))
        removed[key] = res.rowcount or 0
    audit.record(db, user, "purge_demo", "system", None, removed, request)
    db.commit()
    return removed
