"""Authentication, role checks and template rendering helpers shared by routers."""
from __future__ import annotations

from pathlib import Path

from fastapi import Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import AttendanceStatus, Course, Enrollment, Role, User

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["app_name"] = settings.app_name
templates.env.globals["statuses"] = [s.value for s in AttendanceStatus]


class NotAuthenticated(Exception):
    pass


class Forbidden(Exception):
    pass


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("uid")
    user = db.get(User, uid) if uid else None
    if user is None or not user.is_active:
        raise NotAuthenticated()
    return user


def require(*roles: Role):
    def checker(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise Forbidden()
        return user

    return checker


admin_only = require(Role.admin)
staff = require(Role.admin, Role.teacher)


def course_scope(db: Session, user: User) -> list[int] | None:
    """Course ids the user may see; None = everything (admin)."""
    if user.role == Role.admin:
        return None
    if user.role == Role.teacher:
        return list(db.scalars(select(Course.id).where(Course.teacher_id == user.id)).all())
    if user.student_id:
        return list(db.scalars(select(Enrollment.course_id).where(Enrollment.student_id == user.student_id)).all())
    return []


def can_manage_course(user: User, course: Course) -> bool:
    return user.role == Role.admin or (user.role == Role.teacher and course.teacher_id == user.id)


def flash(request: Request, message: str, category: str = "success") -> None:
    request.session.setdefault("flash", []).append([category, message])


def render(request: Request, name: str, user: User | None = None, status_code: int = 200, **ctx):
    flashes = request.session.pop("flash", []) if "session" in request.scope else []
    return templates.TemplateResponse(
        request, name, {"user": user, "flashes": flashes, **ctx}, status_code=status_code
    )
