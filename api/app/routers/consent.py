from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from app import audit
from app.deps import DB, CurrentUser, ManagerUser
from app.listing import ListParams, Page, apply_search, paginate
from app.models import ConsentRecord, ConsentStatus, Contact
from app.schemas import ConsentCreate, ConsentOut
from app.services import get_or_404

router = APIRouter(prefix="/consent", tags=["consent & suppression"])


@router.get("", response_model=Page[ConsentOut])
def list_records(
    _: CurrentUser,
    db: DB,
    params: Annotated[ListParams, Depends()],
    status: ConsentStatus | None = None,
    contact_id: int | None = None,
) -> Page[ConsentOut]:
    stmt = apply_search(select(ConsentRecord), params.q, [ConsentRecord.email, ConsentRecord.domain, ConsentRecord.reason])
    if status:
        stmt = stmt.where(ConsentRecord.status == status.value)
    if contact_id:
        stmt = stmt.where(ConsentRecord.contact_id == contact_id)
    items, total = paginate(db, stmt.order_by(ConsentRecord.recorded_at.desc()), params)
    return Page(items=[ConsentOut.model_validate(i) for i in items], total=total, page=params.page, page_size=params.page_size)


@router.post("", response_model=ConsentOut, status_code=201)
def add_record(body: ConsentCreate, user: CurrentUser, db: DB, request: Request) -> ConsentRecord:
    """Any user may record an opt-out (it only restricts outreach); recording consent needs a manager."""
    if not (body.contact_id or body.email or body.domain or body.phone):
        raise HTTPException(422, "Provide a contact, email, phone or domain")
    if body.status == ConsentStatus.consented and user.role == "rep":
        raise HTTPException(403, "Only managers can record consent")
    if body.contact_id:
        get_or_404(db, Contact, body.contact_id, "Contact")
    data = body.model_dump()
    data["channel"], data["status"] = body.channel.value, body.status.value
    data["email"] = data["email"].lower() if data["email"] else None
    rec = ConsentRecord(**data, recorded_by_id=user.id)
    db.add(rec)
    db.flush()
    audit.record(db, user, "create", "consent_record", rec.id, audit.snapshot(rec), request)
    db.commit()
    return rec


@router.delete("/{record_id}", status_code=204)
def remove_record(record_id: int, user: ManagerUser, db: DB, request: Request) -> None:
    rec = get_or_404(db, ConsentRecord, record_id, "Consent record")
    snap = audit.snapshot(rec)
    db.delete(rec)
    audit.record(db, user, "delete", "consent_record", record_id, snap, request)
    db.commit()
