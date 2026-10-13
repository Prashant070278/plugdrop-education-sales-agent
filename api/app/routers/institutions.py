import csv
import io
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app import audit
from app.deps import DB, CurrentUser, ManagerUser
from app.listing import ListParams, Page, apply_search, apply_sort, csv_safe, paginate, pairs
from app.models import CLOSED_STAGES, Campus, Contact, Institution, InstitutionType, Lead, LeadStage, StudentSize
from app.schemas import (
    CampusIn,
    CampusOut,
    DuplicateMatch,
    ImportResult,
    ImportRowResult,
    InstitutionCreate,
    InstitutionDetail,
    InstitutionOut,
    InstitutionUpdate,
)
from app.services import (
    apply_institution_normalization,
    ensure_user,
    get_or_404,
    institution_duplicates,
    raise_if_exact,
    suppressed_institution_ids,
)

router = APIRouter(prefix="/institutions", tags=["institutions"])

SORTABLE = {
    "name": Institution.name,
    "country": Institution.country,
    "city": Institution.city,
    "institution_type": Institution.institution_type,
    "created_at": Institution.created_at,
    "updated_at": Institution.updated_at,
    "last_verified_at": Institution.last_verified_at,
    "research_score": Institution.research_score,
    "last_researched_at": Institution.last_researched_at,
}
CSV_FIELDS = ["name", "website", "institution_type", "country", "city", "student_size_range", "courses", "source"]


def enrich(db: DB, items: list[Institution], detail: bool = False) -> list[Any]:
    ids = [i.id for i in items]
    contact_counts = (
        pairs(
            db.execute(
                select(Contact.institution_id, func.count())
                .where(Contact.institution_id.in_(ids))
                .group_by(Contact.institution_id)
            ).all()
        )
        if ids
        else {}
    )
    campus_counts = (
        pairs(
            db.execute(
                select(Campus.institution_id, func.count()).where(Campus.institution_id.in_(ids)).group_by(Campus.institution_id)
            ).all()
        )
        if ids
        else {}
    )
    stages: dict[int, str] = {}
    if ids:
        for inst_id, stage in db.execute(
            select(Lead.institution_id, Lead.stage)
            .where(Lead.institution_id.in_(ids))
            .order_by(Lead.institution_id, Lead.updated_at)
        ):
            if inst_id not in stages or stages[inst_id] in {s.value for s in CLOSED_STAGES}:
                stages[inst_id] = stage
    suppressed = suppressed_institution_ids(db, ids)
    cls = InstitutionDetail if detail else InstitutionOut
    out = []
    for i in items:
        o = cls.model_validate(i)
        o.contact_count = contact_counts.get(i.id, 0)
        o.campus_count = campus_counts.get(i.id, 0)
        o.lead_stage = LeadStage(stages[i.id]) if i.id in stages else None
        o.suppressed = i.id in suppressed
        out.append(o)
    return out


def _filtered(
    params: ListParams,
    institution_type: list[InstitutionType] | None,
    country: str | None,
    student_size_range: StudentSize | None,
    owner_id: int | None,
    demo: bool | None,
    tier: str | None = None,
) -> Any:
    stmt = select(Institution).options(selectinload(Institution.owner))
    stmt = apply_search(stmt, params.q, [Institution.name, Institution.city, Institution.country, Institution.domain])
    if institution_type:
        stmt = stmt.where(Institution.institution_type.in_([t.value for t in institution_type]))
    if country:
        stmt = stmt.where(func.lower(Institution.country) == country.lower())
    if student_size_range:
        stmt = stmt.where(Institution.student_size_range == student_size_range.value)
    if owner_id:
        stmt = stmt.where(Institution.owner_id == owner_id)
    if demo is not None:
        stmt = stmt.where(Institution.is_demo.is_(demo))
    if tier == "none":
        stmt = stmt.where(Institution.research_tier.is_(None))
    elif tier:
        stmt = stmt.where(Institution.research_tier == tier)
    return apply_sort(stmt, params.sort, SORTABLE, Institution.name.asc())


@router.get("", response_model=Page[InstitutionOut])
def list_institutions(
    _: CurrentUser,
    db: DB,
    params: Annotated[ListParams, Depends()],
    institution_type: Annotated[list[InstitutionType] | None, Query()] = None,
    country: str | None = None,
    student_size_range: StudentSize | None = None,
    owner_id: int | None = None,
    demo: bool | None = None,
    tier: Annotated[str | None, Query(pattern="^(A|B|C|D|none)$")] = None,
) -> Page[InstitutionOut]:
    stmt = _filtered(params, institution_type, country, student_size_range, owner_id, demo, tier)
    items, total = paginate(db, stmt, params)
    return Page(items=enrich(db, items), total=total, page=params.page, page_size=params.page_size)


