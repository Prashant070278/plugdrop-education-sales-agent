"""Shared pagination, sorting and search for list endpoints."""

from typing import Any, Generic, TypeVar

from fastapi import HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class ListParams:
    def __init__(
        self,
        q: str | None = Query(None, max_length=200, description="Free-text search"),
        sort: str | None = Query(None, max_length=50, description="Field name, prefix with - for descending"),
        page: int = Query(1, ge=1),
        page_size: int = Query(25, ge=1, le=200),
    ) -> None:
        self.q = q.strip() if q else None
        self.sort = sort
        self.page = page
        self.page_size = page_size


def apply_search(stmt: Select[Any], q: str | None, columns: list[Any]) -> Select[Any]:
    if not q:
        return stmt
    like = f"%{q.replace('%', r'\%').replace('_', r'\_')}%"
    return stmt.where(or_(*[c.ilike(like) for c in columns]))


def apply_sort(stmt: Select[Any], sort: str | None, allowed: dict[str, Any], default: ColumnElement[Any]) -> Select[Any]:
    if not sort:
        return stmt.order_by(default)
    desc = sort.startswith("-")
    key = sort.lstrip("-")
    if key not in allowed:
        raise HTTPException(422, f"Cannot sort by '{key}'. Allowed: {', '.join(sorted(allowed))}")
    col = allowed[key]
    return stmt.order_by(col.desc().nullslast() if desc else col.asc().nullslast(), default)


def paginate(db: Session, stmt: Select[Any], params: ListParams) -> tuple[list[Any], int]:
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = db.scalars(stmt.offset((params.page - 1) * params.page_size).limit(params.page_size)).all()
    return list(rows), int(total)


def pairs(rows: Any) -> dict[Any, Any]:
    """Turn a two-column result into a dict (typed loosely for GROUP BY queries)."""
    return {r[0]: r[1] for r in rows}


def csv_safe(values: list[Any]) -> list[Any]:
    """Neutralise spreadsheet formula injection in exported cells."""
    return ["'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r") else v for v in values]
