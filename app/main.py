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
from app.models import Role, User
from app.routers import admin, analytics, auth, courses, dashboard, reports, sessions, students
from app.security import hash_password

log = logging.getLogger("attendance")


def bootstrap_admin() -> None:
    """Create the first administrator account if no users exist yet."""
    with database.SessionLocal() as db:
        if db.scalar(select(User.id).limit(1)) is None:
            db.add(User(username=settings.admin_username, password_hash=hash_password(settings.admin_password),
                        full_name="Administrator", role=Role.admin))
            db.commit()
            log.warning("Created initial admin user '%s' - change the password after first login!",
                        settings.admin_username)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    database.init_db()
    bootstrap_admin()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax",
                       session_cookie="attendance_session", max_age=8 * 3600)
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

    for r in (auth, dashboard, students, courses, sessions, analytics, reports, admin):
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
