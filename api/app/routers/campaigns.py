from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import audit
from app.deps import DB, CurrentUser, ManagerUser
from app.listing import pairs
from app.models import Campaign, Lead
from app.schemas import CampaignIn, CampaignOut
from app.services import ensure_user, get_or_404

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


@router.get("", response_model=list[CampaignOut])
def list_campaigns(_: CurrentUser, db: DB) -> list[CampaignOut]:
    counts = pairs(
        db.execute(select(Lead.campaign_id, func.count()).where(Lead.campaign_id.is_not(None)).group_by(Lead.campaign_id)).all()
    )
    out = []
    for c in db.scalars(select(Campaign).order_by(Campaign.created_at.desc())):
        o = CampaignOut.model_validate(c)
        o.lead_count = counts.get(c.id, 0)
        out.append(o)
    return out


def _save(db: DB, c: Campaign) -> None:
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(409, "A campaign with this name already exists, or its end date is before its start date") from e


@router.post("", response_model=CampaignOut, status_code=201)
def create_campaign(body: CampaignIn, user: ManagerUser, db: DB, request: Request) -> Campaign:
    ensure_user(db, body.owner_id)
    data = body.model_dump()
    data["channel"], data["status"] = body.channel.value, body.status.value
    c = Campaign(**data)
    c.owner_id = c.owner_id or user.id
    db.add(c)
    _save(db, c)
    audit.record(db, user, "create", "campaign", c.id, audit.snapshot(c), request)
    db.commit()
    return c


@router.put("/{campaign_id}", response_model=CampaignOut)
def update_campaign(campaign_id: int, body: CampaignIn, user: ManagerUser, db: DB, request: Request) -> Campaign:
    c = get_or_404(db, Campaign, campaign_id, "Campaign")
    ensure_user(db, body.owner_id)
    before = audit.snapshot(c)
    for k, v in body.model_dump().items():
        setattr(c, k, v.value if hasattr(v, "value") else v)
    _save(db, c)
    audit.record(db, user, "update", "campaign", c.id, audit.diff(before, audit.snapshot(c)), request)
    db.commit()
    return c
