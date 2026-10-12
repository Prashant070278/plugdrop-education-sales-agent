import csv
import io
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app import audit
from app.deps import DB, CurrentUser, ManagerUser
from app.listing import ListParams, Page, apply_search, apply_sort, csv_safe, paginate
from app.models import Contact, Institution, VerificationStatus
from app.normalize import domain_from_website, normalize_name
from app.schemas import ContactCreate, ContactOut, ContactUpdate, DuplicateMatch, ImportResult, ImportRowResult
from app.services import (
    apply_contact_normalization,
    contact_duplicates,
    get_or_404,
    raise_if_exact,
    suppressed_contact_ids,
)

router = APIRouter(prefix="/contacts", tags=["contacts"])

SORTABLE = {
    "last_name": Contact.last_name,
    "first_name": Contact.first_name,
    "designation": Contact.designation,
    "verification_status": Contact.verification_status,
    "created_at": Contact.created_at,
    "updated_at": Contact.updated_at,
    "last_verified_at": Contact.last_verified_at,
    "institution": Institution.name,
}
CSV_FIELDS = [
    "first_name",
    "last_name",
    "designation",
    "department",
    "email",
    "phone",
    "linkedin_url",
    "verification_status",
    "source",
]


def enrich(db: DB, items: list[Contact]) -> list[ContactOut]:
    blocked = suppressed_contact_ids(db, items)
    out = []
    for c in items:
        o = ContactOut.model_validate(c)
        o.suppressed = c.id in blocked
        out.append(o)
    return out


def _filtered(
    params: ListParams,
    institution_id: int | None,
    verification_status: VerificationStatus | None,
    department: str | None,
    demo: bool | None,
) -> Any:
    stmt = select(Contact).join(Institution).options(selectinload(Contact.institution))
    stmt = apply_search(
        stmt,
        params.q,
        [Contact.first_name, Contact.last_name, Contact.email, Contact.designation, Contact.department, Institution.name],
    )
    if institution_id:
        stmt = stmt.where(Contact.institution_id == institution_id)
    if verification_status:
        stmt = stmt.where(Contact.verification_status == verification_status.value)
    if department:
        stmt = stmt.where(Contact.department.ilike(f"%{department}%"))
    if demo is not None:
        stmt = stmt.where(Contact.is_demo.is_(demo))
    return apply_sort(stmt, params.sort, SORTABLE, Contact.last_name.asc())


@router.get("", response_model=Page[ContactOut])
def list_contacts(
    _: CurrentUser,
    db: DB,
    params: Annotated[ListParams, Depends()],
    institution_id: int | None = None,
    verification_status: VerificationStatus | None = None,
    department: str | None = None,
    demo: bool | None = None,
) -> Page[ContactOut]:
    items, total = paginate(db, _filtered(params, institution_id, verification_status, department, demo), params)
    return Page(items=enrich(db, items), total=total, page=params.page, page_size=params.page_size)


@router.get("/duplicates", response_model=list[DuplicateMatch])
def check_duplicates(
    _: CurrentUser,
    db: DB,
    first_name: str = "",
    last_name: str = "",
    institution_id: int | None = None,
    email: str | None = None,
    linkedin_url: str | None = None,
    exclude_id: int | None = None,
) -> list[DuplicateMatch]:
    return contact_duplicates(db, institution_id, first_name, last_name, email, linkedin_url, exclude_id)


@router.get("/export.csv")
def export_csv(
    _: CurrentUser,
    db: DB,
    params: Annotated[ListParams, Depends()],
    institution_id: int | None = None,
    verification_status: VerificationStatus | None = None,
    department: str | None = None,
    demo: bool | None = None,
) -> Response:
    stmt = _filtered(params, institution_id, verification_status, department, demo)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "institution", "institution_website", *CSV_FIELDS, "last_verified_at", "is_demo"])
    for c in db.scalars(stmt).yield_per(500):
        w.writerow(
            csv_safe(
                [
                    c.id,
                    c.institution.name,
                    c.institution.website or "",
                    c.first_name,
                    c.last_name,
                    c.designation or "",
                    c.department or "",
                    c.email or "",
                    c.phone or "",
                    c.linkedin_url or "",
                    c.verification_status,
                    c.source or "",
                    c.last_verified_at.isoformat() if c.last_verified_at else "",
                    c.is_demo,
                ]
            )
        )

    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="contacts.csv"'})