@router.get("/countries", response_model=list[str])
def countries(_: CurrentUser, db: DB) -> list[str]:
    return [
        c
        for (c,) in db.execute(
            select(Institution.country).where(Institution.country != "").distinct().order_by(Institution.country)
        )
    ]


@router.get("/duplicates", response_model=list[DuplicateMatch])
def check_duplicates(
    _: CurrentUser,
    db: DB,
    name: str = Query(min_length=2),
    website: str | None = None,
    country: str = "",
    city: str = "",
    exclude_id: int | None = None,
) -> list[DuplicateMatch]:
    return institution_duplicates(db, name, website, country.strip(), city.strip(), exclude_id)


@router.get("/export.csv")
def export_csv(
    _: CurrentUser,
    db: DB,
    params: Annotated[ListParams, Depends()],
    institution_type: Annotated[list[InstitutionType] | None, Query()] = None,
    country: str | None = None,
    student_size_range: StudentSize | None = None,
    owner_id: int | None = None,
    demo: bool | None = None,
) -> Response:
    stmt = _filtered(params, institution_type, country, student_size_range, owner_id, demo)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", *CSV_FIELDS, "domain", "last_verified_at", "is_demo"])
    for i in db.scalars(stmt).yield_per(500):
        w.writerow(
            csv_safe(
                [
                    i.id,
                    i.name,
                    i.website or "",
                    i.institution_type,
                    i.country,
                    i.city,
                    i.student_size_range,
                    "; ".join(i.courses),
                    i.source or "",
                    i.domain or "",
                    i.last_verified_at.isoformat() if i.last_verified_at else "",
                    i.is_demo,
                ]
            )
        )

    return Response(
        buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="institutions.csv"'}
    )


@router.post("/import", response_model=ImportResult)
async def import_csv(
    user: CurrentUser, db: DB, request: Request, file: UploadFile = File(...), dry_run: bool = Query(True)
) -> ImportResult:
    raw = await file.read()
    if len(raw) > 5_000_000:
        raise HTTPException(413, "CSV must be 5 MB or smaller")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise HTTPException(422, "CSV must be UTF-8 encoded") from e
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "name" not in reader.fieldnames:
        raise HTTPException(422, f"CSV needs a 'name' column. Supported columns: {', '.join(CSV_FIELDS)}")
    results: list[ImportRowResult] = []
    for n, row in enumerate(reader, start=2):
        if n > 5001:
            raise HTTPException(422, "CSV may contain at most 5,000 rows")
        payload = {k: (row.get(k) or "").strip() for k in CSV_FIELDS if k in row}
        payload["courses"] = [c for c in (payload.get("courses") or "").split(";") if c.strip()]
        for k in ("institution_type", "student_size_range"):
            if not payload.get(k):
                payload.pop(k, None)
        try:
            body = InstitutionCreate.model_validate(payload)
        except ValidationError as e:
            msg = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
            results.append(ImportRowResult(row=n, status="error", message=msg))
            continue
        dups = [
            d
            for d in institution_duplicates(
                db, body.name, str(body.website) if body.website else None, body.country.strip(), body.city.strip()
            )
            if d.exact
        ]
        if dups:
            results.append(ImportRowResult(row=n, status="duplicate", message=dups[0].reason, id=dups[0].id))
            continue
        data = apply_institution_normalization(body.model_dump(exclude={"campuses"}, mode="python"))
        data["institution_type"] = body.institution_type.value
        data["student_size_range"] = body.student_size_range.value
        data["source"] = data.get("source") or f"CSV import: {file.filename}"
        inst = Institution(**data)
        try:
            with db.begin_nested():
                db.add(inst)
                db.flush()
        except IntegrityError:
            results.append(ImportRowResult(row=n, status="duplicate", message="Duplicate within this file"))
            continue
        results.append(ImportRowResult(row=n, status="created", message="OK", id=None if dry_run else inst.id))
    if dry_run:
        db.rollback()
    else:
        created = sum(1 for r in results if r.status == "created")
        audit.record(db, user, "import", "institution", None, {"file": file.filename, "created": created}, request)
        db.commit()
    return ImportResult(
        dry_run=dry_run,
        created=sum(r.status == "created" for r in results),
        duplicates=sum(r.status == "duplicate" for r in results),
        errors=sum(r.status == "error" for r in results),
        rows=results,
    )


