"""Business rules shared by routers: duplicate detection, suppression, lookups."""

from __future__ import annotations

from typing import Any, TypeVar

from fastapi import HTTPException, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import ConsentRecord, ConsentStatus, Contact, Institution, User
from app.normalize import domain_from_website, normalize_email, normalize_linkedin, normalize_name
from app.schemas import DuplicateMatch

M = TypeVar("M", bound=Base)

BLOCKING = (ConsentStatus.opted_out.value, ConsentStatus.suppressed.value)


def get_or_404(db: Session, model: type[M], obj_id: int, label: str | None = None) -> M:
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{label or model.__name__} {obj_id} not found")
    return obj


def ensure_exists(db: Session, model: type[Base], obj_id: int | None, label: str) -> None:
    if obj_id is not None and db.get(model, obj_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{label} {obj_id} does not exist")


def ensure_user(db: Session, user_id: int | None) -> None:
    if user_id is None:
        return
    u = db.get(User, user_id)
    if u is None or not u.is_active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"User {user_id} does not exist or is inactive")


# ---------- duplicates ----------
def institution_duplicates(
    db: Session, name: str, website: str | None, country: str, city: str, exclude_id: int | None = None
) -> list[DuplicateMatch]:
    """Exact: same website domain, or same normalised name in the same city and country.
    Possible: same normalised name elsewhere (shown to the user, never auto-merged)."""
    norm = normalize_name(name)
    domain = domain_from_website(website)
    conds: list[Any] = [Institution.normalized_name == norm]
    if domain:
        conds.append(Institution.domain == domain)
    stmt = select(Institution).where(or_(*conds))
    if exclude_id:
        stmt = stmt.where(Institution.id != exclude_id)
    out: list[DuplicateMatch] = []
    for inst in db.scalars(stmt.limit(20)):
        if domain and inst.domain == domain:
            out.append(DuplicateMatch(id=inst.id, label=inst.name, reason=f"Same website domain ({domain})", exact=True))
        elif inst.normalized_name == norm and inst.country == country and inst.city == city:
            out.append(DuplicateMatch(id=inst.id, label=inst.name, reason="Same name in the same city and country", exact=True))
        else:
            out.append(
                DuplicateMatch(
                    id=inst.id,
                    label=f"{inst.name} ({inst.city or '?'}, {inst.country or '?'})",
                    reason="Same name, different location",
                    exact=False,
                )
            )
    return out


def contact_duplicates(
    db: Session,
    institution_id: int | None,
    first_name: str,
    last_name: str,
    email: str | None,
    linkedin_url: str | None,
    exclude_id: int | None = None,
) -> list[DuplicateMatch]:
    """Exact: same email (case-insensitive) or same LinkedIn URL.
    Possible: same first+last name at the same institution. People with similar names are
    reported for review only and are never merged automatically."""
    out: list[DuplicateMatch] = []
    email_n = normalize_email(email)
    li = normalize_linkedin(linkedin_url)
    conds: list[Any] = []
    if email_n:
        conds.append(func.lower(Contact.email) == email_n)
    if li:
        conds.append(Contact.linkedin_url == li)
    if institution_id:
        conds.append(
            and_(
                Contact.institution_id == institution_id,
                func.lower(Contact.first_name) == first_name.strip().lower(),
                func.lower(Contact.last_name) == last_name.strip().lower(),
            )
        )
    if not conds:
        return out
    stmt = select(Contact).where(or_(*conds))
    if exclude_id:
        stmt = stmt.where(Contact.id != exclude_id)
    for c in db.scalars(stmt.limit(20)):
        if email_n and (c.email or "").lower() == email_n:
            out.append(DuplicateMatch(id=c.id, label=c.full_name, reason=f"Same email ({email_n})", exact=True))
        elif li and c.linkedin_url == li:
            out.append(DuplicateMatch(id=c.id, label=c.full_name, reason="Same LinkedIn profile", exact=True))
        else:
            out.append(
                DuplicateMatch(
                    id=c.id, label=c.full_name, reason="Same name at this institution — review, not merged", exact=False
                )
            )
    return out


def raise_if_exact(matches: list[DuplicateMatch], entity: str) -> None:
    exact = [m for m in matches if m.exact]
    if exact:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"message": f"Duplicate {entity}", "duplicates": [m.model_dump() for m in exact]},
        )


# ---------- suppression ----------
def suppressed_contact_ids(db: Session, contacts: list[Contact]) -> set[int]:
    """Contacts that must not be contacted: opted out / suppressed by contact, email or institution domain."""
    if not contacts:
        return set()
    ids = [c.id for c in contacts]
    emails = [c.email.lower() for c in contacts if c.email]
    inst_ids = {c.institution_id for c in contacts}
    domains = {d for (d,) in db.execute(select(Institution.domain).where(Institution.id.in_(inst_ids))) if d}
    email_domains = {e.split("@", 1)[1] for e in emails if "@" in e}
    conds: list[Any] = [ConsentRecord.contact_id.in_(ids)]
    if emails:
        conds.append(func.lower(ConsentRecord.email).in_(emails))
    all_domains = domains | email_domains
    if all_domains:
        conds.append(ConsentRecord.domain.in_(all_domains))
    records = db.scalars(select(ConsentRecord).where(ConsentRecord.status.in_(BLOCKING), or_(*conds))).all()
    blocked: set[int] = set()
    inst_domain = {i: d for i, d in db.execute(select(Institution.id, Institution.domain).where(Institution.id.in_(inst_ids)))}
    for c in contacts:
        e = (c.email or "").lower()
        ed = e.split("@", 1)[1] if "@" in e else None
        for r in records:
            if (
                r.contact_id == c.id
                or (r.email and e and r.email.lower() == e)
                or (r.domain and r.domain in {ed, inst_domain.get(c.institution_id)})
            ):
                blocked.add(c.id)
                break
    return blocked


def is_contact_suppressed(db: Session, contact: Contact) -> bool:
    return contact.id in suppressed_contact_ids(db, [contact])


def suppressed_institution_ids(db: Session, institution_ids: list[int]) -> set[int]:
    if not institution_ids:
        return set()
    rows = db.execute(
        select(Institution.id)
        .join(ConsentRecord, ConsentRecord.domain == Institution.domain)
        .where(Institution.id.in_(institution_ids), ConsentRecord.status.in_(BLOCKING))
    ).all()
    return {r[0] for r in rows}


def assert_contactable(db: Session, contact: Contact | None) -> None:
    if contact is not None and is_contact_suppressed(db, contact):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{contact.full_name} is opted out or suppressed and must not be contacted.",
        )


def apply_contact_normalization(data: dict[str, Any]) -> dict[str, Any]:
    if "email" in data:
        data["email"] = normalize_email(data["email"])
    if "linkedin_url" in data:
        data["linkedin_url"] = normalize_linkedin(str(data["linkedin_url"]) if data["linkedin_url"] else None)
    for k in ("first_name", "last_name"):
        if k in data and isinstance(data[k], str):
            data[k] = data[k].strip()
    return data


def apply_institution_normalization(data: dict[str, Any]) -> dict[str, Any]:
    if "website" in data:
        data["website"] = str(data["website"]) if data["website"] else None
        data["domain"] = domain_from_website(data["website"])
    if "name" in data and data["name"]:
        data["name"] = data["name"].strip()
        data["normalized_name"] = normalize_name(data["name"])
    for k in ("country", "city"):
        if k in data and isinstance(data[k], str):
            data[k] = data[k].strip()
    return data
