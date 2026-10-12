from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app import audit
from app.deps import DB, CurrentUser, ManagerUser
from app.listing import ListParams, Page, apply_search, apply_sort, paginate, pairs
from app.models import (
    CLOSED_STAGES,
    OUTREACH_STAGES,
    Activity,
    ActivityType,
    Campaign,
    Contact,
    Institution,
    Lead,
    LeadStage,
    Opportunity,
    OpportunityStage,
    ResearchFinding,
    Task,
    TaskStatus,
)
from app.schemas import ActivityOut, LeadCreate, LeadOut, LeadUpdate, ResearchOut, StageChange, TaskOut
from app.services import assert_contactable, ensure_exists, ensure_user, get_or_404

router = APIRouter(prefix="/leads", tags=["leads"])

SORTABLE = {
    "stage": Lead.stage,
    "score": Lead.score,
    "created_at": Lead.created_at,
    "updated_at": Lead.updated_at,
    "stage_changed_at": Lead.stage_changed_at,
    "institution": Institution.name,
}
OPEN_OPP = [OpportunityStage.won.value, OpportunityStage.lost.value]


def enrich(db: DB, leads: list[Lead]) -> list[LeadOut]:
    ids = [lead.id for lead in leads]
    values: dict[int, Decimal] = {}
    due: dict[int, datetime] = {}
    if ids:
        values = pairs(
            db.execute(
                select(Opportunity.lead_id, func.coalesce(func.sum(Opportunity.estimated_value), 0))
                .where(Opportunity.lead_id.in_(ids), Opportunity.stage.not_in(OPEN_OPP))
                .group_by(Opportunity.lead_id)
            ).all()
        )
        due = pairs(
            db.execute(
                select(Task.lead_id, func.min(Task.due_at))
                .where(Task.lead_id.in_(ids), Task.status == TaskStatus.open.value)
                .group_by(Task.lead_id)
            ).all()
        )
    out = []
    for lead in leads:
        o = LeadOut.model_validate(lead)
        o.open_value = values.get(lead.id, Decimal("0"))
        o.next_task_due = due.get(lead.id)
        out.append(o)
    return out


def _load() -> Any:
    return (
        select(Lead)
        .join(Institution, Lead.institution_id == Institution.id)
        .options(selectinload(Lead.institution), selectinload(Lead.primary_contact), selectinload(Lead.owner))
    )


@router.get("", response_model=Page[LeadOut])
def list_leads(
    _: CurrentUser,
    db: DB,
    params: Annotated[ListParams, Depends()],
    stage: Annotated[list[LeadStage] | None, Query()] = None,
    owner_id: int | None = None,
    institution_id: int | None = None,
    campaign_id: int | None = None,
    demo: bool | None = None,
) -> Page[LeadOut]:
    stmt = apply_search(_load(), params.q, [Institution.name, Lead.source, Lead.summary])
    if stage:
        stmt = stmt.where(Lead.stage.in_([s.value for s in stage]))
    if owner_id:
        stmt = stmt.where(Lead.owner_id == owner_id)
    if institution_id:
        stmt = stmt.where(Lead.institution_id == institution_id)
    if campaign_id:
        stmt = stmt.where(Lead.campaign_id == campaign_id)
    if demo is not None:
        stmt = stmt.where(Lead.is_demo.is_(demo))
    stmt = apply_sort(stmt, params.sort, SORTABLE, Lead.updated_at.desc())
    items, total = paginate(db, stmt, params)
    return Page(items=enrich(db, items), total=total, page=params.page, page_size=params.page_size)


@router.get("/stage-counts", response_model=dict[str, int])
def stage_counts(_: CurrentUser, db: DB) -> dict[str, int]:
    counts = pairs(db.execute(select(Lead.stage, func.count()).group_by(Lead.stage)).all())
    return {s.value: int(counts.get(s.value, 0)) for s in LeadStage}


@router.post("", response_model=LeadOut, status_code=201)
def create_lead(body: LeadCreate, user: CurrentUser, db: DB, request: Request) -> LeadOut:
    get_or_404(db, Institution, body.institution_id, "Institution")
    ensure_user(db, body.owner_id)
    ensure_exists(db, Campaign, body.campaign_id, "Campaign")
    contact = None
    if body.primary_contact_id:
        contact = get_or_404(db, Contact, body.primary_contact_id, "Contact")
        if contact.institution_id != body.institution_id:
            raise HTTPException(422, "Primary contact must belong to the lead's institution")
    if body.stage in OUTREACH_STAGES:
        assert_contactable(db, contact)
    lead = Lead(**body.model_dump(exclude={"stage"}), stage=body.stage.value)
    if lead.owner_id is None:
        lead.owner_id = user.id
    db.add(lead)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(409, "This institution already has an open lead") from e
    db.add(
        Activity(
            activity_type=ActivityType.status_change.value,
            subject=f"Lead created at stage {lead.stage}",
            lead_id=lead.id,
            institution_id=lead.institution_id,
            user_id=user.id,
            details={"from": None, "to": lead.stage},
        )
    )
    audit.record(db, user, "create", "lead", lead.id, audit.snapshot(lead), request)
    db.commit()
    return enrich(db, [db.scalars(_load().where(Lead.id == lead.id)).one()])[0]


