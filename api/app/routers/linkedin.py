from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.deps import DB, CurrentUser
from app.linkedin import service as L
from app.models import Contact, LinkedinItem
from app.services import get_or_404

router = APIRouter(prefix="/linkedin", tags=["linkedin"])


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: str
    contact_id: int | None
    lead_id: int | None
    institution_id: int | None
    title: str
    body: str
    quality: dict[str, Any]
    status: str
    approved_at: datetime | None
    done_at: datetime | None
    is_demo: bool
    created_at: datetime


@router.get("/items", response_model=list[ItemOut])
def list_items(
    _: CurrentUser, db: DB, contact_id: int | None = None, lead_id: int | None = None, kind: str | None = None
) -> list[LinkedinItem]:
    q = select(LinkedinItem).where(LinkedinItem.status != "discarded")
    if contact_id:
        q = q.where(LinkedinItem.contact_id == contact_id)
    if lead_id:
        q = q.where(LinkedinItem.lead_id == lead_id)
    if kind:
        q = q.where(LinkedinItem.kind == kind)
    return list(db.scalars(q.order_by(LinkedinItem.id.desc()).limit(200)))


class GenerateIn(BaseModel):
    kind: Literal["note", "connection_draft", "message_draft"]
    contact_id: int


@router.post("/generate", response_model=ItemOut, status_code=201)
def generate(body: GenerateIn, user: CurrentUser, db: DB) -> LinkedinItem:
    c = get_or_404(db, Contact, body.contact_id, "Contact")
    fn = {"note": L.research_note, "connection_draft": L.connection_draft, "message_draft": L.message_draft}[body.kind]
    return fn(db, user, c)


@router.post("/content-ideas", response_model=list[ItemOut], status_code=201)
def content_ideas(user: CurrentUser, db: DB) -> list[LinkedinItem]:
    return L.content_ideas(db, user)


class EditIn(BaseModel):
    body: str = Field(min_length=3, max_length=5000)


@router.patch("/items/{item_id}", response_model=ItemOut)
def edit(item_id: int, body: EditIn, user: CurrentUser, db: DB) -> LinkedinItem:
    return L.edit(db, get_or_404(db, LinkedinItem, item_id, "LinkedIn item"), user, body.body)


@router.post("/items/{item_id}/approve", response_model=ItemOut)
def approve(item_id: int, user: CurrentUser, db: DB) -> LinkedinItem:
    return L.approve(db, get_or_404(db, LinkedinItem, item_id, "LinkedIn item"), user)


class DoneIn(BaseModel):
    note: str | None = Field(None, max_length=300)


@router.post("/items/{item_id}/done", response_model=ItemOut)
def done(item_id: int, body: DoneIn, user: CurrentUser, db: DB) -> LinkedinItem:
    return L.mark_done(db, get_or_404(db, LinkedinItem, item_id, "LinkedIn item"), user, body.note)


@router.post("/items/{item_id}/discard", response_model=ItemOut)
def discard(item_id: int, _: CurrentUser, db: DB) -> LinkedinItem:
    item = get_or_404(db, LinkedinItem, item_id, "LinkedIn item")
    item.status = "discarded"
    db.commit()
    return item