@router.post("", response_model=InstitutionDetail, status_code=201)
def create_institution(body: InstitutionCreate, user: CurrentUser, db: DB, request: Request) -> Any:
    ensure_user(db, body.owner_id)
    raise_if_exact(
        institution_duplicates(
            db, body.name, str(body.website) if body.website else None, body.country.strip(), body.city.strip()
        ),
        "institution",
    )
    data = apply_institution_normalization(body.model_dump(exclude={"campuses"}))
    data["institution_type"] = body.institution_type.value
    data["student_size_range"] = body.student_size_range.value
    if data.get("owner_id") is None:
        data["owner_id"] = user.id
    inst = Institution(**data)
    inst.campuses = [Campus(**c.model_dump()) for c in body.campuses]
    db.add(inst)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(409, "Duplicate institution or campus name") from e
    audit.record(db, user, "create", "institution", inst.id, audit.snapshot(inst), request)
    db.commit()
    db.refresh(inst)
    return enrich(db, [inst], detail=True)[0]


@router.get("/{institution_id}", response_model=InstitutionDetail)
def get_institution(institution_id: int, _: CurrentUser, db: DB) -> Any:
    inst = get_or_404(db, Institution, institution_id, "Institution")
    return enrich(db, [inst], detail=True)[0]


@router.patch("/{institution_id}", response_model=InstitutionDetail)
def update_institution(institution_id: int, body: InstitutionUpdate, user: CurrentUser, db: DB, request: Request) -> Any:
    inst = get_or_404(db, Institution, institution_id, "Institution")
    data = body.model_dump(exclude_unset=True)
    ensure_user(db, data.get("owner_id"))
    if any(k in data for k in ("name", "website", "country", "city")):
        raise_if_exact(
            institution_duplicates(
                db,
                data.get("name") or inst.name,
                str(data["website"]) if data.get("website") else (inst.website if "website" not in data else None),
                (data.get("country") if "country" in data else inst.country) or "",
                (data.get("city") if "city" in data else inst.city) or "",
                exclude_id=inst.id,
            ),
            "institution",
        )
    data = apply_institution_normalization(data)
    before = audit.snapshot(inst)
    for k, v in data.items():
        setattr(inst, k, v.value if hasattr(v, "value") else v)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(409, "Another institution already uses this name/location or website") from e
    audit.record(db, user, "update", "institution", inst.id, audit.diff(before, audit.snapshot(inst)), request)
    db.commit()
    return enrich(db, [inst], detail=True)[0]


@router.delete("/{institution_id}", status_code=204)
def delete_institution(institution_id: int, user: ManagerUser, db: DB, request: Request) -> None:
    inst = get_or_404(db, Institution, institution_id, "Institution")
    if db.scalar(select(func.count()).select_from(Contact).where(Contact.institution_id == inst.id)):
        raise HTTPException(409, "Remove or move this institution's contacts before deleting it")
    snap = audit.snapshot(inst)
    db.delete(inst)
    audit.record(db, user, "delete", "institution", institution_id, snap, request)
    db.commit()


# ----- campuses -----
@router.post("/{institution_id}/campuses", response_model=CampusOut, status_code=201)
def add_campus(institution_id: int, body: CampusIn, user: CurrentUser, db: DB, request: Request) -> Campus:
    get_or_404(db, Institution, institution_id, "Institution")
    campus = Campus(institution_id=institution_id, **body.model_dump())
    db.add(campus)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(409, "This institution already has a campus with that name") from e
    audit.record(db, user, "create", "campus", campus.id, audit.snapshot(campus), request)
    db.commit()
    return campus


@router.delete("/{institution_id}/campuses/{campus_id}", status_code=204)
def delete_campus(institution_id: int, campus_id: int, user: CurrentUser, db: DB, request: Request) -> None:
    campus = get_or_404(db, Campus, campus_id, "Campus")
    if campus.institution_id != institution_id:
        raise HTTPException(404, "Campus not found for this institution")
    snap = audit.snapshot(campus)
    db.delete(campus)
    audit.record(db, user, "delete", "campus", campus_id, snap, request)
    db.commit()
