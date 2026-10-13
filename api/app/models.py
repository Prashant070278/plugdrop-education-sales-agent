"""Database schema for the PlugDrop sales CRM.

Enumerations are stored as short strings guarded by CHECK constraints so that adding a
value is a simple migration. Every business table carries created/updated timestamps,
and demo rows are flagged with ``is_demo`` so they can be found and removed.
"""

from __future__ import annotations

import enum
import re
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
    whatsapp = "whatsapp"
    linkedin = "linkedin"


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
    # Latest automated research result (Stage 2); detail lives in research_runs.
    research_score: Mapped[int | None] = mapped_column(Integer)
    research_tier: Mapped[str | None] = mapped_column(String(1))
    last_researched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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


ROLE_NAME = re.compile(
    r"\b(?:office|team|cell|desk|helpdesk|help desk|centre|center|department|admissions?|registrar|director|chancellor"
    r"|university|college|principal|dean|head|in-?charge|manager|officer|secretary|coordinator|enquir(?:y|ies))\b",
    re.I,
)


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
    salutation: Mapped[str | None] = mapped_column(String(30))  # e.g. "Prof.", "Dr." - used in "Dear Prof. Sharma"
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    designation: Mapped[str | None] = mapped_column(String(200))
    department: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(50))
    # WhatsApp number in international format (+91…). Messages need recorded opt-in (a 'consented' consent record).
    whatsapp_number: Mapped[str | None] = mapped_column(String(20), index=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    verification_status: Mapped[str] = mapped_column(String(20), nullable=False, default=VerificationStatus.unverified.value)
    source: Mapped[str | None] = mapped_column(String(200))
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    institution: Mapped[Institution] = relationship(back_populates="contacts")

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def greeting_name(self) -> str:
        """How an email addresses this person: "Prof. Chopade" with a salutation, "Sir/Madam" for an office or role
        mailbox (e.g. "Admissions Office"), otherwise the first name."""
        if self.salutation and self.last_name:
            return f"{self.salutation} {self.last_name}"
        if not self.last_name and ROLE_NAME.search(self.first_name):
            return "Sir/Madam"
        return self.first_name


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
    # Set when the finding came from an approved research-agent evidence item (Stage 2).
    evidence_level: Mapped[str | None] = mapped_column(String(30))
    excerpt: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    research_evidence_id: Mapped[int | None] = mapped_column(ForeignKey("research_evidence.id", ondelete="SET NULL"), unique=True)


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
    # Stage 4: why the task exists and which activity created it. auto_key makes rule-created tasks idempotent.
    reason: Mapped[str | None] = mapped_column(String(40))
    source_activity_id: Mapped[int | None] = mapped_column(ForeignKey("activities.id", ondelete="SET NULL"))
    meeting_id: Mapped[int | None] = mapped_column(ForeignKey("meetings.id", ondelete="SET NULL"), index=True)
    auto_key: Mapped[str | None] = mapped_column(String(160), unique=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    owner: Mapped[User | None] = relationship()
    source_activity: Mapped[Activity | None] = relationship(foreign_keys=[source_activity_id])

    @property
    def source_activity_subject(self) -> str | None:
        return self.source_activity.subject if self.source_activity else None


class TaskReason(StrEnum):
    manual = "manual"
    reply_needed = "reply_needed"  # a prospect replied and needs an answer
    interested_no_meeting = "interested_no_meeting"
    meeting_approval = "meeting_approval"
    meeting_confirmation = "meeting_confirmation"
    post_meeting = "post_meeting"
    proposal_follow_up = "proposal_follow_up"
    reconnect_later = "reconnect_later"
    whatsapp_handoff = "whatsapp_handoff"


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


# ---------------------------------------------------------------------------
# Stage 2: AI prospect research agent
# ---------------------------------------------------------------------------


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    completed = "completed"
    partial = "partial"  # finished, some items failed
    failed = "failed"
    cancelled = "cancelled"


class RunStatus(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class EvidenceLevel(StrEnum):
    verified_fact = "verified_fact"  # stated directly in a retrieved source; excerpt checked against source text
    source_inference = "source_inference"  # reasonable inference from a cited source
    hypothesis = "hypothesis"  # not supported by a source; never scores


class EvidenceKind(StrEnum):
    profile = "profile"
    signal = "signal"
    use_case = "use_case"


class ReviewStatus(StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class EmailStatus(StrEnum):
    none = "none"  # no email found
    published = "published"  # shown on a cited public business page
    provider_verified = "provider_verified"  # a licensed provider reports it as verified


class ResearchJob(Base):
    __tablename__ = "research_jobs"
    __table_args__ = (_check("status", JobStatus), Index("ix_research_jobs_status_created", "status", "created_at"))

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=JobStatus.queued.value)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    reasoner: Mapped[str] = mapped_column(String(50), nullable=False)
    requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    total_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    requested_by: Mapped[User | None] = relationship()
    runs: Mapped[list[ResearchRun]] = relationship(back_populates="job", order_by="ResearchRun.id")


class ResearchRun(Base):
    """One institution researched within a job. Holds status, retries, cost, log and the scored result."""

    __tablename__ = "research_runs"
    __table_args__ = (
        _check("status", RunStatus),
        CheckConstraint("tier IS NULL OR tier IN ('A', 'B', 'C', 'D')", name="tier_valid"),
        CheckConstraint("score IS NULL OR score BETWEEN 0 AND 100", name="score_range"),
        Index("ix_research_runs_queue", "status", "next_attempt_at"),
        Index("ix_research_runs_institution_finished", "institution_id", "finished_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("research_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    institution_id: Mapped[int] = mapped_column(ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=RunStatus.queued.value)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("0"))
    log: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    # Result
    score: Mapped[int | None] = mapped_column(Integer)
    tier: Mapped[str | None] = mapped_column(String(1))
    confidence: Mapped[str | None] = mapped_column(String(10))
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    weights_used: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job: Mapped[ResearchJob] = relationship(back_populates="runs")
    institution: Mapped[Institution] = relationship()
    evidence: Mapped[list[ResearchEvidence]] = relationship(back_populates="run", order_by="ResearchEvidence.id")
    candidates: Mapped[list[ResearchContactCandidate]] = relationship(
        back_populates="run", order_by="ResearchContactCandidate.id"
    )


class ResearchEvidence(Base):
    __tablename__ = "research_evidence"
    __table_args__ = (
        _check("kind", EvidenceKind),
        _check("evidence_level", EvidenceLevel),
        _check("review_status", ReviewStatus),
        CheckConstraint("confidence BETWEEN 0 AND 100", name="confidence_range"),
        CheckConstraint("evidence_level = 'hypothesis' OR source_url IS NOT NULL", name="sourced_unless_hypothesis"),
        Index("ix_research_evidence_run", "run_id"),
        Index("ix_research_evidence_institution", "institution_id", "review_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False)
    institution_id: Mapped[int] = mapped_column(ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_level: Mapped[str] = mapped_column(String(30), nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    excerpt: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(1000))
    source_title: Mapped[str | None] = mapped_column(String(500))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    review_status: Mapped[str] = mapped_column(String(20), nullable=False, default=ReviewStatus.pending.value)
    review_note: Mapped[str | None] = mapped_column(String(500))
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[ResearchRun] = relationship(back_populates="evidence")
    reviewed_by: Mapped[User | None] = relationship()


class ResearchContactCandidate(Base):
    """A person found by a provider. Never written to contacts until a human approves and pushes it."""

    __tablename__ = "research_contact_candidates"
    __table_args__ = (
        _check("email_status", EmailStatus),
        _check("verification_status", VerificationStatus),
        _check("review_status", ReviewStatus),
        CheckConstraint("email IS NULL OR email_status <> 'none'", name="email_has_status"),
        CheckConstraint("source_url IS NOT NULL", name="has_source"),
        Index("ix_research_candidates_run", "run_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False)
    institution_id: Mapped[int] = mapped_column(ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False)
    salutation: Mapped[str | None] = mapped_column(String(30))
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    designation: Mapped[str | None] = mapped_column(String(200))
    department: Mapped[str | None] = mapped_column(String(200))
    buying_role: Mapped[str | None] = mapped_column(String(50))
    email: Mapped[str | None] = mapped_column(String(254))
    email_status: Mapped[str] = mapped_column(String(20), nullable=False, default=EmailStatus.none.value)
    phone: Mapped[str | None] = mapped_column(String(50))
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    verification_status: Mapped[str] = mapped_column(String(20), nullable=False, default=VerificationStatus.unverified.value)
    source_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    excerpt: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    notes: Mapped[str | None] = mapped_column(String(500))
    existing_contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"))
    review_status: Mapped[str] = mapped_column(String(20), nullable=False, default=ReviewStatus.pending.value)
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pushed_contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"))
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[ResearchRun] = relationship(back_populates="candidates")

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class AppSetting(Base):
    """Small key/value store for configurable settings (e.g. research scoring weights)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# Stage 3: AI outreach agent
# ---------------------------------------------------------------------------


class MailboxProvider(StrEnum):
    mock = "mock"
    gmail = "gmail"
    microsoft = "microsoft"


class MailboxStatus(StrEnum):
    pending_approval = "pending_approval"  # connected, waiting for an admin to approve its use
    active = "active"
    needs_reauth = "needs_reauth"
    disabled = "disabled"


class SequenceStatus(StrEnum):
    draft = "draft"  # drafts generated, nothing approved yet
    active = "active"  # at least one step approved/scheduled/sent
    stopped = "stopped"  # see stop_reason
    completed = "completed"  # every step sent


class StopReason(StrEnum):
    replied = "replied"
    opted_out = "opted_out"
    bounced = "bounced"
    suppressed = "suppressed"
    not_a_fit = "not_a_fit"
    cancelled = "cancelled"
    lead_closed = "lead_closed"


class MessageStatus(StrEnum):
    draft = "draft"
    approved = "approved"
    scheduled = "scheduled"
    sending = "sending"
    drafted_in_mailbox = "drafted_in_mailbox"  # draft-only mode: placed in the mailbox for a person to send
    sent = "sent"
    delivered = "delivered"
    bounced = "bounced"
    failed = "failed"
    cancelled = "cancelled"
    blocked = "blocked"  # pre-send check refused (suppression, limits, …)


class ReplyLabel(StrEnum):
    interested = "interested"
    meeting_request = "meeting_request"
    needs_information = "needs_information"
    not_now = "not_now"
    not_interested = "not_interested"
    referral = "referral"
    out_of_office = "out_of_office"
    unclear = "unclear"


class ContentKind(StrEnum):
    case_study = "case_study"
    claim = "claim"
    cta = "cta"
    value_prop = "value_prop"


class ContentStatus(StrEnum):
    draft = "draft"
    approved = "approved"
    retired = "retired"


class Mailbox(TimestampMixin, Base):
    """A connected sending mailbox. OAuth tokens are stored encrypted; passwords are never stored."""

    __tablename__ = "mailboxes"
    __table_args__ = (
        _check("provider", MailboxProvider),
        _check("status", MailboxStatus),
        Index("uq_mailboxes_provider_email", "provider", func.lower(text("email_address")), unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    email_address: Mapped[str] = mapped_column(String(254), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    connected_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    authorization_note: Mapped[str] = mapped_column(String(500), nullable=False)
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=MailboxStatus.pending_approval.value)
    sending_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    daily_send_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=40)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String(200)), nullable=False, default=list)
    token_ciphertext: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_cursor: Mapped[str | None] = mapped_column(String(500))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))

    owner: Mapped[User | None] = relationship(foreign_keys=[owner_id])


class ContentItem(TimestampMixin, Base):
    """Approved case studies, claims and calls to action the writer may use. Nothing else may be claimed."""

    __tablename__ = "content_items"
    __table_args__ = (_check("kind", ContentKind), _check("status", ContentStatus))

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    use_cases: Mapped[list[str]] = mapped_column(ARRAY(String(50)), nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ContentStatus.draft.value)
    source_note: Mapped[str | None] = mapped_column(String(500))
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class EmailTemplate(TimestampMixin, Base):
    __tablename__ = "email_templates"
    __table_args__ = (
        CheckConstraint("step BETWEEN 1 AND 10", name="step_range"),
        _check("status", ContentStatus),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    step: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    use_case: Mapped[str | None] = mapped_column(String(50))
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ContentStatus.draft.value)
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class OutreachSequence(TimestampMixin, Base):
    __tablename__ = "outreach_sequences"
    __table_args__ = (
        _check("status", SequenceStatus),
        CheckConstraint(
            "stop_reason IS NULL OR stop_reason IN ('replied','opted_out','bounced','suppressed','not_a_fit','cancelled','lead_closed')",
            name="stop_reason_valid",
        ),
        # One live sequence per contact at a time.
        Index("uq_sequences_live_contact", "contact_id", unique=True, postgresql_where=text("status IN ('draft', 'active')")),
        Index("ix_sequences_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    institution_id: Mapped[int] = mapped_column(ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    research_run_id: Mapped[int | None] = mapped_column(ForeignKey("research_runs.id", ondelete="SET NULL"))
    mailbox_id: Mapped[int | None] = mapped_column(ForeignKey("mailboxes.id", ondelete="SET NULL"))
    use_case: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=SequenceStatus.draft.value)
    stop_reason: Mapped[str | None] = mapped_column(String(20))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    spacing_days: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False, default=list)
    facts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    institution: Mapped[Institution] = relationship()
    contact: Mapped[Contact] = relationship()
    lead: Mapped[Lead] = relationship()
    mailbox: Mapped[Mailbox | None] = relationship()
    messages: Mapped[list[OutreachMessage]] = relationship(back_populates="sequence", order_by="OutreachMessage.step")
    replies: Mapped[list[EmailReply]] = relationship(back_populates="sequence", order_by="EmailReply.received_at")


class OutreachMessage(TimestampMixin, Base):
    __tablename__ = "outreach_messages"
    __table_args__ = (
        _check("status", MessageStatus),
        # Duplicate-send protection: one message per step per sequence, and a unique idempotency key.
        UniqueConstraint("sequence_id", "step", "kind", name="uq_outreach_messages_step"),
        UniqueConstraint("idempotency_key", name="uq_outreach_messages_idempotency"),
        Index("ix_outreach_messages_due", "status", "scheduled_for"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sequence_id: Mapped[int] = mapped_column(ForeignKey("outreach_sequences.id", ondelete="CASCADE"), nullable=False, index=True)
    step: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="sequence")  # sequence | reply
    status: Mapped[str] = mapped_column(String(25), nullable=False, default=MessageStatus.draft.value)
    to_email: Mapped[str] = mapped_column(String(254), nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    generated_by: Mapped[str] = mapped_column(String(30), nullable=False)
    generation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)  # facts/content used, rationale
    quality: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)  # last quality-check result
    edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    planned_offset_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider: Mapped[str | None] = mapped_column(String(20))
    provider_message_id: Mapped[str | None] = mapped_column(String(300))
    thread_id: Mapped[str | None] = mapped_column(String(300))
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    send_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500))

    sequence: Mapped[OutreachSequence] = relationship(back_populates="messages")


class EmailReply(Base):
    __tablename__ = "email_replies"
    __table_args__ = (
        CheckConstraint(
            "label IN ('interested','meeting_request','needs_information','not_now','not_interested','referral','out_of_office','unclear')",
            name="label_valid",
        ),
        UniqueConstraint("provider_message_id", name="uq_email_replies_provider_message"),
        Index("ix_email_replies_attention", "handled", "received_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sequence_id: Mapped[int | None] = mapped_column(ForeignKey("outreach_sequences.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("outreach_messages.id", ondelete="SET NULL"))
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"))
    mailbox_id: Mapped[int | None] = mapped_column(ForeignKey("mailboxes.id", ondelete="SET NULL"))
    provider_message_id: Mapped[str] = mapped_column(String(300), nullable=False)
    from_email: Mapped[str] = mapped_column(String(254), nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    label: Mapped[str] = mapped_column(String(30), nullable=False, default=ReplyLabel.unclear.value)
    label_confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    label_source: Mapped[str] = mapped_column(String(20), nullable=False, default="rules")  # rules | claude | human
    opt_out: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    summary: Mapped[str | None] = mapped_column(String(500))
    handled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    handled_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    sequence: Mapped[OutreachSequence | None] = relationship(back_populates="replies")


class EmailEvent(Base):
    """Append-only log of every outreach action and delivery result."""

    __tablename__ = "email_events"
    __table_args__ = (Index("ix_email_events_sequence", "sequence_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sequence_id: Mapped[int | None] = mapped_column(ForeignKey("outreach_sequences.id", ondelete="CASCADE"))
    message_id: Mapped[int | None] = mapped_column(ForeignKey("outreach_messages.id", ondelete="SET NULL"))
    event: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    actor: Mapped[User | None] = relationship()


class MockMail(Base):
    """Mailbox used by the mock email provider (tests and demos). Nothing here ever leaves the system."""

    __tablename__ = "mock_mail"

    id: Mapped[int] = mapped_column(primary_key=True)
    mailbox_id: Mapped[int] = mapped_column(ForeignKey("mailboxes.id", ondelete="CASCADE"), nullable=False, index=True)
    folder: Mapped[str] = mapped_column(String(20), nullable=False)  # drafts | sent | inbox
    message_id: Mapped[str] = mapped_column(String(300), nullable=False, unique=True)
    thread_id: Mapped[str] = mapped_column(String(300), nullable=False)
    in_reply_to: Mapped[str | None] = mapped_column(String(300))
    from_email: Mapped[str] = mapped_column(String(254), nullable=False)
    to_email: Mapped[str] = mapped_column(String(254), nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    delivery: Mapped[str | None] = mapped_column(String(20))  # delivered | bounced
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# ---------------------------------------------------------------------------
# Stage 4: WhatsApp, meetings, LinkedIn assistant
# ---------------------------------------------------------------------------


class WaTemplateStatus(StrEnum):
    draft = "draft"  # written here, not yet submitted to WhatsApp
    submitted = "submitted"  # waiting for WhatsApp (Meta) review
    approved = "approved"  # approved by WhatsApp: may be sent
    rejected = "rejected"
    paused = "paused"
    disabled = "disabled"


class WaTemplate(TimestampMixin, Base):
    """A WhatsApp message template. Outside the 24-hour customer-service window only approved templates may be sent."""

    __tablename__ = "wa_templates"
    __table_args__ = (
        _check("status", WaTemplateStatus),
        CheckConstraint("category IN ('marketing', 'utility')", name="category_valid"),
        UniqueConstraint("name", "language", name="uq_wa_templates_name_language"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # lower_snake_case, as WhatsApp requires
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="en")
    category: Mapped[str] = mapped_column(String(20), nullable=False, default="marketing")
    body: Mapped[str] = mapped_column(Text, nullable=False)  # uses {{1}}, {{2}} …
    params: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)  # [{"label","source"}]
    purpose: Mapped[str] = mapped_column(String(30), nullable=False, default="first_contact")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=WaTemplateStatus.draft.value)
    provider_template_id: Mapped[str | None] = mapped_column(String(100))
    rejection_reason: Mapped[str | None] = mapped_column(String(500))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class WaConversationState(StrEnum):
    open = "open"  # handled with suggestions; a person approves every reply
    handoff = "handoff"  # a named person owns it; automation stays out
    closed = "closed"


class WaConversation(TimestampMixin, Base):
    __tablename__ = "wa_conversations"
    __table_args__ = (_check("state", WaConversationState),)

    id: Mapped[int] = mapped_column(primary_key=True)
    wa_number: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"), index=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"), index=True)
    institution_id: Mapped[int | None] = mapped_column(ForeignKey("institutions.id", ondelete="SET NULL"))
    profile_name: Mapped[str | None] = mapped_column(String(200))
    state: Mapped[str] = mapped_column(String(20), nullable=False, default=WaConversationState.open.value)
    assigned_to_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    handoff_reason: Mapped[str | None] = mapped_column(String(300))
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_outbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unread: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    contact: Mapped[Contact | None] = relationship()
    assigned_to: Mapped[User | None] = relationship()
    messages: Mapped[list[WaMessage]] = relationship(back_populates="conversation", order_by="WaMessage.id")


class WaMessageStatus(StrEnum):
    draft = "draft"
    approved = "approved"
    queued = "queued"
    sending = "sending"
    sent = "sent"
    delivered = "delivered"
    read = "read"
    failed = "failed"
    blocked = "blocked"
    cancelled = "cancelled"
    received = "received"


class WaMessage(Base):
    __tablename__ = "wa_messages"
    __table_args__ = (
        _check("status", WaMessageStatus),
        CheckConstraint("direction IN ('in', 'out')", name="direction_valid"),
        CheckConstraint("kind IN ('template', 'text')", name="kind_valid"),
        Index("ix_wa_messages_conversation", "conversation_id", "created_at"),
        Index("ix_wa_messages_queue", "status", "scheduled_for"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("wa_conversations.id", ondelete="CASCADE"), nullable=False)
    direction: Mapped[str] = mapped_column(String(3), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default="text")
    template_id: Mapped[int | None] = mapped_column(ForeignKey("wa_templates.id", ondelete="SET NULL"))
    params: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    purpose: Mapped[str | None] = mapped_column(String(30))  # first_contact | reply | meeting | follow_up
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=WaMessageStatus.draft.value)
    provider_message_id: Mapped[str | None] = mapped_column(String(200), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    error: Mapped[str | None] = mapped_column(String(500))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    label: Mapped[str | None] = mapped_column(String(30))
    label_confidence: Mapped[int | None] = mapped_column(Integer)
    opt_out: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    suggestion: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    conversation: Mapped[WaConversation] = relationship(back_populates="messages")
    template: Mapped[WaTemplate | None] = relationship()


class WebhookEvent(Base):
    """Every verified webhook call, kept for de-duplication and troubleshooting."""

    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    event_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error: Mapped[str | None] = mapped_column(String(500))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MockWhatsApp(Base):
    """Outbox of the mock WhatsApp provider (tests and demos). Nothing here ever leaves the system."""

    __tablename__ = "mock_whatsapp"

    id: Mapped[int] = mapped_column(primary_key=True)
    to_number: Mapped[str] = mapped_column(String(20), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    template_name: Mapped[str | None] = mapped_column(String(100))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    provider_message_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CalendarConnection(TimestampMixin, Base):
    """A person's calendar, connected with OAuth (free/busy + create events). An admin approves it before use."""

    __tablename__ = "calendar_connections"
    __table_args__ = (
        CheckConstraint("provider IN ('mock', 'google', 'microsoft')", name="provider_valid"),
        _check("status", MailboxStatus),
        UniqueConstraint("user_id", "provider", name="uq_calendar_user_provider"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    email_address: Mapped[str] = mapped_column(String(254), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=MailboxStatus.pending_approval.value)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String(200)), nullable=False, default=list)
    token_ciphertext: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))

    user: Mapped[User] = relationship(foreign_keys=[user_id])


class MockCalendarEvent(Base):
    """Events in the mock calendar provider (tests and demos)."""

    __tablename__ = "mock_calendar_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_email: Mapped[str] = mapped_column(String(254), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attendees: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True)


class MeetingStatus(StrEnum):
    proposed = "proposed"  # times and a reply drafted; nothing booked
    awaiting_approval = "awaiting_approval"  # a time is chosen; needs approval before the calendar event is created
    scheduled = "scheduled"  # calendar event created and invitation sent
    confirmed = "confirmed"  # the prospect confirmed
    completed = "completed"
    cancelled = "cancelled"
    no_show = "no_show"


class Meeting(TimestampMixin, Base):
    __tablename__ = "meetings"
    __table_args__ = (
        _check("status", MeetingStatus),
        CheckConstraint("source IN ('email_reply', 'whatsapp', 'manual')", name="source_valid"),
        CheckConstraint("outcome IS NULL OR outcome IN ('positive', 'neutral', 'negative', 'no_show')", name="outcome_valid"),
        Index("ix_meetings_status_start", "status", "start_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    institution_id: Mapped[int] = mapped_column(ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"), index=True)
    organizer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    source_activity_id: Mapped[int | None] = mapped_column(ForeignKey("activities.id", ondelete="SET NULL"))
    email_reply_id: Mapped[int | None] = mapped_column(ForeignKey("email_replies.id", ondelete="SET NULL"))
    wa_message_id: Mapped[int | None] = mapped_column(ForeignKey("wa_messages.id", ondelete="SET NULL"))
    intent: Mapped[str | None] = mapped_column(String(30))
    requested_text: Mapped[str | None] = mapped_column(String(500))  # what the prospect said about timing
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=MeetingStatus.proposed.value)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    agenda: Mapped[str] = mapped_column(Text, nullable=False, default="")
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    timezone: Mapped[str] = mapped_column(String(60), nullable=False, default="Asia/Kolkata")
    proposed_slots: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    location: Mapped[str | None] = mapped_column(String(500))
    attendees: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    calendar_connection_id: Mapped[int | None] = mapped_column(ForeignKey("calendar_connections.id", ondelete="SET NULL"))
    calendar_event_id: Mapped[str | None] = mapped_column(String(300))
    idempotency_key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(String(20))
    next_steps: Mapped[str | None] = mapped_column(Text)
    draft_email_id: Mapped[int | None] = mapped_column(ForeignKey("outreach_messages.id", ondelete="SET NULL"))
    draft_wa_id: Mapped[int | None] = mapped_column(ForeignKey("wa_messages.id", ondelete="SET NULL"))
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    lead: Mapped[Lead] = relationship()
    institution: Mapped[Institution] = relationship()
    contact: Mapped[Contact | None] = relationship()
    organizer: Mapped[User | None] = relationship(foreign_keys=[organizer_id])


class LinkedinItem(TimestampMixin, Base):
    """LinkedIn assistance only: research notes, message drafts and content ideas. Nothing is sent automatically;
    a person copies the text into LinkedIn and marks it done."""

    __tablename__ = "linkedin_items"
    __table_args__ = (
        CheckConstraint("kind IN ('note', 'connection_draft', 'message_draft', 'content_suggestion')", name="kind_valid"),
        CheckConstraint("status IN ('draft', 'approved', 'done', 'discarded')", name="status_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), index=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"), index=True)
    institution_id: Mapped[int | None] = mapped_column(ForeignKey("institutions.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    facts_used: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    quality: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    generated_by: Mapped[str] = mapped_column(String(20), nullable=False, default="rules")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
