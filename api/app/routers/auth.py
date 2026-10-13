from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import func, select

from app import audit
from app.config import get_settings
from app.deps import DB, CurrentUser
from app.models import User
from app.schemas import LoginIn, UserOut
from app.security import create_access_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, response: Response, request: Request, db: DB) -> User:
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if user is None or not user.is_active or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    s = get_settings()
    token = create_access_token(user.id, user.role)
    response.set_cookie(
        s.cookie_name,
        token,
        httponly=True,
        secure=s.cookie_secure,
        samesite="lax",
        max_age=s.access_token_minutes * 60,
        path="/",
    )
    audit.record(db, user, "login", "user", user.id, request=request)
    db.commit()
    return user


@router.post("/logout", status_code=204)
def logout(response: Response) -> None:
    response.delete_cookie(get_settings().cookie_name, path="/")


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user
