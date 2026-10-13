"""Database schema for the PlugDrop sales CRM.

Enumerations are stored as short strings guarded by CHECK constraints so that adding a
value is a simple migration. Every business table carries created/updated timestamps,
and demo rows are flagged with ``is_demo`` so they can be found and removed.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class StrEnum(str, enum.Enum):
    def __str__(self) -> str:
        return self.value

    @classmethod
    def values(cls) -> list[str]:
        return [m.value for m in cls]


class Role(StrEnum):
    admin = "admin"
    manager = "manager"
    rep = "rep"


class InstitutionType(StrEnum):
    university = "university"
    college = "college"
    university_group = "university_group"
    edtech = "edtech"
    online_education = "online_education"
    test_prep = "test_prep"
    vocational = "vocational"
    professional_education = "professional_education"
    other = "other"


class StudentSize(StrEnum):
    under_1k = "under_1k"
    s1k_5k = "1k_5k"
    s5k_20k = "5k_20k"
    s20k_50k = "20k_50k"
    over_50k = "over_50k"
    unknown = "unknown"


class VerificationStatus(StrEnum):
    unverified = "unverified"
    verified = "verified"
    bounced = "bounced"
    invalid = "invalid"


class LeadStage(StrEnum):
    new = "new"
    researched = "researched"
    qualified = "qualified"
    outreach_ready = "outreach_ready"
    contacted = "contacted"
    engaged = "engaged"
    meeting_booked = "meeting_booked"
    proposal = "proposal"
    negotiation = "negotiation"
    won = "won"
    lost = "lost"
    nurture = "nurture"


# Order of the forward-moving funnel (won/lost/nurture are outcomes, not steps).
FUNNEL: list[LeadStage] = [
    LeadStage.new,
    LeadStage.researched,
    LeadStage.qualified,
    LeadStage.outreach_ready,
    LeadStage.contacted,
    LeadStage.engaged,
    LeadStage.meeting_booked,
    LeadStage.proposal,
    LeadStage.negotiation,
    LeadStage.won,
]
CLOSED_STAGES = {LeadStage.won, LeadStage.lost}
OUTREACH_STAGES = {LeadStage.outreach_ready, LeadStage.contacted, LeadStage.engaged}


class OpportunityStage(StrEnum):
    discovery = "discovery"
    demo = "demo"
    proposal = "proposal"
    negotiation = "negotiation"
    won = "won"
    lost = "lost"


class Solution(StrEnum):
    voice_ai = "voice_ai"
    crm = "crm"
    chat = "chat"
    whatsapp = "whatsapp"
    automation = "automation"


class ActivityType(StrEnum):
    research = "research"
    call = "call"
    email = "email"
    reply = "reply"
    meeting = "meeting"
    task = "task"
    status_change = "status_change"
    note = "note"


OUTREACH_ACTIVITY = {ActivityType.call, ActivityType.email}


class TaskPriority(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"
    urgent = "urgent"


class TaskStatus(StrEnum):
    open = "open"
    done = "done"


class CampaignChannel(StrEnum):
    email = "email"
    call = "call"
    whatsapp = "whatsapp"
    linkedin = "linkedin"
    multi = "multi"


class CampaignStatus(StrEnum):
    draft = "draft"
    active = "active"
    paused = "paused"
    completed = "completed"


class ConsentChannel(StrEnum):
    all = "all"
    email = "email"
    call = "call"
    whatsapp = "whatsapp"
    sms = "sms"


class ConsentStatus(StrEnum):
    opted_out = "opted_out"
    suppressed = "suppressed"
    consented = "consented"


def _check(column: str, enum_cls: type[StrEnum], name: str | None = None) -> CheckConstraint:
    allowed = ", ".join(f"'{v}'" for v in enum_cls.values())
    return CheckConstraint(f"{column} IN ({allowed})", name=name or f"{column}_valid")


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        _check("role", Role),
        Index("uq_users_email_lower", func.lower(text("email")), unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default=Role.rep.value)
    hashed_password: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Institution(TimestampMixin, Base):
    __tablename__ = "institutions"
    __table_args__ = (
        _check("institution_type", InstitutionType),
        _check("student_size_range", StudentSize),
        # A website domain identifies an institution; two rows may not share one.
        Index("uq_institutions_domain", "domain", unique=True, postgresql_where=text("domain IS NOT NULL")),
        # Without a domain, the same normalised name in the same city+country is a duplicate.
        UniqueConstraint("normalized_name", "country", "city", name="uq_institutions_name_location"),
        Index("ix_institutions_country_type", "country", "institution_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    website: Mapped[str | None] = mapped_column(String(500))
    domain: Mapped[str | None] = mapped_column(String(255))
    institution_type: Mapped[str] = mapped_column(String(40), nullable=False, default=InstitutionType.other.value)
    country: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    city: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    student_size_range: Mapped[str] = mapped_column(String(20), nullable=False, default=StudentSize.unknown.value)
    courses: Mapped[list[str]] = mapped_column(ARRAY(String(200)), nullable=False, default=list)
    source: Mapped[str | None] = mapped_column(String(200))
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    campuses: Mapped[list[Campus]] = relationship(back_populates="institution", cascade="all, delete-orphan")
    contacts: Mapped[list[Contact]] = relationship(back_populates="institution")
    owner: Mapped[User | None] = relationship()


class Campus(TimestampMixin, Base):
    __tablename__ = "campuses"
    __table_args__ = (UniqueConstraint("institution_id", "name", name="uq_campuses_institution_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    institution_id: Mapped[int] = mapped_column(ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    city: Mapped[str | None] = mapped_column(String(100))
    country: Mapped[str | None] = mapped_column(String(100))
    address: Mapped[str | None] = mapped_column(String(500))

    institution: Mapped[Institution] = relationship(back_populates="campuses")


class Contact(TimestampMixin, Base):
    __tablename__ = "contacts"
    __table_args__ = (
        _check("verification_status", VerificationStatus),
        # Exact identifiers are unique; similar names are never treated as duplicates.
        Index("uq_contacts_email_lower", func.lower(text("email")), unique=True, postgresql_where=text("email IS NOT NULL")),
        Index("uq_contacts_linkedin", "linkedin_url", unique=True, postgresql_where=text("linkedin_url IS NOT NULL")),
        Index("ix_contacts_name", "last_name", "first_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    institution_id: Mapped[int] = mapped_column(ForeignKey("institutions.id", ondelete="RESTRICT"), nullable=False, index=True)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    designation: Mapped[str | None] = mapped_column(String(200))
    department: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(50))
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    verification_status: Mapped[str] = mapped_column(String(20), nullable=False, default=VerificationStatus.unverified.value)
    source: Mapped[str | None] = mapped_column(String(200))
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    institution: Mapped[Institution] = relationship(back_populates="contacts")

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class Lead(TimestampMixin, Base):
    __tablename__ = "leads"
    __table_args__ = (
        _check("stage", LeadStage),
        CheckConstraint("score BETWEEN 0 AND 100", name="score_range"),
        # One open lead per institution; closed (won/lost) leads may accumulate.
        Index(
            "uq_leads_open_per_institution",
            "institution_id",
            unique=True,
            postgresql_where=text("stage NOT IN ('won', 'lost')"),
        ),
        Index("ix_leads_stage_owner", "stage", "owner_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    institution_id: Mapped[int] = mapped_column(ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True)
    primary_contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"), index=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id", ondelete="SET NULL"), index=True)
    stage: Mapped[str] = mapped_column(String(30), nullable=False, default=LeadStage.new.value)
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str | None] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(Text)
    lost_reason: Mapped[str | None] = mapped_column(String(300))
    stage_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    institution: Mapped[Institution] = relationship()
    primary_contact: Mapped[Contact | None] = relationship()
    owner: Mapped[User | None] = relationship()


class Opportunity(TimestampMixin, Base):
    __tablename__ = "opportunities"
    __table_args__ = (
        _check("stage", OpportunityStage),
        CheckConstraint("estimated_value >= 0", name="value_non_negative"),
        Index("ix_opportunities_stage_owner", "stage", "owner_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    institution_id: Mapped[int] = mapped_column(ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    estimated_value: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    solution_interest: Mapped[list[str]] = mapped_column(ARRAY(String(30)), nullable=False, default=list)
    expected_decision_date: Mapped[date | None] = mapped_column(Date)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    stage: Mapped[str] = mapped_column(String(30), nullable=False, default=OpportunityStage.discovery.value)
    next_action: Mapped[str | None] = mapped_column(String(500))
    next_action_date: Mapped[date | None] = mapped_column(Date)
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    lead: Mapped[Lead] = relationship()
    institution: Mapped[Institution] = relationship()
    owner: Mapped[User | None] = relationship()


class Activity(Base):
    __tablename__ = "activities"
    __table_args__ = (
        _check("activity_type", ActivityType),
        Index("ix_activities_lead_time", "lead_id", "occurred_at"),
        Index("ix_activities_institution_time", "institution_id", "occurred_at"),
        Index("ix_activities_type_time", "activity_type", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    activity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    institution_id: Mapped[int | None] = mapped_column(ForeignKey("institutions.id", ondelete="CASCADE"))
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"), index=True)
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id", ondelete="SET NULL"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    user: Mapped[User | None] = relationship()
    contact: Mapped[Contact | None] = relationship()


class ResearchFinding(TimestampMixin, Base):
    __tablename__ = "research_findings"
    __table_args__ = (CheckConstraint("confidence BETWEEN 0 AND 100", name="confidence_range"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    institution_id: Mapped[int] = mapped_column(ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"), index=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"), index=True)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1000))
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    found_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        _check("priority", TaskPriority),
        _check("status", TaskStatus),
        Index("ix_tasks_owner_status_due", "owner_id", "status", "due_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminder_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    priority: Mapped[str] = mapped_column(String(10), nullable=False, default=TaskPriority.medium.value)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=TaskStatus.open.value)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    institution_id: Mapped[int | None] = mapped_column(ForeignKey("institutions.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"))
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id", ondelete="SET NULL"))
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    owner: Mapped[User | None] = relationship()


class Campaign(TimestampMixin, Base):
    __tablename__ = "campaigns"
    __table_args__ = (
        _check("channel", CampaignChannel),
        _check("status", CampaignStatus),
        UniqueConstraint("name", name="uq_campaigns_name"),
        CheckConstraint("end_date IS NULL OR start_date IS NULL OR end_date >= start_date", name="dates_ordered"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(String(20), nullable=False, default=CampaignChannel.email.value)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=CampaignStatus.draft.value)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ConsentRecord(Base):
    """Opt-outs, suppressions and recorded consent. Matches a contact, an email, or a whole domain."""

    __tablename__ = "consent_records"
    __table_args__ = (
        _check("channel", ConsentChannel),
        _check("status", ConsentStatus),
        CheckConstraint(
            "contact_id IS NOT NULL OR email IS NOT NULL OR domain IS NOT NULL OR phone IS NOT NULL",
            name="has_subject",
        ),
        Index("ix_consent_email_lower", func.lower(text("email"))),
        Index("ix_consent_domain", "domain"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), index=True)
    email: Mapped[str | None] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(50))
    domain: Mapped[str | None] = mapped_column(String(255))
    channel: Mapped[str] = mapped_column(String(20), nullable=False, default=ConsentChannel.all.value)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ConsentStatus.opted_out.value)
    reason: Mapped[str | None] = mapped_column(String(500))
    source: Mapped[str | None] = mapped_column(String(200))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    recorded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_entity", "entity_type", "entity_id", "created_at"),
        Index("ix_audit_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[int | None] = mapped_column(Integer)
    changes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    actor: Mapped[User | None] = relationship()
