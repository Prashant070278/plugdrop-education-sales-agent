from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import audit
from app.deps import DB, RANK, CurrentUser
from app.listing import ListParams, Page, apply_search, apply_sort, paginate, pairs
from app.models import Contact, Institution, Lead, Opportunity, Role, Task, TaskPriority, TaskStatus
from app.schemas import TaskCreate, TaskOut, TaskUpdate
from app.services import ensure_exists, ensure_user, get_or_404

router = APIRouter(prefix="/tasks", tags=["tasks"])

SORTABLE = {"due_at": Task.due_at, "priority": Task.priority, "created_at": Task.created_at, "title": Task.title}


def to_out(db: DB, tasks: list[Task]) -> list[TaskOut]:
    inst_ids = {t.institution_id for t in tasks if t.institution_id}
    names = pairs(db.execute(select(Institution.id, Institution.name).where(Institution.id.in_(inst_ids)))) if inst_ids else {}
    out = []
    for t in tasks:
        o = TaskOut.model_validate(t)
        o.institution_name = names.get(t.institution_id) if t.institution_id else None
        o.source_activity_subject = t.source_activity.subject if t.source_activity else None
        out.append(o)
    return out


@router.get("", response_model=Page[TaskOut])
def list_tasks(
    user: CurrentUser,
    db: DB,
    params: Annotated[ListParams, Depends()],
    status: TaskStatus | None = None,
    priority: TaskPriority | None = None,
    owner_id: int | None = None,
    mine: bool = False,
    due: Literal["overdue", "today", "week"] | None = None,
    lead_id: int | None = None,
    institution_id: int | None = None,
) -> Page[TaskOut]:
    stmt = apply_search(
        select(Task).options(selectinload(Task.owner), selectinload(Task.source_activity)),
        params.q,
        [Task.title, Task.description],
    )
    if status:
        stmt = stmt.where(Task.status == status.value)
    if priority:
        stmt = stmt.where(Task.priority == priority.value)
    if mine:
        stmt = stmt.where(Task.owner_id == user.id)
    elif owner_id:
        stmt = stmt.where(Task.owner_id == owner_id)
    if lead_id:
        stmt = stmt.where(Task.lead_id == lead_id)
    if institution_id:
        stmt = stmt.where(Task.institution_id == institution_id)
    now = datetime.now(UTC)
    if due == "overdue":
        stmt = stmt.where(Task.status == TaskStatus.open.value, Task.due_at < now)
    elif due in ("today", "week"):
        end = now.replace(hour=23, minute=59, second=59) + (timedelta(days=6) if due == "week" else timedelta())
        stmt = stmt.where(Task.status == TaskStatus.open.value, Task.due_at <= end)
    stmt = apply_sort(stmt, params.sort, SORTABLE, Task.due_at.asc().nullslast())
    items, total = paginate(db, stmt, params)
    return Page(items=to_out(db, items), total=total, page=params.page, page_size=params.page_size)


@router.post("", response_model=TaskOut, status_code=201)
def create_task(body: TaskCreate, user: CurrentUser, db: DB, request: Request) -> TaskOut:
    ensure_user(db, body.owner_id)
    ensure_exists(db, Lead, body.lead_id, "Lead")
    ensure_exists(db, Institution, body.institution_id, "Institution")
    ensure_exists(db, Contact, body.contact_id, "Contact")
    ensure_exists(db, Opportunity, body.opportunity_id, "Opportunity")
    if body.reminder_at and body.due_at and body.reminder_at > body.due_at:
        raise HTTPException(422, "Reminder must be before the due date")
    data = body.model_dump()
    data["priority"] = body.priority.value
    task = Task(**data)
    if task.owner_id is None:
        task.owner_id = user.id
    if task.lead_id and not task.institution_id:
        lead = db.get(Lead, task.lead_id)
        task.institution_id = lead.institution_id if lead else None
    # Every task has an owner, a due date, a reason and the activity it came from (Stage 4).
    if task.due_at is None:
        task.due_at = (datetime.now(UTC) + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    task.reason = "manual"
    db.add(task)
    db.flush()
    from app.engagement.coordination import record_activity

    src = record_activity(
        db,
        "task",
        f"Task created by {user.full_name}: {task.title}",
        lead_id=task.lead_id,
        institution_id=task.institution_id,
        contact_id=task.contact_id,
        user_id=user.id,
        details={"task_id": task.id, "reason": "manual"},
        is_demo=task.is_demo,
    )
    task.source_activity_id = src.id
    audit.record(db, user, "create", "task", task.id, audit.snapshot(task), request)
    db.commit()
    db.refresh(task)
    return to_out(db, [task])[0]


@router.patch("/{task_id}", response_model=TaskOut)
def update_task(task_id: int, body: TaskUpdate, user: CurrentUser, db: DB, request: Request) -> TaskOut:
    task = get_or_404(db, Task, task_id, "Task")
    if RANK[user.role] < RANK[Role.manager.value] and task.owner_id not in (None, user.id):
        raise HTTPException(403, "Reps can only edit their own tasks")
    data = body.model_dump(exclude_unset=True)
    ensure_user(db, data.get("owner_id"))
    before = audit.snapshot(task)
    for k, v in data.items():
        setattr(task, k, v.value if hasattr(v, "value") else v)
    if "status" in data:
        task.completed_at = datetime.now(UTC) if task.status == TaskStatus.done.value else None
    db.flush()
    audit.record(db, user, "update", "task", task.id, audit.diff(before, audit.snapshot(task)), request)
    db.commit()
    db.refresh(task)
    return to_out(db, [task])[0]


@router.delete("/{task_id}", status_code=204)
def delete_task(task_id: int, user: CurrentUser, db: DB, request: Request) -> None:
    task = get_or_404(db, Task, task_id, "Task")
    if RANK[user.role] < RANK[Role.manager.value] and task.owner_id != user.id:
        raise HTTPException(403, "Reps can only delete their own tasks")
    snap = audit.snapshot(task)
    db.delete(task)
    audit.record(db, user, "delete", "task", task_id, snap, request)
    db.commit()
