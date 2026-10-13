from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.deps import DB, RANK, CurrentUser, ManagerUser
from app.listing import ListParams, Page, paginate, pairs
from app.models import (
    Institution,
    ResearchEvidence,
    ResearchJob,
    ResearchRun,
    Role,
    RunStatus,
)
from app.research import service, workbook
from app.research.reasoning import SIGNAL_CATEGORIES, USE_CASES
from app.research.scoring import ROLE_LABELS
from app.schemas import UserRef
from app.services import get_or_404

router = APIRouter(prefix="/research", tags=["research agent"])


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class InstitutionMini(ORM):
    id: int
    name: str
    is_demo: bool
    research_score: int | None = None
    research_tier: str | None = None
    last_researched_at: datetime | None = None


class RunSummary(ORM):
    id: int
    job_id: int
    institution: InstitutionMini
    status: str
    attempts: int
    max_attempts: int
    next_attempt_at: datetime
    last_error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    provider_calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    score: int | None
    tier: str | None
    confidence: str | None
    pushed_at: datetime | None


class JobOut(ORM):
    id: int
    status: str
    label: str
    provider: str
    reasoner: str
    requested_by: UserRef | None
    total_items: int
    cost_usd: Decimal
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    counts: dict[str, int] = {}


class JobDetail(JobOut):
    runs: list[RunSummary]


class EvidenceOut(ORM):
    id: int
    kind: str
    category: str
    category_label: str = ""
    claim: str
    evidence_level: str
    confidence: int
    excerpt: str | None
    source_url: str | None
    source_title: str | None
    published_at: datetime | None
    retrieved_at: datetime | None
    provider: str
    review_status: str
    review_note: str | None
    reviewed_at: datetime | None
    pushed_at: datetime | None


class CandidateOut(ORM):
    id: int
    salutation: str | None = None
    first_name: str
    last_name: str
    full_name: str
    designation: str | None
    department: str | None
    buying_role: str | None
    buying_role_label: str | None = None
    email: str | None
    email_status: str
    phone: str | None
    linkedin_url: str | None
    verification_status: str
    source_url: str
    excerpt: str | None
    retrieved_at: datetime | None
    provider: str
    notes: str | None
    existing_contact_id: int | None
    review_status: str
    pushed_contact_id: int | None
    pushed_at: datetime | None


class RunDetail(RunSummary):
    score_breakdown: dict[str, Any]
    weights_used: dict[str, Any]
    summary: dict[str, Any]
    log: list[dict[str, Any]]
    evidence: list[EvidenceOut]
    candidates: list[CandidateOut]
    is_latest: bool = False
    is_stale: bool = False


class JobCreate(BaseModel):
    institution_ids: list[int] = Field(min_length=1, max_length=500)
    label: str | None = Field(None, max_length=200)


class StaleJobCreate(BaseModel):
    limit: int = Field(25, ge=1, le=100)
    demo: bool | None = None


class ReviewIn(BaseModel):
    evidence: dict[int, Literal["approved", "rejected", "pending"]] = Field(default_factory=dict)
    candidates: dict[int, Literal["approved", "rejected", "pending"]] = Field(default_factory=dict)
    note: str | None = Field(None, max_length=500)


class Weights(BaseModel):
    fit: float = Field(ge=0, le=100)
    problem: float = Field(ge=0, le=100)
    intent: float = Field(ge=0, le=100)
    access: float = Field(ge=0, le=100)
    quality: float = Field(ge=0, le=100)


class Tiers(BaseModel):
    A: int = Field(ge=1, le=100)
    B: int = Field(ge=1, le=100)
    C: int = Field(ge=1, le=100)


class ConfigIn(BaseModel):
    weights: Weights
    tiers: Tiers
    evidence_fresh_days: int = Field(ge=1, le=3650)
    evidence_stale_days: int = Field(ge=1, le=3650)
    rerun_after_days: int = Field(ge=1, le=3650)


def _job_out(db: DB, jobs: list[ResearchJob]) -> list[JobOut]:
    ids = [j.id for j in jobs]
    counts: dict[int, dict[str, int]] = {}
    if ids:
        for jid, st, n in db.execute(
            select(ResearchRun.job_id, ResearchRun.status, func.count())
            .where(ResearchRun.job_id.in_(ids))
            .group_by(ResearchRun.job_id, ResearchRun.status)
        ):
            counts.setdefault(jid, {})[st] = n
    out = []
    for j in jobs:
        o = JobOut.model_validate(j)
        o.counts = counts.get(j.id, {})
        out.append(o)
    return out


