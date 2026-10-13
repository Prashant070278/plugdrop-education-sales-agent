"""Research jobs: queueing, processing (retries, rate limits, budget, logging, cost), review and CRM push."""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, selectinload

from app import audit
from app.config import Settings, get_settings
from app.models import (
    CLOSED_STAGES,
    Activity,
    AppSetting,
    Campus,
    ConsentRecord,
    Contact,
    Institution,
    JobStatus,
    Lead,
    ResearchContactCandidate,
    ResearchEvidence,
    ResearchFinding,
    ResearchJob,
    ResearchRun,
    ReviewStatus,
    RunStatus,
    User,
)
from app.normalize import domain_from_website, normalize_email, normalize_name
from app.research.providers import ProviderError, get_provider
from app.research.providers.base import ProviderResult, SourceDocument
from app.research.reasoning import SIGNAL_CATEGORIES, USE_CASES, Finding, ReasonerError, get_reasoner
from app.research.scoring import EvidenceIn, PersonIn, buying_role, merged_config, score
from app.services import contact_duplicates

log = logging.getLogger("plugdrop.research")
CONFIG_KEY = "research_scoring"
TERMINAL = {RunStatus.succeeded.value, RunStatus.failed.value, RunStatus.cancelled.value}
TARGET_ROLES = [
    "Director/Head of Admissions",
    "Student Recruitment Head",
    "CIO/CTO",
    "CRM or Digital Transformation Head",
    "Contact Centre Head",
    "COO",
]


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------
def load_config(db: Session) -> dict[str, Any]:
    row = db.get(AppSetting, CONFIG_KEY)
    return merged_config(row.value if row else None)


def save_config(db: Session, value: dict[str, Any], user: User) -> dict[str, Any]:
    cfg = merged_config(value)
    w = cfg["weights"]
    if set(w) != {"fit", "problem", "intent", "access", "quality"}:
        raise HTTPException(422, "weights must contain exactly fit, problem, intent, access, quality")
    for k, v in w.items():
        if not isinstance(v, int | float) or v < 0 or v > 100:
            raise HTTPException(422, f"weight '{k}' must be a number between 0 and 100")
    if sum(w.values()) <= 0:
        raise HTTPException(422, "At least one weight must be above zero")
    t = cfg["tiers"]
    if not (100 >= t["A"] > t["B"] > t["C"] > 0):
        raise HTTPException(422, "Tier thresholds must satisfy 100 ≥ A > B > C > 0")
    for k in ("evidence_fresh_days", "evidence_stale_days", "rerun_after_days"):
        if not isinstance(cfg[k], int) or not 1 <= cfg[k] <= 3650:
            raise HTTPException(422, f"{k} must be a whole number of days between 1 and 3650")
    if cfg["evidence_fresh_days"] >= cfg["evidence_stale_days"]:
        raise HTTPException(422, "evidence_fresh_days must be smaller than evidence_stale_days")
    row = db.get(AppSetting, CONFIG_KEY)
    before = row.value if row else {}
    if row is None:
        row = AppSetting(key=CONFIG_KEY, value=cfg, updated_by_id=user.id)
        db.add(row)
    else:
        row.value, row.updated_by_id = cfg, user.id
    audit.record(db, user, "update", "research_config", None, {"from": before, "to": cfg})
    db.flush()
    rescored = rescore_latest(db, cfg)
    db.commit()
    return {"config": cfg, "rescored": rescored}