@router.post("/import", response_model=ImportResult)
async def import_csv(
    user: CurrentUser, db: DB, request: Request, file: UploadFile = File(...), dry_run: bool = Query(True)
) -> ImportResult:
    """Columns: institution_website or institution (name) to link the contact, plus contact fields."""
    raw = await file.read()
    if len(raw) > 5_000_000:
        raise HTTPException(413, "CSV must be 5 MB or smaller")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise HTTPException(422, "CSV must be UTF-8 encoded") from e
    reader = csv.DictReader(io.StringIO(text))
    cols = set(reader.fieldnames or [])
    if "first_name" not in cols or not ({"institution", "institution_website"} & cols):
        raise HTTPException(422, "CSV needs 'first_name' and 'institution' or 'institution_website' columns")
    results: list[ImportRowResult] = []
    for n, row in enumerate(reader, start=2):
        if n > 5001:
            raise HTTPException(422, "CSV may contain at most 5,000 rows")
        domain = domain_from_website(row.get("institution_website"))
        inst = None
        if domain:
            inst = db.scalar(select(Institution).where(Institution.domain == domain))
        if inst is None and (row.get("institution") or "").strip():
            matches = db.scalars(
                select(Institution).where(Institution.normalized_name == normalize_name(row["institution"]))
            ).all()
            if len(matches) > 1:
                results.append(
                    ImportRowResult(row=n, status="error", message="Institution name is ambiguous; add institution_website")
                )
                continue
            inst = matches[0] if matches else None
        if inst is None:
            results.append(ImportRowResult(row=n, status="error", message="Institution not found; import it first"))
            continue
        payload: dict[str, Any] = {k: (row.get(k) or "").strip() for k in CSV_FIELDS if k in row}
        if not payload.get("verification_status"):
            payload.pop("verification_status", None)
        payload["institution_id"] = inst.id
        try:
            body = ContactCreate.model_validate(payload)
        except ValidationError as e:
            msg = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
            results.append(ImportRowResult(row=n, status="error", message=msg))
            continue
        exact = [
            d
            for d in contact_duplicates(
                db, inst.id, body.first_name, body.last_name, body.email, str(body.linkedin_url) if body.linkedin_url else None
            )
            if d.exact
        ]
        if exact:
            results.append(ImportRowResult(row=n, status="duplicate", message=exact[0].reason, id=exact[0].id))
            continue
        data = apply_contact_normalization(body.model_dump())
        data["verification_status"] = body.verification_status.value
        data["source"] = data.get("source") or f"CSV import: {file.filename}"
        contact = Contact(**data)
        try:
            with db.begin_nested():
                db.add(contact)
                db.flush()
        except IntegrityError:
            results.append(ImportRowResult(row=n, status="duplicate", message="Duplicate within this file"))
            continue
        results.append(ImportRowResult(row=n, status="created", message="OK", id=None if dry_run else contact.id))
    if dry_run:
        db.rollback()
    else:
        audit.record(
            db,
            user,
            "import",
            "contact",
            None,
            {"file": file.filename, "created": sum(r.status == "created" for r in results)},
            request,
        )
        db.commit()
    return ImportResult(
        dry_run=dry_run,
        created=sum(r.status == "created" for r in results),
        duplicates=sum(r.status == "duplicate" for r in results),
        errors=sum(r.status == "error" for r in results),
        rows=results,
    )


@router.post("", response_model=ContactOut, status_code=201)
def create_contact(body: ContactCreate, user: CurrentUser, db: DB, request: Request) -> ContactOut:
    get_or_404(db, Institution, body.institution_id, "Institution")
    raise_if_exact(
        contact_duplicates(
            db,
            body.institution_id,
            body.first_name,
            body.last_name,
            body.email,
            str(body.linkedin_url) if body.linkedin_url else None,
        ),
        "contact",
    )
    data = apply_contact_normalization(body.model_dump())
    data["verification_status"] = body.verification_status.value
    contact = Contact(**data)
    db.add(contact)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(409, "A contact with this email or LinkedIn URL already exists") from e
    audit.record(db, user, "create", "contact", contact.id, audit.snapshot(contact), request)
    db.commit()
    db.refresh(contact)
    return enrich(db, [contact])[0]


@router.get("/{contact_id}", response_model=ContactOut)
def get_contact(contact_id: int, _: CurrentUser, db: DB) -> ContactOut:
    return enrich(db, [get_or_404(db, Contact, contact_id, "Contact")])[0]


@router.patch("/{contact_id}", response_model=ContactOut)
def update_contact(contact_id: int, body: ContactUpdate, user: CurrentUser, db: DB, request: Request) -> ContactOut:
    contact = get_or_404(db, Contact, contact_id, "Contact")
    data = body.model_dump(exclude_unset=True)
    if data.get("institution_id"):
        get_or_404(db, Institution, data["institution_id"], "Institution")
    if "email" in data or "linkedin_url" in data:
        raise_if_exact(
            [
                d
                for d in contact_duplicates(
                    db,
                    None,
                    "",
                    "",
                    data.get("email"),
                    str(data["linkedin_url"]) if data.get("linkedin_url") else None,
                    exclude_id=contact.id,
                )
            ],
            "contact",
        )
    data = apply_contact_normalization(data)
    before = audit.snapshot(contact)
    for k, v in data.items():
        setattr(contact, k, v.value if hasattr(v, "value") else v)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(409, "A contact with this email or LinkedIn URL already exists") from e
    audit.record(db, user, "update", "contact", contact.id, audit.diff(before, audit.snapshot(contact)), request)
    db.commit()
    db.refresh(contact)
    return enrich(db, [contact])[0]


@router.delete("/{contact_id}", status_code=204)
def delete_contact(contact_id: int, user: ManagerUser, db: DB, request: Request) -> None:
    contact = get_or_404(db, Contact, contact_id, "Contact")
    snap = audit.snapshot(contact)
    db.delete(contact)
    audit.record(db, user, "delete", "contact", contact_id, snap, request)
    db.commit()


@router.get("/{contact_id}/suppressed", response_model=bool)
def contact_suppressed(contact_id: int, _: CurrentUser, db: DB) -> bool:
    c = get_or_404(db, Contact, contact_id, "Contact")
    return c.id in suppressed_contact_ids(db, [c])