def _run_detail(db: DB, run: ResearchRun) -> RunDetail:
    d = RunDetail.model_validate(run)
    for e in d.evidence:
        e.category_label = SIGNAL_CATEGORIES.get(e.category) or USE_CASES.get(e.category) or e.category.replace("_", " ").title()
    for c in d.candidates:
        c.buying_role_label = ROLE_LABELS.get(c.buying_role or "")
    d.is_latest = service.latest_succeeded_id(db, run.institution_id) == run.id
    cfg = service.load_config(db)
    d.is_stale = bool(run.finished_at and run.finished_at < datetime.now(UTC) - timedelta(days=cfg["rerun_after_days"]))
    return d


def _load_run(db: DB, run_id: int) -> ResearchRun:
    run = db.scalars(
        select(ResearchRun)
        .where(ResearchRun.id == run_id)
        .options(selectinload(ResearchRun.evidence), selectinload(ResearchRun.candidates), selectinload(ResearchRun.institution))
    ).first()
    if run is None:
        raise HTTPException(404, f"Research run {run_id} not found")
    return run


@router.get("/overview")
def overview(_: CurrentUser, db: DB) -> dict[str, Any]:
    s = get_settings()
    cfg = service.load_config(db)
    tiers = pairs(
        db.execute(
            select(Institution.research_tier, func.count())
            .where(Institution.research_tier.is_not(None))
            .group_by(Institution.research_tier)
        )
    )
    queue = pairs(
        db.execute(
            select(ResearchRun.status, func.count())
            .where(ResearchRun.status.in_([RunStatus.queued.value, RunStatus.running.value]))
            .group_by(ResearchRun.status)
        )
    )
    reasoner = s.research_reasoner if s.research_reasoner != "auto" else ("claude" if s.anthropic_api_key else "rules")
    pending = (
        db.scalar(
            select(func.count())
            .select_from(ResearchEvidence)
            .join(ResearchRun)
            .where(
                ResearchEvidence.review_status == "pending",
                ResearchEvidence.evidence_level != "hypothesis",
                ResearchRun.status == RunStatus.succeeded.value,
            )
        )
        or 0
    )
    return {
        "provider": s.research_provider,
        "reasoner": reasoner,
        "model": s.anthropic_model if reasoner == "claude" else None,
        "worker": s.research_worker,
        "tiers": {t: tiers.get(t, 0) for t in "ABCD"},
        "queued": queue.get(RunStatus.queued.value, 0),
        "running": queue.get(RunStatus.running.value, 0),
        "stale": len(service.stale_institution_ids(db, cfg, limit=10000)),
        "researched": sum(tiers.values()),
        "pending_review": pending,
        "spent_today_usd": float(service.spent_today(db)),
        "daily_budget_usd": s.research_daily_budget_usd,
        "rerun_after_days": cfg["rerun_after_days"],
    }


@router.get("/config")
def get_config(_: CurrentUser, db: DB) -> dict[str, Any]:
    return service.load_config(db)


@router.put("/config")
def put_config(body: ConfigIn, user: ManagerUser, db: DB) -> dict[str, Any]:
    return service.save_config(db, body.model_dump(), user)


@router.post("/jobs", response_model=JobOut, status_code=202)
def create_job(body: JobCreate, user: CurrentUser, db: DB) -> JobOut:
    job = service.create_job(db, user, body.institution_ids, body.label)
    return _job_out(db, [job])[0]


@router.post("/jobs/stale", response_model=JobOut, status_code=202)
def create_stale_job(body: StaleJobCreate, user: CurrentUser, db: DB) -> JobOut:
    ids = service.stale_institution_ids(db, service.load_config(db), limit=body.limit, demo=body.demo)
    if not ids:
        raise HTTPException(409, "Nothing is stale — every institution has recent research")
    job = service.create_job(db, user, ids, f"Re-run stale research ({len(ids)})")
    return _job_out(db, [job])[0]


@router.get("/jobs", response_model=Page[JobOut])
def list_jobs(_: CurrentUser, db: DB, params: Annotated[ListParams, Depends()]) -> Page[JobOut]:
    stmt = (
        select(ResearchJob)
        .options(selectinload(ResearchJob.requested_by))
        .order_by(ResearchJob.created_at.desc(), ResearchJob.id.desc())
    )
    items, total = paginate(db, stmt, params)
    return Page(items=_job_out(db, items), total=total, page=params.page, page_size=params.page_size)


@router.get("/jobs/{job_id}", response_model=JobDetail)
def get_job(job_id: int, _: CurrentUser, db: DB) -> JobDetail:
    job = db.scalars(
        select(ResearchJob)
        .where(ResearchJob.id == job_id)
        .options(selectinload(ResearchJob.runs).selectinload(ResearchRun.institution), selectinload(ResearchJob.requested_by))
    ).first()
    if job is None:
        raise HTTPException(404, f"Research job {job_id} not found")
    d = JobDetail.model_validate(job)
    d.counts = _job_out(db, [job])[0].counts
    return d


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: int, user: CurrentUser, db: DB) -> JobOut:
    job = get_or_404(db, ResearchJob, job_id, "Research job")
    if job.requested_by_id != user.id and RANK[user.role] < RANK[Role.manager.value]:
        raise HTTPException(403, "Only the requester or a manager can cancel this job")
    service.cancel_job(db, job, user)
    return _job_out(db, [job])[0]