# ---------------------------------------------------------------------------
# job creation
# ---------------------------------------------------------------------------
def create_job(db: Session, user: User, institution_ids: list[int], label: str | None = None) -> ResearchJob:
    s = get_settings()
    ids = list(dict.fromkeys(institution_ids))
    if not ids:
        raise HTTPException(422, "Choose at least one institution")
    if len(ids) > s.research_max_items_per_job:
        raise HTTPException(422, f"A job can include at most {s.research_max_items_per_job} institutions")
    found = set(db.scalars(select(Institution.id).where(Institution.id.in_(ids))))
    missing = [i for i in ids if i not in found]
    if missing:
        raise HTTPException(404, f"Institutions not found: {missing}")
    busy = set(
        db.scalars(
            select(ResearchRun.institution_id).where(
                ResearchRun.institution_id.in_(ids), ResearchRun.status.in_([RunStatus.queued.value, RunStatus.running.value])
            )
        )
    )
    todo = [i for i in ids if i not in busy]
    if not todo:
        raise HTTPException(409, "Research is already queued or running for the selected institution(s)")
    reasoner = s.research_reasoner if s.research_reasoner != "auto" else ("claude" if s.anthropic_api_key else "rules")
    job = ResearchJob(
        label=label or (f"{len(todo)} institutions" if len(todo) > 1 else "1 institution"),
        provider=s.research_provider,
        reasoner=reasoner,
        requested_by_id=user.id,
        total_items=len(todo),
    )
    db.add(job)
    db.flush()
    for iid in todo:
        db.add(
            ResearchRun(
                job_id=job.id, institution_id=iid, max_attempts=s.research_max_attempts, next_attempt_at=datetime.now(UTC)
            )
        )
    audit.record(
        db, user, "create", "research_job", job.id, {"institutions": todo, "skipped_already_running": sorted(busy & set(ids))}
    )
    db.commit()
    return job


def stale_institution_ids(db: Session, cfg: dict[str, Any], limit: int = 100, demo: bool | None = None) -> list[int]:
    """Institutions never researched, or whose latest successful research is older than rerun_after_days."""
    cutoff = datetime.now(UTC) - timedelta(days=cfg["rerun_after_days"])
    stmt = select(Institution.id).where((Institution.last_researched_at.is_(None)) | (Institution.last_researched_at < cutoff))
    if demo is not None:
        stmt = stmt.where(Institution.is_demo.is_(demo))
    busy = select(ResearchRun.institution_id).where(ResearchRun.status.in_([RunStatus.queued.value, RunStatus.running.value]))
    stmt = stmt.where(Institution.id.not_in(busy)).order_by(Institution.last_researched_at.asc().nullsfirst()).limit(limit)
    return list(db.scalars(stmt))


