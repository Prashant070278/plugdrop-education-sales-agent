from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.deps import DB, CurrentUser
from app.listing import pairs
from app.models import (
    CLOSED_STAGES,
    FUNNEL,
    Activity,
    ActivityType,
    Contact,
    Institution,
    Lead,
    LeadStage,
    Opportunity,
    OpportunityStage,
    Task,
    TaskStatus,
)
from app.schemas import ActivityOut, OpportunityOut

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

QUALIFIED_PLUS = [s.value for s in FUNNEL[FUNNEL.index(LeadStage.qualified) :] if s != LeadStage.won]


class FunnelStep(BaseModel):
    stage: str
    reached: int
    rate: float  # share of all leads that reached at least this stage


class WeekPoint(BaseModel):
    week_start: str
    calls: int
    emails: int
    meetings: int


class Dashboard(BaseModel):
    period_days: int
    total_prospects: int
    total_contacts: int
    open_leads: int
    qualified_leads: int
    outreach_activity: int
    meetings: int
    meetings_booked_leads: int
    pipeline_value: Decimal
    won_value: Decimal
    win_rate: float | None
    lead_to_meeting_rate: float | None
    stage_counts: dict[str, int]
    funnel: list[FunnelStep]
    weekly_activity: list[WeekPoint]
    tasks_overdue: int
    tasks_due_today: int
    top_opportunities: list[OpportunityOut]
    recent_activity: list[ActivityOut]


@router.get("", response_model=Dashboard)
def dashboard(user: CurrentUser, db: DB, days: int = Query(30, ge=7, le=365), mine: bool = False) -> Dashboard:
    now = datetime.now(UTC)
    since = now - timedelta(days=days)
    lead_q = select(Lead)
    if mine:
        lead_q = lead_q.where(Lead.owner_id == user.id)
    leads = db.scalars(lead_q).all()
    lead_ids = [lead.id for lead in leads]

    stage_counts = {s.value: 0 for s in LeadStage}
    for lead in leads:
        stage_counts[lead.stage] += 1

    # Furthest funnel stage each lead ever reached (current stage + logged stage changes).
    reached_idx: dict[int, int] = {}
    funnel_vals = [s.value for s in FUNNEL]
    for lead in leads:
        reached_idx[lead.id] = funnel_vals.index(lead.stage) if lead.stage in funnel_vals else 0
    if lead_ids:
        for lead_id, details in db.execute(
            select(Activity.lead_id, Activity.details).where(
                Activity.lead_id.in_(lead_ids), Activity.activity_type == ActivityType.status_change.value
            )
        ):
            to = (details or {}).get("to")
            if to in funnel_vals:
                reached_idx[lead_id] = max(reached_idx.get(lead_id, 0), funnel_vals.index(to))
    total = len(leads)
    funnel = []
    for i, s in enumerate(FUNNEL):
        n = sum(1 for v in reached_idx.values() if v >= i)
        funnel.append(FunnelStep(stage=s.value, reached=n, rate=round(n / total, 4) if total else 0.0))

    won, lost = stage_counts[LeadStage.won.value], stage_counts[LeadStage.lost.value]
    meeting_idx = funnel_vals.index(LeadStage.meeting_booked.value)
    reached_meeting = sum(1 for v in reached_idx.values() if v >= meeting_idx)

    mine_act: list[Any] = [Activity.user_id == user.id] if mine else []
    counts = pairs(
        db.execute(
            select(Activity.activity_type, func.count())
            .where(Activity.occurred_at >= since, *mine_act)
            .group_by(Activity.activity_type)
        ).all()
    )

    weeks: list[WeekPoint] = []
    start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    for w in range(7, -1, -1):
        ws = start - timedelta(weeks=w)
        we = ws + timedelta(weeks=1)
        row = pairs(
            db.execute(
                select(Activity.activity_type, func.count())
                .where(
                    Activity.occurred_at >= ws,
                    Activity.occurred_at < we,
                    Activity.activity_type.in_(["call", "email", "meeting"]),
                    *mine_act,
                )
                .group_by(Activity.activity_type)
            ).all()
        )
        weeks.append(
            WeekPoint(
                week_start=ws.date().isoformat(),
                calls=row.get("call", 0),
                emails=row.get("email", 0),
                meetings=row.get("meeting", 0),
            )
        )

    mine_opp: list[Any] = [Opportunity.owner_id == user.id] if mine else []
    opp_q = select(Opportunity).where(*mine_opp)
    open_opp = [OpportunityStage.won.value, OpportunityStage.lost.value]
    pipeline_value = db.scalar(
        select(func.coalesce(func.sum(Opportunity.estimated_value), 0)).where(Opportunity.stage.not_in(open_opp), *mine_opp)
    )
    won_value = db.scalar(
        select(func.coalesce(func.sum(Opportunity.estimated_value), 0)).where(
            Opportunity.stage == OpportunityStage.won.value, *mine_opp
        )
    )
    top = db.scalars(
        opp_q.options(selectinload(Opportunity.institution), selectinload(Opportunity.owner))
        .where(Opportunity.stage.not_in(open_opp))
        .order_by(Opportunity.estimated_value.desc())
        .limit(5)
    ).all()

    task_filter: list[Any] = [Task.status == TaskStatus.open.value]
    if mine:
        task_filter.append(Task.owner_id == user.id)
    end_today = now.replace(hour=23, minute=59, second=59)
    overdue = db.scalar(select(func.count()).select_from(Task).where(*task_filter, Task.due_at < now)) or 0
    due_today = (
        db.scalar(select(func.count()).select_from(Task).where(*task_filter, Task.due_at >= now, Task.due_at <= end_today)) or 0
    )

    recent = db.scalars(
        select(Activity)
        .options(selectinload(Activity.user), selectinload(Activity.contact))
        .where(*mine_act)
        .order_by(Activity.occurred_at.desc())
        .limit(8)
    ).all()

    return Dashboard(
        period_days=days,
        total_prospects=db.scalar(select(func.count()).select_from(Institution)) or 0,
        total_contacts=db.scalar(select(func.count()).select_from(Contact)) or 0,
        open_leads=sum(1 for lead in leads if lead.stage not in {s.value for s in CLOSED_STAGES}),
        qualified_leads=sum(stage_counts[s] for s in QUALIFIED_PLUS),
        outreach_activity=int(counts.get("call", 0) + counts.get("email", 0)),
        meetings=int(counts.get("meeting", 0)),
        meetings_booked_leads=stage_counts[LeadStage.meeting_booked.value],
        pipeline_value=Decimal(pipeline_value or 0),
        won_value=Decimal(won_value or 0),
        win_rate=round(won / (won + lost), 4) if (won + lost) else None,
        lead_to_meeting_rate=round(reached_meeting / total, 4) if total else None,
        stage_counts=stage_counts,
        funnel=funnel,
        weekly_activity=weeks,
        tasks_overdue=overdue,
        tasks_due_today=due_today,
        top_opportunities=[OpportunityOut.model_validate(o) for o in top],
        recent_activity=[ActivityOut.model_validate(a) for a in recent],
    )
