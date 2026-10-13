from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.deps import DB, CurrentUser, ManagerUser
from app.listing import ListParams, Page, paginate
from app.models import AuditEvent
from app.schemas import AuditOut

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=Page[AuditOut])
def list_audit(
    _: ManagerUser,
    db: DB,
    params: Annotated[ListParams, Depends()],
    entity_type: str | None = None,
    entity_id: int | None = None,
    actor_id: int | None = None,
) -> Page[AuditOut]:
    stmt = select(AuditEvent).options(selectinload(AuditEvent.actor))
    if entity_type:
        stmt = stmt.where(AuditEvent.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditEvent.entity_id == entity_id)
    if actor_id:
        stmt = stmt.where(AuditEvent.actor_id == actor_id)
    items, total = paginate(db, stmt.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()), params)
    return Page(items=[AuditOut.model_validate(i) for i in items], total=total, page=params.page, page_size=params.page_size)


@router.get("/entity/{entity_type}/{entity_id}", response_model=list[AuditOut])
def entity_history(entity_type: str, entity_id: int, _: CurrentUser, db: DB) -> list[AuditEvent]:
    """History of a single record; visible to every signed-in user."""
    return list(
        db.scalars(
            select(AuditEvent)
            .options(selectinload(AuditEvent.actor))
            .where(AuditEvent.entity_type == entity_type, AuditEvent.entity_id == entity_id)
            .order_by(AuditEvent.created_at.desc())
            .limit(200)
        )
    )
