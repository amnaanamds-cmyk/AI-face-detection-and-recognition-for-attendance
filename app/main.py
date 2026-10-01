"""FastAPI application entry point.

Run:  uvicorn app.main:app --reload     then open http://127.0.0.1:8000
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from app import database
from app.config import settings
from app.deps import Forbidden, NotAuthenticated, render
from app.models import Organization, Role, User
from app.routers import (admin, analytics, auth, billing, courses, dashboard, imports, integrity, kiosk, legal, messages,
                         mobile,
                         platform, reports, sessions, students)
from app.security import hash_password
from app.tenancy import create_org, upgrade_database

log = logging.getLogger("attendance")


def bootstrap_admin() -> None:
    """Create the first organization and its administrator if no users exist yet.

    This first account is also the platform operator (superadmin) of the installation.
    """
    with database.SessionLocal() as db:
        if db.scalar(select(User.id).limit(1)) is None:
            org = db.scalar(select(Organization).order_by(Organization.id).limit(1)) or create_org(
                db, settings.default_org_name, plan="selfhosted" if settings.edition == "selfhosted" else "trial")
            db.add(User(username=settings.admin_username, password_hash=hash_password(settings.admin_password),
                        full_name="Administrator", role=Role.admin, org_id=org.id, is_superadmin=True))
            db.commit()
            log.warning("Created initial admin user '%s' - change the password after first login!",
                        settings.admin_username)


def check_production_settings() -> None:
    """The hosted edition is on the internet: refuse to start with the demo defaults."""
    if settings.edition != "saas":
        return
    problems = []
    if settings.admin_password == "admin123":
        problems.append("ADMIN_PASSWORD is the default 'admin123'")
    if not settings.public_base_url.startswith("https://"):
        problems.append("PUBLIC_BASE_URL must be your https:// address")
    if "[" in settings.legal_company or "[" in settings.legal_email:
        log.warning("LEGAL_COMPANY_NAME / LEGAL_CONTACT_EMAIL are not set - the legal pages show placeholders")
    if problems:
        raise RuntimeError("EDITION=saas: " + "; ".join(problems))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    check_production_settings()
    database.init_db()
    upgrade_database(database.engine)
    bootstrap_admin()
    from app.services import ledger
    with database.SessionLocal() as db:
        for org_id in db.scalars(select(Organization.id)).all():
            ledger.ensure_baseline(db, org_id)  # first start with the ledger: existing records = starting point
    from app.services import tunnel
    if settings.edition != "saas":
        tunnel.resume_if_remembered()  # "share online" was on before the restart
    yield
    tunnel.tunnel.stop_process()  # server quitting: end the tunnel too (it restarts next time if it was on)


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax",
                       session_cookie="attendance_session", max_age=8 * 3600,
                       https_only=settings.public_base_url.startswith("https://"))
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

    for r in (mobile, legal, auth, dashboard, imports, students, courses, sessions, kiosk, analytics, reports, admin, messages, integrity, platform, billing):
        app.include_router(r.router)

    @app.exception_handler(NotAuthenticated)
    async def _unauth(request: Request, _exc):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        return RedirectResponse(f"/login?next={request.url.path}", status_code=303)

    @app.exception_handler(Forbidden)
    async def _forbidden(request: Request, _exc):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Forbidden"}, status_code=403)
        return render(request, "error.html", None, status_code=403,
                      message="You do not have permission to open this page.")

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
