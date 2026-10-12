from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Role, User
from app.security import decode_access_token

DB = Annotated[Session, Depends(get_db)]


def _token_from_request(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(get_settings().cookie_name)


def get_current_user(request: Request, db: DB) -> User:
    token = _token_from_request(request)
    user_id = decode_access_token(token) if token else None
    user = db.get(User, user_id) if user_id else None
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]

RANK = {Role.rep.value: 1, Role.manager.value: 2, Role.admin.value: 3}


def require_role(minimum: Role) -> Callable[[User], User]:
    def checker(user: CurrentUser) -> User:
        if RANK.get(user.role, 0) < RANK[minimum.value]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {minimum.value} role or higher")
        return user

    return checker


ManagerUser = Annotated[User, Depends(require_role(Role.manager))]
AdminUser = Annotated[User, Depends(require_role(Role.admin))]
