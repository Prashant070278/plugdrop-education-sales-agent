import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.db import SessionLocal
from app.routers import (
    activities,
    audit_log,
    auth,
    campaigns,
    consent,
    contacts,
    dashboard,
    engagement,
    institutions,
    leads,
    linkedin,
    meetings,
    opportunities,
    outreach,
    research,
    settings,
    tasks,
    users,
    whatsapp,
)

log = logging.getLogger("plugdrop")

app = FastAPI(title="PlugDrop Sales CRM API", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@app.exception_handler(IntegrityError)
async def integrity_error(_: Request, exc: IntegrityError) -> JSONResponse:
    log.warning("Integrity error: %s", exc.orig)
    return JSONResponse(
        status_code=409, content={"detail": "This change conflicts with existing data (duplicate or invalid reference)."}
    )


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [{"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": "Validation failed", "errors": errors})


@app.on_event("startup")
def _start_worker() -> None:
    if get_settings().research_worker == "embedded":
        from app.research.service import start_embedded_worker

        start_embedded_worker(SessionLocal)
    if get_settings().outreach_worker == "embedded":
        from app.outreach.service import start_embedded_worker as start_outreach_worker

        start_outreach_worker(SessionLocal)
    if get_settings().engagement_worker == "embedded":
        from app.engagement.worker import start_embedded_worker as start_engagement_worker

        start_engagement_worker(SessionLocal)


@app.get("/api/health", tags=["system"])
def health() -> dict[str, str]:
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    return {"status": "ok"}


for r in (
    auth,
    users,
    institutions,
    contacts,
    leads,
    opportunities,
    outreach,
    research,
    engagement,
    whatsapp,
    meetings,
    linkedin,
    activities,
    tasks,
    campaigns,
    consent,
    audit_log,
    dashboard,
    settings,
):
    app.include_router(r.router, prefix="/api")
