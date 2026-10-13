"""Request and response models. All input is validated here before it reaches the database."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, computed_field, field_validator

from app.models import (
    ActivityType,
    CampaignChannel,
    CampaignStatus,
    ConsentChannel,
    ConsentStatus,
    InstitutionType,
    LeadStage,
    OpportunityStage,
    Role,
    Solution,
    StudentSize,
    TaskPriority,
    TaskStatus,
    VerificationStatus,
)

Freshness = Literal["fresh", "aging", "stale", "never"]


def freshness_of(ts: datetime | None) -> Freshness:
    if ts is None:
        return "never"
    age = datetime.now(UTC) - ts
    if age <= timedelta(days=90):
        return "fresh"
    if age <= timedelta(days=180):
        return "aging"
    return "stale"


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def _blank_to_none(v: Any) -> Any:
    if isinstance(v, str) and not v.strip():
        return None
    return v


# ---------- users / auth ----------
class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class UserOut(ORM):
    id: int
    email: str
    full_name: str
    role: Role
    is_active: bool


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    role: Role = Role.rep
    password: str = Field(min_length=10, max_length=200)


class UserUpdate(BaseModel):
    full_name: str | None = Field(None, min_length=1, max_length=200)
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = Field(None, min_length=10, max_length=200)


class UserRef(ORM):
    id: int
    full_name: str


# ---------- institutions ----------
class CampusIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    city: str | None = Field(None, max_length=100)
    country: str | None = Field(None, max_length=100)
    address: str | None = Field(None, max_length=500)


class CampusOut(ORM):
    id: int
    institution_id: int
    name: str
    city: str | None
    country: str | None
    address: str | None


class InstitutionBase(BaseModel):
    name: str = Field(min_length=2, max_length=300)
    website: HttpUrl | None = None
    institution_type: InstitutionType = InstitutionType.other
    country: str = Field("", max_length=100)
    city: str = Field("", max_length=100)
    student_size_range: StudentSize = StudentSize.unknown
    courses: list[str] = Field(default_factory=list, max_length=200)
    source: str | None = Field(None, max_length=200)
    notes: str | None = Field(None, max_length=10000)
    owner_id: int | None = None
    last_verified_at: datetime | None = None

    _blank = field_validator("website", "source", "notes", mode="before")(_blank_to_none)

    @field_validator("courses")
    @classmethod
    def _courses(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for c in v:
            c = c.strip()
            if c and c not in out:
                if len(c) > 200:
                    raise ValueError("course names must be 200 characters or fewer")
                out.append(c)
        return out


class InstitutionCreate(InstitutionBase):
    campuses: list[CampusIn] = Field(default_factory=list, max_length=100)


class InstitutionUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=300)
    website: HttpUrl | None = None
    institution_type: InstitutionType | None = None
    country: str | None = Field(None, max_length=100)
    city: str | None = Field(None, max_length=100)
    student_size_range: StudentSize | None = None
    courses: list[str] | None = None
    source: str | None = Field(None, max_length=200)
    notes: str | None = Field(None, max_length=10000)
    owner_id: int | None = None
    last_verified_at: datetime | None = None

    _blank = field_validator("website", "source", "notes", mode="before")(_blank_to_none)


class InstitutionOut(ORM):
    id: int
    name: str
    website: str | None
    domain: str | None
    institution_type: InstitutionType
    country: str
    city: str
    student_size_range: StudentSize
    courses: list[str]
    source: str | None
    notes: str | None
    owner: UserRef | None
    last_verified_at: datetime | None
    is_demo: bool
    created_at: datetime
    updated_at: datetime
    campus_count: int = 0
    contact_count: int = 0
    lead_stage: LeadStage | None = None
    suppressed: bool = False
    research_score: int | None = None
    research_tier: str | None = None
    last_researched_at: datetime | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def freshness(self) -> Freshness:
        return freshness_of(self.last_verified_at)


class InstitutionDetail(InstitutionOut):
    campuses: list[CampusOut]


# ---------- contacts ----------
class ContactBase(BaseModel):
    institution_id: int
    salutation: str | None = Field(None, max_length=30)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field("", max_length=100)
    designation: str | None = Field(None, max_length=200)
    department: str | None = Field(None, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=50, pattern=r"^[0-9+()\-. ]{5,50}$")
    linkedin_url: HttpUrl | None = None
    verification_status: VerificationStatus = VerificationStatus.unverified
    source: str | None = Field(None, max_length=200)
    last_verified_at: datetime | None = None
    notes: str | None = Field(None, max_length=4000)

    _blank = field_validator(
        "salutation", "designation", "department", "email", "phone", "linkedin_url", "source", "notes", mode="before"
    )(_blank_to_none)

    @field_validator("linkedin_url")
    @classmethod
    def _linkedin(cls, v: HttpUrl | None) -> HttpUrl | None:
        if v is not None and not (v.host or "").endswith("linkedin.com"):
            raise ValueError("must be a linkedin.com profile URL")
        return v


class ContactCreate(ContactBase):
    pass


class ContactUpdate(BaseModel):
    institution_id: int | None = None
    salutation: str | None = Field(None, max_length=30)
    first_name: str | None = Field(None, min_length=1, max_length=100)
    last_name: str | None = Field(None, max_length=100)
    designation: str | None = Field(None, max_length=200)
    department: str | None = Field(None, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=50, pattern=r"^[0-9+()\-. ]{5,50}$")
    linkedin_url: HttpUrl | None = None
    verification_status: VerificationStatus | None = None
    source: str | None = Field(None, max_length=200)
    last_verified_at: datetime | None = None
    notes: str | None = Field(None, max_length=4000)

    _blank = field_validator(
        "salutation", "designation", "department", "email", "phone", "linkedin_url", "source", "notes", mode="before"
    )(_blank_to_none)


class InstitutionRef(ORM):
    id: int
    name: str


class ContactOut(ORM):
    id: int
    institution_id: int
    institution: InstitutionRef
    salutation: str | None = None
    first_name: str
    last_name: str
    full_name: str
    notes: str | None = None
    designation: str | None
    department: str | None
    email: str | None
    phone: str | None
    linkedin_url: str | None
    verification_status: VerificationStatus
    source: str | None
    last_verified_at: datetime | None
    is_demo: bool
    created_at: datetime
    updated_at: datetime
    suppressed: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def freshness(self) -> Freshness:
        return freshness_of(self.last_verified_at)


class ContactRef(ORM):
    id: int
    full_name: str
    designation: str | None = None
    email: str | None = None


class DuplicateMatch(BaseModel):
    id: int
    label: str
    reason: str
    exact: bool


# ---------- leads ----------
class LeadCreate(BaseModel):
    institution_id: int
    primary_contact_id: int | None = None
    owner_id: int | None = None
    campaign_id: int | None = None
    stage: LeadStage = LeadStage.new
    score: int = Field(0, ge=0, le=100)
    source: str | None = Field(None, max_length=200)
    summary: str | None = Field(None, max_length=10000)


class LeadUpdate(BaseModel):
    primary_contact_id: int | None = None
    owner_id: int | None = None
    campaign_id: int | None = None
    score: int | None = Field(None, ge=0, le=100)
    source: str | None = Field(None, max_length=200)
    summary: str | None = Field(None, max_length=10000)


class StageChange(BaseModel):
    stage: LeadStage
    note: str | None = Field(None, max_length=2000)
    lost_reason: str | None = Field(None, max_length=300)


class LeadOut(ORM):
    id: int
    institution: InstitutionRef
    primary_contact: ContactRef | None
    owner: UserRef | None
    campaign_id: int | None
    stage: LeadStage
    score: int
    source: str | None
    summary: str | None
    lost_reason: str | None
    stage_changed_at: datetime
    is_demo: bool
    created_at: datetime
    updated_at: datetime
    open_value: Decimal = Decimal("0")
    next_task_due: datetime | None = None


# ---------- opportunities ----------
class OpportunityCreate(BaseModel):
    lead_id: int
    name: str = Field(min_length=2, max_length=300)
    estimated_value: Decimal = Field(Decimal("0"), ge=0, max_digits=14, decimal_places=2)
    currency: str = Field("USD", pattern=r"^[A-Z]{3}$")
    solution_interest: list[Solution] = Field(default_factory=list)
    expected_decision_date: date | None = None
    owner_id: int | None = None
    stage: OpportunityStage = OpportunityStage.discovery
    next_action: str | None = Field(None, max_length=500)
    next_action_date: date | None = None


class OpportunityUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=300)
    estimated_value: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=2)
    currency: str | None = Field(None, pattern=r"^[A-Z]{3}$")
    solution_interest: list[Solution] | None = None
    expected_decision_date: date | None = None
    owner_id: int | None = None
    stage: OpportunityStage | None = None
    next_action: str | None = Field(None, max_length=500)
    next_action_date: date | None = None


class OpportunityOut(ORM):
    id: int
    lead_id: int
    institution: InstitutionRef
    name: str
    estimated_value: Decimal
    currency: str
    solution_interest: list[Solution]
    expected_decision_date: date | None
    owner: UserRef | None
    stage: OpportunityStage
    next_action: str | None
    next_action_date: date | None
    is_demo: bool
    created_at: datetime
    updated_at: datetime


# ---------- activities / research ----------
class ActivityCreate(BaseModel):
    activity_type: ActivityType
    subject: str = Field(min_length=1, max_length=300)
    body: str | None = Field(None, max_length=20000)
    occurred_at: datetime | None = None
    lead_id: int | None = None
    institution_id: int | None = None
    contact_id: int | None = None
    opportunity_id: int | None = None


class ActivityOut(ORM):
    id: int
    activity_type: ActivityType
    subject: str
    body: str | None
    occurred_at: datetime
    lead_id: int | None
    institution_id: int | None
    contact: ContactRef | None
    opportunity_id: int | None
    user: UserRef | None
    details: dict[str, Any]
    is_demo: bool


class ResearchCreate(BaseModel):
    institution_id: int
    contact_id: int | None = None
    lead_id: int | None = None
    category: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=10000)
    source_url: HttpUrl | None = None
    confidence: int = Field(50, ge=0, le=100)

    _blank = field_validator("source_url", mode="before")(_blank_to_none)


class ResearchOut(ORM):
    id: int
    institution_id: int
    contact_id: int | None
    lead_id: int | None
    category: str
    summary: str
    source_url: str | None
    confidence: int
    found_at: datetime
    is_demo: bool
    evidence_level: str | None = None
    excerpt: str | None = None
    published_at: datetime | None = None


# ---------- tasks ----------
class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(None, max_length=10000)
    due_at: datetime | None = None
    reminder_at: datetime | None = None
    priority: TaskPriority = TaskPriority.medium
    owner_id: int | None = None
    lead_id: int | None = None
    institution_id: int | None = None
    contact_id: int | None = None
    opportunity_id: int | None = None


class TaskUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    description: str | None = Field(None, max_length=10000)
    due_at: datetime | None = None
    reminder_at: datetime | None = None
    priority: TaskPriority | None = None
    status: TaskStatus | None = None
    owner_id: int | None = None


class TaskOut(ORM):
    id: int
    title: str
    description: str | None
    due_at: datetime | None
    reminder_at: datetime | None
    priority: TaskPriority
    status: TaskStatus
    completed_at: datetime | None
    owner: UserRef | None
    lead_id: int | None
    institution_id: int | None
    contact_id: int | None
    opportunity_id: int | None
    is_demo: bool
    created_at: datetime
    institution_name: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def overdue(self) -> bool:
        return self.status == TaskStatus.open and self.due_at is not None and self.due_at < datetime.now(UTC)


# ---------- campaigns ----------
class CampaignIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    description: str | None = Field(None, max_length=5000)
    channel: CampaignChannel = CampaignChannel.email
    status: CampaignStatus = CampaignStatus.draft
    start_date: date | None = None
    end_date: date | None = None
    owner_id: int | None = None


class CampaignOut(ORM):
    id: int
    name: str
    description: str | None
    channel: CampaignChannel
    status: CampaignStatus
    start_date: date | None
    end_date: date | None
    owner_id: int | None
    is_demo: bool
    lead_count: int = 0


# ---------- consent / suppression ----------
class ConsentCreate(BaseModel):
    contact_id: int | None = None
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=50)
    domain: str | None = Field(None, max_length=255, pattern=r"^[a-z0-9.-]+\.[a-z]{2,}$")
    channel: ConsentChannel = ConsentChannel.all
    status: ConsentStatus = ConsentStatus.opted_out
    reason: str | None = Field(None, max_length=500)
    source: str | None = Field(None, max_length=200)

    _blank = field_validator("email", "phone", "domain", mode="before")(_blank_to_none)


class ConsentOut(ORM):
    id: int
    contact_id: int | None
    email: str | None
    phone: str | None
    domain: str | None
    channel: ConsentChannel
    status: ConsentStatus
    reason: str | None
    source: str | None
    recorded_at: datetime
    is_demo: bool


# ---------- audit ----------
class AuditOut(ORM):
    id: int
    actor: UserRef | None
    action: str
    entity_type: str
    entity_id: int | None
    changes: dict[str, Any]
    created_at: datetime


# ---------- import ----------
class ImportRowResult(BaseModel):
    row: int
    status: Literal["created", "duplicate", "error"]
    message: str
    id: int | None = None


class ImportResult(BaseModel):
    dry_run: bool
    created: int
    duplicates: int
    errors: int
    rows: list[ImportRowResult]