# ---------------------------------------------------------------------------
# processing
# ---------------------------------------------------------------------------
class RateLimiter:
    """Simple token bucket per provider (per worker process)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[float, float]] = {}

    def wait(self, key: str, per_minute: int, sleep: Any = time.sleep) -> float:
        if per_minute <= 0:
            return 0.0
        with self._lock:
            tokens, last = self._buckets.get(key, (float(per_minute), time.monotonic()))
            now = time.monotonic()
            tokens = min(per_minute, tokens + (now - last) * per_minute / 60)
            waited = 0.0
            if tokens < 1:
                waited = (1 - tokens) * 60 / per_minute
                sleep(waited)
                tokens = 1
                now = time.monotonic()
            self._buckets[key] = (tokens - 1, now)
            return waited


RATE_LIMITER = RateLimiter()


def _log(run: ResearchRun, event: str, **data: Any) -> None:
    entry = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "event": event, **data}
    run.log = [*(run.log or []), entry]
    log.info("research run=%s %s %s", run.id, event, data)


def spent_today(db: Session) -> Decimal:
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return db.scalar(select(func.coalesce(func.sum(ResearchRun.cost_usd), 0)).where(ResearchRun.finished_at >= start)) or Decimal(
        0
    )


def claim_next(db: Session) -> ResearchRun | None:
    run = db.scalars(
        select(ResearchRun)
        .where(ResearchRun.status == RunStatus.queued.value, ResearchRun.next_attempt_at <= datetime.now(UTC))
        .order_by(ResearchRun.next_attempt_at, ResearchRun.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    ).first()
    if run is None:
        return None
    run.status = RunStatus.running.value
    run.attempts += 1
    run.started_at = datetime.now(UTC)
    job = db.get(ResearchJob, run.job_id)
    if job and job.status == JobStatus.queued.value:
        job.status, job.started_at = JobStatus.running.value, datetime.now(UTC)
    db.commit()
    return run


def process_next(
    db: Session, settings: Settings | None = None, provider: Any = None, reasoner: Any = None, sleep: Any = time.sleep
) -> ResearchRun | None:
    """Claim and process one due run. Returns the run, or None when nothing is due."""
    s = settings or get_settings()
    run = claim_next(db)
    if run is None:
        return None
    if spent_today(db) >= Decimal(str(s.research_daily_budget_usd)):
        tomorrow = (datetime.now(UTC) + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
        run.status, run.attempts, run.next_attempt_at = RunStatus.queued.value, run.attempts - 1, tomorrow
        run.last_error = f"Daily research budget of ${s.research_daily_budget_usd:.2f} reached; deferred to tomorrow"
        _log(run, "deferred_budget")
        db.commit()
        return run
    inst = db.get(Institution, run.institution_id)
    assert inst is not None
    _log(run, "attempt_started", attempt=run.attempts)
    t0 = time.monotonic()
    try:
        prov = provider or get_provider(s.research_provider)
        waited = RATE_LIMITER.wait(prov.name, s.research_provider_rate_per_minute, sleep)
        if waited:
            _log(run, "rate_limited", waited_seconds=round(waited, 2))
        result: ProviderResult = prov.research(
            name=inst.name, website=inst.website, country=inst.country, city=inst.city, roles=TARGET_ROLES
        )
        run.provider_calls += result.calls
        run.cost_usd = Decimal(str(run.cost_usd)) + Decimal(str(round(result.cost_usd, 6)))
        _log(
            run,
            "provider_done",
            provider=prov.name,
            documents=len(result.documents),
            people=len(result.people),
            calls=result.calls,
            cost_usd=round(result.cost_usd, 6),
        )
        rsn = reasoner or get_reasoner(s)
        inst_info = {
            "name": inst.name,
            "website": inst.website,
            "city": inst.city,
            "country": inst.country,
            "institution_type": inst.institution_type,
            "institution_type_label": inst.institution_type.replace("_", " "),
            "student_size_range": inst.student_size_range,
        }
        people_info = [{"designation": p.designation} for p in result.people]
        analysis = rsn.analyze(inst_info, result.documents, people_info) if result.documents else None
        if analysis:
            run.input_tokens += analysis.input_tokens
            run.output_tokens += analysis.output_tokens
            run.cost_usd = Decimal(str(run.cost_usd)) + Decimal(str(round(analysis.cost_usd, 6)))
            _log(
                run,
                "reasoner_done",
                reasoner=rsn.name,
                model=analysis.model,
                findings=len(analysis.findings),
                input_tokens=analysis.input_tokens,
                output_tokens=analysis.output_tokens,
                cost_usd=round(analysis.cost_usd, 6),
            )
        _store_result(db, run, inst, result, analysis, prov.name)
        run.status, run.finished_at, run.last_error = RunStatus.succeeded.value, datetime.now(UTC), None
        _log(run, "succeeded", score=run.score, tier=run.tier, seconds=round(time.monotonic() - t0, 2))
    except (ProviderError, ReasonerError) as e:
        db.rollback()
        run = db.get(ResearchRun, run.id)
        assert run is not None
        _fail(run, str(e), e.transient, s)
    except Exception as e:  # noqa: BLE001 - never let one institution kill the worker
        db.rollback()
        log.exception("research run %s crashed", run.id)
        run = db.get(ResearchRun, run.id)
        assert run is not None
        _fail(run, f"Unexpected error: {e.__class__.__name__}", True, s)
    db.flush()
    _finish_job(db, run.job_id)
    db.commit()
    return run


def _fail(run: ResearchRun, message: str, transient: bool, s: Settings) -> None:
    if transient and run.attempts < run.max_attempts:
        delay = s.research_retry_base_seconds * 2 ** (run.attempts - 1)
        run.status, run.next_attempt_at = RunStatus.queued.value, datetime.now(UTC) + timedelta(seconds=delay)
        _log(run, "retry_scheduled", error=message, in_seconds=delay)
    else:
        run.status, run.finished_at = RunStatus.failed.value, datetime.now(UTC)
        _log(run, "failed", error=message, permanent=not transient)
    run.last_error = message


def _finish_job(db: Session, job_id: int) -> None:
    job = db.get(ResearchJob, job_id)
    if job is None:
        return
    rows = db.execute(
        select(ResearchRun.status, func.count(), func.coalesce(func.sum(ResearchRun.cost_usd), 0))
        .where(ResearchRun.job_id == job_id)
        .group_by(ResearchRun.status)
    ).all()
    counts = {st: n for st, n, _ in rows}
    job.cost_usd = sum((c for _, _, c in rows), Decimal(0))
    if sum(counts.get(st, 0) for st in TERMINAL) < job.total_items:
        return
    ok, bad, cancelled = (
        counts.get(RunStatus.succeeded.value, 0),
        counts.get(RunStatus.failed.value, 0),
        counts.get(RunStatus.cancelled.value, 0),
    )
    if job.status != JobStatus.cancelled.value:
        job.status = (
            JobStatus.completed
            if bad == 0 and cancelled == 0
            else JobStatus.failed
            if ok == 0 and cancelled == 0
            else JobStatus.partial
        ).value
    job.finished_at = datetime.now(UTC)


def _store_result(
    db: Session, run: ResearchRun, inst: Institution, result: ProviderResult, analysis: Any, provider_name: str
) -> None:
    docs: dict[str, SourceDocument] = {d.doc_id: d for d in result.documents}
    findings: list[Finding] = list(analysis.findings) if analysis else []
    # Profile facts come straight from the provider's official source, not from the model.
    prof = result.profile
    profile_confirmed = False
    campus_count: int | None = None
    if prof and prof.source:
        src = prof.source
        if prof.official_name and src.official and normalize_name(prof.official_name) in normalize_name(src.text):
            profile_confirmed = domain_from_website(prof.website) == domain_from_website(inst.website) or not inst.website
            findings.insert(
                0,
                Finding(
                    "profile",
                    "official_name",
                    f"Official name: {prof.official_name}",
                    "verified_fact",
                    90,
                    src.doc_id,
                    prof.official_name,
                ),
            )
        if prof.website:
            findings.insert(
                1,
                Finding(
                    "profile",
                    "website",
                    f"Official website: {domain_from_website(prof.website)}",
                    "verified_fact" if src.official else "source_inference",
                    85,
                    src.doc_id,
                    None,
                ),
            )
        if prof.city or prof.country:
            loc = ", ".join(x for x in (prof.city, prof.country) if x)
            findings.append(
                Finding(
                    "profile",
                    "location",
                    f"Located in {loc}",
                    "verified_fact" if loc.split(",")[0] in src.text else "source_inference",
                    80,
                    src.doc_id,
                    loc.split(",")[0] if loc.split(",")[0] in src.text else None,
                )
            )
        if prof.campus_count:
            stated = f"{prof.campus_count} campuses" in src.text
            campus_count = prof.campus_count if stated or prof.campus_count == 1 else None
            if prof.campus_count > 1:
                findings.append(
                    Finding(
                        "signal",
                        "multi_campus",
                        f"Operates {prof.campus_count} campuses",
                        "verified_fact" if stated else "source_inference",
                        85,
                        src.doc_id,
                        f"operating {prof.campus_count} campuses" if stated else None,
                    )
                )
    if campus_count is None:
        crm_campuses = db.scalar(select(func.count()).select_from(Campus).where(Campus.institution_id == inst.id)) or 0
        campus_count = crm_campuses or None

    now = datetime.now(UTC)
    for f in findings:
        if f.evidence_level == "verified_fact" and f.doc_id and f.excerpt is None and f.kind != "profile":
            f.evidence_level = "source_inference"
        d = docs.get(f.doc_id or "")
        db.add(
            ResearchEvidence(
                run_id=run.id,
                institution_id=inst.id,
                kind=f.kind,
                category=f.category,
                claim=f.claim[:2000],
                evidence_level=f.evidence_level if d or f.evidence_level == "hypothesis" else "hypothesis",
                confidence=f.confidence,
                excerpt=f.excerpt,
                source_url=d.url if d else None,
                source_title=d.title if d else None,
                published_at=d.published_at if d else None,
                retrieved_at=d.retrieved_at if d else None,
                provider=provider_name,
            )
        )

    people_in: list[PersonIn] = []
    for p in result.people:
        email = normalize_email(p.email)
        status = p.email_source if p.email_source in ("published", "provider_verified") else "none"
        notes = None
        if p.email_inferred or status == "none":
            if email:
                notes = "Provider suggested a pattern-based email; discarded (not verified)."
            email, status = None, "none"
        if status == "published" and email and email not in p.source.text.lower():
            email, status, notes = None, "none", "Email not shown on the cited source; discarded."
        role, _ = buying_role(p.designation)
        dups = [
            m for m in contact_duplicates(db, inst.id, p.first_name, p.last_name, email, p.linkedin_url) if m.exact or not email
        ]
        existing = dups[0].id if dups and dups[0].exact else None
        if dups and not existing:
            notes = (notes + " " if notes else "") + f"Possible match in CRM: {dups[0].label} (review)."
        db.add(
            ResearchContactCandidate(
                run_id=run.id,
                institution_id=inst.id,
                first_name=p.first_name,
                last_name=p.last_name,
                designation=p.designation,
                department=p.department,
                buying_role=role,
                email=email,
                email_status=status,
                phone=p.phone,
                linkedin_url=p.linkedin_url,
                verification_status="verified" if status == "provider_verified" else "unverified",
                source_url=p.source.url,
                excerpt=p.excerpt,
                retrieved_at=p.source.retrieved_at,
                provider=provider_name,
                notes=notes,
                existing_contact_id=existing,
            )
        )
        people_in.append(PersonIn(name=f"{p.first_name} {p.last_name}".strip(), designation=p.designation, email_status=status))
    db.flush()

    cfg = load_config(db)
    ev = [
        EvidenceIn(e.kind, e.category, e.evidence_level, e.confidence, e.published_at, e.source_url)
        for e in db.scalars(select(ResearchEvidence).where(ResearchEvidence.run_id == run.id))
    ]
    inst_info = {
        "name": inst.name,
        "institution_type": inst.institution_type,
        "student_size_range": None if inst.student_size_range == "unknown" else inst.student_size_range,
    }
    res = score(
        institution=inst_info,
        evidence=ev,
        people=people_in,
        profile_confirmed=profile_confirmed,
        campus_count=campus_count,
        stored_config=cfg,
        now=now,
    )
    missing = res.missing
    if analysis:
        known = {m["item"].lower() for m in missing}
        missing += [m for m in analysis.missing_information if m["item"].lower() not in known]
    if not result.documents:
        missing.insert(
            0,
            {
                "item": "Any source material",
                "how_to_verify": f"The '{provider_name}' provider returned nothing for this institution. "
                "Use a licensed provider or research manually.",
            },
        )
    run.score, run.tier, run.confidence, run.score_breakdown = res.score, res.tier, res.confidence, res.breakdown
    run.weights_used = cfg["weights"]
    run.summary = {
        "fit_summary": analysis.fit_summary if analysis else "No source material was found, so fit could not be assessed.",
        "primary_use_case": analysis.primary_use_case if analysis else None,
        "primary_use_case_label": USE_CASES.get(analysis.primary_use_case or "", None) if analysis else None,
        "use_case_rationale": analysis.use_case_rationale if analysis else "",
        "next_action": res.next_action,
        "missing_information": missing,
        "decision_makers": res.decision_makers,
        "profile_confirmed": profile_confirmed,
        "campus_count": campus_count,
        "reasoner_model": analysis.model if analysis else None,
        "documents_reviewed": len(result.documents),
    }
    inst.research_score, inst.research_tier, inst.last_researched_at = res.score, res.tier, now
    db.add(
        Activity(
            activity_type="research",
            subject=f"Automated research: score {res.score} (tier {res.tier})",
            body=f"{run.summary['fit_summary']}\nNext: {res.next_action}",
            institution_id=inst.id,
            details={"research_run_id": run.id, "score": res.score, "tier": res.tier},
            is_demo=inst.is_demo,
        )
    )


def rescore_run(db: Session, run: ResearchRun, cfg: dict[str, Any]) -> None:
    inst = run.institution
    ev = [EvidenceIn(e.kind, e.category, e.evidence_level, e.confidence, e.published_at, e.source_url) for e in run.evidence]
    people = [PersonIn(c.full_name, c.designation, c.email_status) for c in run.candidates]
    res = score(
        institution={
            "name": inst.name,
            "institution_type": inst.institution_type,
            "student_size_range": None if inst.student_size_range == "unknown" else inst.student_size_range,
        },
        evidence=ev,
        people=people,
        profile_confirmed=bool(run.summary.get("profile_confirmed")),
        campus_count=run.summary.get("campus_count"),
        stored_config=cfg,
        now=run.finished_at or datetime.now(UTC),
    )
    run.score, run.tier, run.confidence, run.score_breakdown, run.weights_used = (
        res.score,
        res.tier,
        res.confidence,
        res.breakdown,
        cfg["weights"],
    )
    run.summary = {**run.summary, "next_action": res.next_action, "decision_makers": res.decision_makers}
    if inst.last_researched_at and run.finished_at and abs((inst.last_researched_at - run.finished_at).total_seconds()) < 5:
        inst.research_score, inst.research_tier = res.score, res.tier


def latest_runs(db: Session) -> list[ResearchRun]:
    sub = (
        select(ResearchRun.institution_id, func.max(ResearchRun.finished_at).label("f"))
        .where(ResearchRun.status == RunStatus.succeeded.value)
        .group_by(ResearchRun.institution_id)
        .subquery()
    )
    return list(
        db.scalars(
            select(ResearchRun)
            .join(sub, (ResearchRun.institution_id == sub.c.institution_id) & (ResearchRun.finished_at == sub.c.f))
            .options(
                selectinload(ResearchRun.evidence), selectinload(ResearchRun.candidates), selectinload(ResearchRun.institution)
            )
        )
    )


def rescore_latest(db: Session, cfg: dict[str, Any]) -> int:
    runs = latest_runs(db)
    for r in runs:
        rescore_run(db, r, cfg)
    return len(runs)


def latest_succeeded_id(db: Session, institution_id: int) -> int | None:
    return db.scalar(
        select(ResearchRun.id)
        .where(ResearchRun.institution_id == institution_id, ResearchRun.status == RunStatus.succeeded.value)
        .order_by(ResearchRun.finished_at.desc(), ResearchRun.id.desc())
        .limit(1)
    )


# ---------------------------------------------------------------------------
# review and push
# ---------------------------------------------------------------------------
def review(
    db: Session, run: ResearchRun, user: User, evidence: dict[int, str], candidates: dict[int, str], note: str | None
) -> dict[str, int]:
    changed = 0
    ev_by_id = {e.id: e for e in run.evidence}
    ca_by_id = {c.id: c for c in run.candidates}
    for eid, status in evidence.items():
        e = ev_by_id.get(eid)
        if e is None:
            raise HTTPException(404, f"Evidence {eid} is not part of this research result")
        if e.pushed_at:
            raise HTTPException(409, f"Evidence {eid} was already pushed to the CRM")
        if status == ReviewStatus.approved.value and e.evidence_level == "hypothesis":
            raise HTTPException(422, "Hypotheses cannot be approved as findings; verify them first")
        e.review_status, e.reviewed_by_id, e.reviewed_at, e.review_note = status, user.id, datetime.now(UTC), note
        changed += 1
    for cid, status in candidates.items():
        c = ca_by_id.get(cid)
        if c is None:
            raise HTTPException(404, f"Contact candidate {cid} is not part of this research result")
        if c.pushed_at:
            raise HTTPException(409, f"Contact candidate {cid} was already pushed to the CRM")
        c.review_status, c.reviewed_by_id, c.reviewed_at = status, user.id, datetime.now(UTC)
        changed += 1
    audit.record(
        db,
        user,
        "review",
        "research_run",
        run.id,
        {
            "evidence": {str(k): v for k, v in evidence.items()},
            "candidates": {str(k): v for k, v in candidates.items()},
            "note": note,
        },
    )
    db.commit()
    return {"changed": changed}


def _email_suppressed(db: Session, email: str | None, institution: Institution, contact_id: int | None = None) -> bool:
    domains = {d for d in (institution.domain, email.split("@", 1)[1] if email and "@" in email else None) if d}
    conds: list[Any] = [ConsentRecord.domain.in_(domains)] if domains else []
    if email:
        conds.append(func.lower(ConsentRecord.email) == email.lower())
    if contact_id:
        conds.append(ConsentRecord.contact_id == contact_id)
    if not conds:
        return False
    return bool(
        db.scalar(
            select(func.count())
            .select_from(ConsentRecord)
            .where(ConsentRecord.status.in_(["opted_out", "suppressed"]), or_(*conds))
        )
    )


def push(db: Session, run: ResearchRun, user: User) -> dict[str, Any]:
    if run.status != RunStatus.succeeded.value:
        raise HTTPException(409, "Only completed research can be pushed")
    if latest_succeeded_id(db, run.institution_id) != run.id:
        raise HTTPException(409, "A newer research result exists for this institution; review that one instead")
    inst = run.institution
    lead = db.scalar(select(Lead).where(Lead.institution_id == inst.id, Lead.stage.not_in([s.value for s in CLOSED_STAGES])))
    now = datetime.now(UTC)
    findings = contacts_created = contacts_linked = 0
    skipped: list[str] = []
    for e in run.evidence:
        if e.review_status != ReviewStatus.approved.value or e.pushed_at:
            continue
        label = SIGNAL_CATEGORIES.get(e.category) or USE_CASES.get(e.category) or e.category.replace("_", " ").title()
        rf = ResearchFinding(
            institution_id=inst.id,
            lead_id=lead.id if lead else None,
            category=label[:100],
            summary=e.claim,
            source_url=e.source_url,
            confidence=e.confidence,
            user_id=user.id,
            is_demo=inst.is_demo,
            evidence_level=e.evidence_level,
            excerpt=e.excerpt,
            published_at=e.published_at,
            retrieved_at=e.retrieved_at,
            research_evidence_id=e.id,
            found_at=e.retrieved_at or now,
        )
        db.add(rf)
        e.pushed_at = now
        findings += 1
    for c in run.candidates:
        if c.review_status != ReviewStatus.approved.value or c.pushed_at:
            continue
        if _email_suppressed(db, c.email, inst, c.existing_contact_id):
            skipped.append(f"{c.full_name}: opted out or suppressed")
            continue
        dups = [m for m in contact_duplicates(db, inst.id, c.first_name, c.last_name, c.email, c.linkedin_url) if m.exact]
        if c.existing_contact_id or dups:
            c.pushed_contact_id = c.existing_contact_id or dups[0].id
            c.pushed_at = now
            contacts_linked += 1
            continue
        contact = Contact(
            institution_id=inst.id,
            salutation=c.salutation,
            first_name=c.first_name,
            last_name=c.last_name,
            designation=c.designation,
            department=c.department,
            email=c.email,
            phone=c.phone,
            linkedin_url=c.linkedin_url,
            verification_status=c.verification_status,
            source=f"Research agent ({c.provider}): {c.source_url}"[:200],
            last_verified_at=c.retrieved_at if c.verification_status == "verified" else None,
            notes=c.notes,
            is_demo=inst.is_demo,
        )
        db.add(contact)
        db.flush()
        audit.record(db, user, "create", "contact", contact.id, {**audit.snapshot(contact), "from_research_run": run.id})
        c.pushed_contact_id, c.pushed_at = contact.id, now
        contacts_created += 1
    if lead and run.score is not None and lead.score != run.score:
        audit.record(
            db,
            user,
            "update",
            "lead",
            lead.id,
            {"score": {"from": lead.score, "to": run.score}, "reason": f"research run {run.id}"},
        )
        lead.score = run.score
    run.pushed_at = now
    if findings or contacts_created or contacts_linked:
        db.add(
            Activity(
                activity_type="research",
                subject=f"Research approved and added to CRM (score {run.score}, tier {run.tier})",
                body=f"{findings} findings, {contacts_created} new contacts, {contacts_linked} linked to existing contacts.",
                institution_id=inst.id,
                lead_id=lead.id if lead else None,
                user_id=user.id,
                is_demo=inst.is_demo,
                details={"research_run_id": run.id},
            )
        )
    audit.record(
        db,
        user,
        "push",
        "research_run",
        run.id,
        {"findings": findings, "contacts_created": contacts_created, "contacts_linked": contacts_linked, "skipped": skipped},
    )
    db.commit()
    return {
        "findings": findings,
        "contacts_created": contacts_created,
        "contacts_linked": contacts_linked,
        "skipped": skipped,
        "lead_score_updated": bool(lead),
    }


def cancel_job(db: Session, job: ResearchJob, user: User) -> None:
    db.execute(
        update(ResearchRun)
        .where(ResearchRun.job_id == job.id, ResearchRun.status == RunStatus.queued.value)
        .values(status=RunStatus.cancelled.value, finished_at=datetime.now(UTC))
    )
    job.status = JobStatus.cancelled.value
    audit.record(db, user, "cancel", "research_job", job.id, {})
    db.flush()
    _finish_job(db, job.id)
    db.commit()


def retry_run(db: Session, run: ResearchRun, user: User) -> None:
    if run.status not in (RunStatus.failed.value, RunStatus.cancelled.value):
        raise HTTPException(409, "Only failed or cancelled research can be retried")
    run.status, run.attempts, run.next_attempt_at, run.finished_at = RunStatus.queued.value, 0, datetime.now(UTC), None
    _log(run, "manual_retry", by=user.id)
    job = db.get(ResearchJob, run.job_id)
    if job:
        job.status, job.finished_at = JobStatus.running.value, None
    audit.record(db, user, "retry", "research_run", run.id, {})
    db.commit()


# ---------------------------------------------------------------------------
# embedded worker
# ---------------------------------------------------------------------------
_worker_started = False


def start_embedded_worker(session_factory: Any) -> None:
    global _worker_started
    if _worker_started:
        return
    _worker_started = True

    def loop() -> None:
        while True:
            try:
                with session_factory() as db:
                    run = process_next(db)
                time.sleep(0.2 if run else 2.0)
            except Exception:  # noqa: BLE001
                log.exception("research worker loop error")
                time.sleep(5)

    threading.Thread(target=loop, name="research-worker", daemon=True).start()
    log.info("embedded research worker started")
