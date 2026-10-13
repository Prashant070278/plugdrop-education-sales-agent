from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import audit
from app.deps import DB, CurrentUser
from app.listing import ListParams, Page, apply_search, paginate
from app.models import OUTREACH_ACTIVITY, Activity, ActivityType, Contact, Institution, Lead, Opportunity, ResearchFinding
from app.schemas import ActivityCreate, ActivityOut, ResearchCreate, ResearchOut
from app.services import assert_contactable, get_or_404

router = APIRouter(tags=["activities"])


@router.get("/activities", response_model=Page[ActivityOut])
def list_activities(
    _: CurrentUser,
    db: DB,
    params: Annotated[ListParams, Depends()],
    activity_type: Annotated[list[ActivityType] | None, Query()] = None,
    lead_id: int | None = None,
    institution_id: int | None = None,
    contact_id: int | None = None,
    user_id: int | None = None,
) -> Page[ActivityOut]:
    stmt = select(Activity).options(selectinload(Activity.user), selectinload(Activity.contact))
    stmt = apply_search(stmt, params.q, [Activity.subject, Activity.body])
    if activity_type:
        stmt = stmt.where(Activity.activity_type.in_([t.value for t in activity_type]))
    for col, val in (
        (Activity.lead_id, lead_id),
        (Activity.institution_id, institution_id),
        (Activity.contact_id, contact_id),
        (Activity.user_id, user_id),
    ):
        if val:
            stmt = stmt.where(col == val)
    stmt = stmt.order_by(Activity.occurred_at.desc(), Activity.id.desc())
    items, total = paginate(db, stmt, params)
    return Page(items=[ActivityOut.model_validate(i) for i in items], total=total, page=params.page, page_size=params.page_size)


@router.post("/activities", response_model=ActivityOut, status_code=201)
def log_activity(body: ActivityCreate, user: CurrentUser, db: DB, request: Request) -> Activity:
    if body.activity_type == ActivityType.status_change:
        raise HTTPException(422, "Status changes are recorded automatically; use the lead stage endpoint")
    if not (body.lead_id or body.institution_id or body.contact_id):
        raise HTTPException(422, "An activity must be linked to a lead, institution or contact")
    institution_id = body.institution_id
    lead = get_or_404(db, Lead, body.lead_id, "Lead") if body.lead_id else None
    if lead:
        institution_id = institution_id or lead.institution_id
    contact = get_or_404(db, Contact, body.contact_id, "Contact") if body.contact_id else None
    if contact:
        institution_id = institution_id or contact.institution_id
        if body.activity_type in OUTREACH_ACTIVITY:
            assert_contactable(db, contact)
    if institution_id:
        get_or_404(db, Institution, institution_id, "Institution")
    if body.opportunity_id:
        get_or_404(db, Opportunity, body.opportunity_id, "Opportunity")
    when = body.occurred_at or datetime.now(UTC)
    if when > datetime.now(UTC) + timedelta(days=365):
        raise HTTPException(422, "occurred_at is too far in the future")
    act = Activity(
        **body.model_dump(exclude={"activity_type", "occurred_at", "institution_id"}),
        activity_type=body.activity_type.value,
        occurred_at=when,
        institution_id=institution_id,
        user_id=user.id,
    )
    db.add(act)
    db.flush()
    audit.record(db, user, "create", "activity", act.id, {"type": act.activity_type, "subject": act.subject}, request)
    db.commit()
    return db.scalars(
        select(Activity).options(selectinload(Activity.user), selectinload(Activity.contact)).where(Activity.id == act.id)
    ).one()


@router.get("/research", response_model=list[ResearchOut])
def list_research(_: CurrentUser, db: DB, institution_id: int) -> list[ResearchFinding]:
    return list(
        db.scalars(
            select(ResearchFinding)
            .where(ResearchFinding.institution_id == institution_id)
            .order_by(ResearchFinding.found_at.desc())
        )
    )


@router.post("/research", response_model=ResearchOut, status_code=201)
def add_research(body: ResearchCreate, user: CurrentUser, db: DB, request: Request) -> ResearchFinding:
    get_or_404(db, Institution, body.institution_id, "Institution")
    if body.contact_id:
        get_or_404(db, Contact, body.contact_id, "Contact")
    if body.lead_id:
        get_or_404(db, Lead, body.lead_id, "Lead")
    data = body.model_dump()
    data["source_url"] = str(body.source_url) if body.source_url else None
    rf = ResearchFinding(**data, user_id=user.id)
    db.add(rf)
    db.flush()
    db.add(
        Activity(
            activity_type=ActivityType.research.value,
            subject=f"Research: {rf.category}",
            body=rf.summary,
            lead_id=rf.lead_id,
            institution_id=rf.institution_id,
            contact_id=rf.contact_id,
            user_id=user.id,
            details={"source_url": rf.source_url, "confidence": rf.confidence},
        )
    )
    audit.record(db, user, "create", "research_finding", rf.id, audit.snapshot(rf), request)
    db.commit()
    return rf