@router.post("/runs/{run_id}/retry", response_model=RunSummary)
def retry(run_id: int, user: CurrentUser, db: DB) -> ResearchRun:
    run = _load_run(db, run_id)
    service.retry_run(db, run, user)
    return _load_run(db, run_id)


@router.get("/runs", response_model=Page[RunSummary])
def list_runs(
    _: CurrentUser,
    db: DB,
    params: Annotated[ListParams, Depends()],
    status: str | None = None,
    tier: str | None = None,
    latest_only: bool = True,
) -> Page[RunSummary]:
    stmt = select(ResearchRun).join(Institution).options(selectinload(ResearchRun.institution))
    if latest_only:
        sub = (
            select(ResearchRun.institution_id, func.max(ResearchRun.id).label("mid"))
            .group_by(ResearchRun.institution_id)
            .subquery()
        )
        stmt = stmt.join(sub, ResearchRun.id == sub.c.mid)
    if status:
        stmt = stmt.where(ResearchRun.status == status)
    if tier:
        stmt = stmt.where(ResearchRun.tier == tier)
    if params.q:
        stmt = stmt.where(Institution.name.ilike(f"%{params.q}%"))
    stmt = stmt.order_by(ResearchRun.score.desc().nullslast(), ResearchRun.id.desc())
    items, total = paginate(db, stmt, params)
    return Page(items=[RunSummary.model_validate(i) for i in items], total=total, page=params.page, page_size=params.page_size)


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: int, _: CurrentUser, db: DB) -> RunDetail:
    return _run_detail(db, _load_run(db, run_id))


@router.get("/institutions/{institution_id}")
def institution_research(institution_id: int, _: CurrentUser, db: DB) -> dict[str, Any]:
    get_or_404(db, Institution, institution_id, "Institution")
    latest_any = db.scalars(
        select(ResearchRun).where(ResearchRun.institution_id == institution_id).order_by(ResearchRun.id.desc()).limit(1)
    ).first()
    ok_id = service.latest_succeeded_id(db, institution_id)
    return {
        "latest": RunSummary.model_validate(latest_any).model_dump(mode="json") if latest_any else None,
        "result": _run_detail(db, _load_run(db, ok_id)).model_dump(mode="json") if ok_id else None,
        "history": [
            RunSummary.model_validate(r).model_dump(mode="json")
            for r in db.scalars(
                select(ResearchRun)
                .options(selectinload(ResearchRun.institution))
                .where(ResearchRun.institution_id == institution_id)
                .order_by(ResearchRun.id.desc())
                .limit(20)
            )
        ],
    }


@router.post("/runs/{run_id}/review", response_model=RunDetail)
def review_run(run_id: int, body: ReviewIn, user: CurrentUser, db: DB) -> RunDetail:
    run = _load_run(db, run_id)
    if run.status != RunStatus.succeeded.value:
        raise HTTPException(409, "Only completed research can be reviewed")
    service.review(db, run, user, dict(body.evidence), dict(body.candidates), body.note)
    db.expire_all()
    return _run_detail(db, _load_run(db, run_id))


@router.post("/runs/{run_id}/push")
def push_run(run_id: int, user: CurrentUser, db: DB) -> dict[str, Any]:
    return service.push(db, _load_run(db, run_id), user)


@router.post("/import-workbook")
async def import_workbook(user: ManagerUser, db: DB, file: UploadFile = File(...), dry_run: bool = Query(True)) -> dict[str, Any]:
    """Import a PlugDrop Admissions Intelligence workbook (.xlsx). dry_run=true previews without saving."""
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(422, "Upload the research workbook as an .xlsx file")
    raw = await file.read(workbook.MAX_BYTES + 1)
    if len(raw) > workbook.MAX_BYTES:
        raise HTTPException(413, "Workbook must be 15 MB or smaller")
    try:
        rep = workbook.import_workbook(db, user, raw, file.filename or "workbook.xlsx", dry_run=dry_run)
    except ValueError as e:
        db.rollback()
        raise HTTPException(422, str(e)) from e
    except Exception as e:  # corrupt or non-Excel file
        db.rollback()
        if e.__class__.__module__.startswith(("zipfile", "openpyxl")) or "zip" in str(e).lower():
            raise HTTPException(422, "Could not read this file as an Excel workbook") from e
        raise
    return rep.as_dict()
