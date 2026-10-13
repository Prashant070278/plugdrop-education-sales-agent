from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import audit
from app.deps import DB, AdminUser, CurrentUser
from app.models import User
from app.schemas import UserCreate, UserOut, UserRef, UserUpdate
from app.security import hash_password
from app.services import get_or_404

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/options", response_model=list[UserRef])
def user_options(_: CurrentUser, db: DB) -> list[User]:
    """Active users, for owner pickers."""
    return list(db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.full_name)))


@router.get("", response_model=list[UserOut])
def list_users(_: AdminUser, db: DB) -> list[User]:
    return list(db.scalars(select(User).order_by(User.full_name)))


@router.post("", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, actor: AdminUser, db: DB, request: Request) -> User:
    if db.scalar(select(User.id).where(func.lower(User.email) == body.email.lower())):
        raise HTTPException(409, "A user with this email already exists")
    user = User(
        email=body.email.lower(), full_name=body.full_name, role=body.role.value, hashed_password=hash_password(body.password)
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(409, "A user with this email already exists") from e
    audit.record(db, actor, "create", "user", user.id, {"email": user.email, "role": user.role}, request)
    db.commit()
    return user


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, actor: AdminUser, db: DB, request: Request) -> User:
    user = get_or_404(db, User, user_id, "User")
    data = body.model_dump(exclude_unset=True)
    if user.id == actor.id and (data.get("is_active") is False or (data.get("role") and data["role"] != user.role)):
        raise HTTPException(422, "You cannot deactivate yourself or change your own role")
    before = audit.snapshot(user)
    if "password" in data:
        pw = data.pop("password")
        if pw:
            user.hashed_password = hash_password(pw)
    for k, v in data.items():
        setattr(user, k, v.value if hasattr(v, "value") else v)
    db.flush()
    changes = audit.diff(before, audit.snapshot(user))
    if body.password:
        changes["password"] = {"from": "***", "to": "***"}
    audit.record(db, actor, "update", "user", user.id, changes, request)
    db.commit()
    return user