@router.get("/{lead_id}", response_model=LeadOut)
def get_lead(lead_id: int, _: CurrentUser, db: DB) -> LeadOut:
    lead = db.scalars(_load().where(Lead.id == lead_id)).one_or_none()
    if lead is None:
        raise HTTPException(404, f"Lead {lead_id} not found")
    return enrich(db, [lead])[0]


@router.patch("/{lead_id}", response_model=LeadOut)
def update_lead(lead_id: int, body: LeadUpdate, user: CurrentUser, db: DB, request: Request) -> LeadOut:
    lead = get_or_404(db, Lead, lead_id, "Lead")
    data = body.model_dump(exclude_unset=True)
    ensure_user(db, data.get("owner_id"))
    ensure_exists(db, Campaign, data.get("campaign_id"), "Campaign")
    if data.get("primary_contact_id"):
        contact = get_or_404(db, Contact, data["primary_contact_id"], "Contact")
        if contact.institution_id != lead.institution_id:
            raise HTTPException(422, "Primary contact must belong to the lead's institution")
    before = audit.snapshot(lead)
    for k, v in data.items():
        setattr(lead, k, v)
    db.flush()
    audit.record(db, user, "update", "lead", lead.id, audit.diff(before, audit.snapshot(lead)), request)
    db.commit()
    return get_lead(lead.id, user, db)


@router.post("/{lead_id}/stage", response_model=LeadOut)
def change_stage(lead_id: int, body: StageChange, user: CurrentUser, db: DB, request: Request) -> LeadOut:
    lead = get_or_404(db, Lead, lead_id, "Lead")
    old = lead.stage
    new = body.stage
    if old == new.value:
        return get_lead(lead.id, user, db)
    if new == LeadStage.lost and not body.lost_reason:
        raise HTTPException(422, "A lost reason is required when marking a lead as lost")
    if new in OUTREACH_STAGES:
        contact = db.get(Contact, lead.primary_contact_id) if lead.primary_contact_id else None
        if contact is None:
            raise HTTPException(422, "Set a primary contact before moving a lead into outreach")
        assert_contactable(db, contact)
    if old in {s.value for s in CLOSED_STAGES} and new not in CLOSED_STAGES:
        clash = db.scalar(
            select(Lead.id).where(
                Lead.institution_id == lead.institution_id,
                Lead.id != lead.id,
                Lead.stage.not_in([s.value for s in CLOSED_STAGES]),
            )
        )
        if clash:
            raise HTTPException(409, "This institution already has another open lead")
    lead.stage = new.value
    lead.stage_changed_at = datetime.now(UTC)
    if new == LeadStage.lost:
        lead.lost_reason = body.lost_reason
    db.add(
        Activity(
            activity_type=ActivityType.status_change.value,
            subject=f"Stage changed: {old.replace('_', ' ')} → {new.value.replace('_', ' ')}",
            body=body.note,
            lead_id=lead.id,
            institution_id=lead.institution_id,
            user_id=user.id,
            details={"from": old, "to": new.value, **({"lost_reason": body.lost_reason} if body.lost_reason else {})},
        )
    )
    audit.record(db, user, "stage_change", "lead", lead.id, {"stage": {"from": old, "to": new.value}}, request)
    db.commit()
    return get_lead(lead.id, user, db)


@router.delete("/{lead_id}", status_code=204)
def delete_lead(lead_id: int, user: ManagerUser, db: DB, request: Request) -> None:
    lead = get_or_404(db, Lead, lead_id, "Lead")
    snap = audit.snapshot(lead)
    db.delete(lead)
    audit.record(db, user, "delete", "lead", lead_id, snap, request)
    db.commit()


@router.get("/{lead_id}/timeline", response_model=list[ActivityOut])
def timeline(lead_id: int, _: CurrentUser, db: DB, limit: int = Query(200, ge=1, le=1000)) -> list[Activity]:
    """Chronological history (newest first): this lead's activities plus institution-level ones."""
    lead = get_or_404(db, Lead, lead_id, "Lead")
    stmt = (
        select(Activity)
        .options(selectinload(Activity.user), selectinload(Activity.contact))
        .where(or_(Activity.lead_id == lead.id, (Activity.institution_id == lead.institution_id) & Activity.lead_id.is_(None)))
        .order_by(Activity.occurred_at.desc(), Activity.id.desc())
        .limit(limit)
    )
    return list(db.scalars(stmt))


@router.get("/{lead_id}/tasks", response_model=list[TaskOut])
def lead_tasks(lead_id: int, _: CurrentUser, db: DB) -> list[Task]:
    get_or_404(db, Lead, lead_id, "Lead")
    return list(
        db.scalars(
            select(Task)
            .options(selectinload(Task.owner))
            .where(Task.lead_id == lead_id)
            .order_by(Task.status, Task.due_at.asc().nullslast())
        )
    )


@router.get("/{lead_id}/research", response_model=list[ResearchOut])
def lead_research(lead_id: int, _: CurrentUser, db: DB) -> list[ResearchFinding]:
    lead = get_or_404(db, Lead, lead_id, "Lead")
    return list(
        db.scalars(
            select(ResearchFinding)
            .where(or_(ResearchFinding.lead_id == lead.id, ResearchFinding.institution_id == lead.institution_id))
            .order_by(ResearchFinding.found_at.desc())
        )
    )
