from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import audit
from app.deps import DB, CurrentUser, ManagerUser
from app.listing import ListParams, Page, apply_search, apply_sort, paginate
from app.models import Activity, ActivityType, Institution, Lead, Opportunity, OpportunityStage
from app.schemas import OpportunityCreate, OpportunityOut, OpportunityUpdate
from app.services import ensure_user, get_or_404

router = APIRouter(prefix="/opportunities", tags=["opportunities"])

SORTABLE = {
    "name": Opportunity.name,
    "estimated_value": Opportunity.estimated_value,
    "expected_decision_date": Opportunity.expected_decision_date,
    "stage": Opportunity.stage,
    "next_action_date": Opportunity.next_action_date,
    "updated_at": Opportunity.updated_at,
    "institution": Institution.name,
}


def _load() -> Any:
    return (
        select(Opportunity)
        .join(Institution, Opportunity.institution_id == Institution.id)
        .options(selectinload(Opportunity.institution), selectinload(Opportunity.owner))
    )


def _one(db: DB, opp_id: int) -> Opportunity:
    o = db.scalars(_load().where(Opportunity.id == opp_id)).one_or_none()
    if o is None:
        raise HTTPException(404, f"Opportunity {opp_id} not found")
    return o


@router.get("", response_model=Page[OpportunityOut])
def list_opportunities(
    _: CurrentUser,
    db: DB,
    params: Annotated[ListParams, Depends()],
    stage: Annotated[list[OpportunityStage] | None, Query()] = None,
    owner_id: int | None = None,
    lead_id: int | None = None,
    institution_id: int | None = None,
) -> Page[OpportunityOut]:
    stmt = apply_search(_load(), params.q, [Opportunity.name, Institution.name, Opportunity.next_action])
    if stage:
        stmt = stmt.where(Opportunity.stage.in_([s.value for s in stage]))
    if owner_id:
        stmt = stmt.where(Opportunity.owner_id == owner_id)
    if lead_id:
        stmt = stmt.where(Opportunity.lead_id == lead_id)
    if institution_id:
        stmt = stmt.where(Opportunity.institution_id == institution_id)
    stmt = apply_sort(stmt, params.sort, SORTABLE, Opportunity.updated_at.desc())
    items, total = paginate(db, stmt, params)
    return Page(
        items=[OpportunityOut.model_validate(i) for i in items], total=total, page=params.page, page_size=params.page_size
    )


@router.post("", response_model=OpportunityOut, status_code=201)
def create_opportunity(body: OpportunityCreate, user: CurrentUser, db: DB, request: Request) -> Opportunity:
    lead = get_or_404(db, Lead, body.lead_id, "Lead")
    ensure_user(db, body.owner_id)
    data = body.model_dump()
    data["stage"] = body.stage.value
    data["solution_interest"] = [s.value for s in body.solution_interest]
    opp = Opportunity(**data, institution_id=lead.institution_id)
    if opp.owner_id is None:
        opp.owner_id = lead.owner_id or user.id
    db.add(opp)
    db.flush()
    db.add(
        Activity(
            activity_type=ActivityType.note.value,
            subject=f"Opportunity created: {opp.name}",
            lead_id=lead.id,
            institution_id=lead.institution_id,
            opportunity_id=opp.id,
            user_id=user.id,
            details={"estimated_value": str(opp.estimated_value), "currency": opp.currency},
        )
    )
    audit.record(db, user, "create", "opportunity", opp.id, audit.snapshot(opp), request)
    db.commit()
    return _one(db, opp.id)


@router.get("/{opp_id}", response_model=OpportunityOut)
def get_opportunity(opp_id: int, _: CurrentUser, db: DB) -> Opportunity:
    return _one(db, opp_id)


@router.patch("/{opp_id}", response_model=OpportunityOut)
def update_opportunity(opp_id: int, body: OpportunityUpdate, user: CurrentUser, db: DB, request: Request) -> Opportunity:
    opp = get_or_404(db, Opportunity, opp_id, "Opportunity")
    data = body.model_dump(exclude_unset=True)
    ensure_user(db, data.get("owner_id"))
    before = audit.snapshot(opp)
    if "stage" in data and data["stage"] is not None:
        data["stage"] = data["stage"].value
    if "solution_interest" in data and data["solution_interest"] is not None:
        data["solution_interest"] = [s.value for s in data["solution_interest"]]
    for k, v in data.items():
        setattr(opp, k, v)
    db.flush()
    changes = audit.diff(before, audit.snapshot(opp))
    if "stage" in changes:
        db.add(
            Activity(
                activity_type=ActivityType.status_change.value,
                subject=f"Opportunity {opp.name}: {changes['stage']['from']} → {changes['stage']['to']}",
                lead_id=opp.lead_id,
                institution_id=opp.institution_id,
                opportunity_id=opp.id,
                user_id=user.id,
                details={"from": changes["stage"]["from"], "to": changes["stage"]["to"]},
            )
        )
    audit.record(db, user, "update", "opportunity", opp.id, changes, request)
    db.commit()
    return _one(db, opp.id)


@router.delete("/{opp_id}", status_code=204)
def delete_opportunity(opp_id: int, user: ManagerUser, db: DB, request: Request) -> None:
    opp = get_or_404(db, Opportunity, opp_id, "Opportunity")
    snap = audit.snapshot(opp)
    db.delete(opp)
    audit.record(db, user, "delete", "opportunity", opp_id, snap, request)
    db.commit()
