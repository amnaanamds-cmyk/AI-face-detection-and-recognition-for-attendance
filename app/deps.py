"""Authentication, role checks and template rendering helpers shared by routers."""
from __future__ import annotations

from pathlib import Path

from fastapi import Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import AttendanceStatus, ClassSession, Course, Enrollment, Role, Student, User

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["app_name"] = settings.app_name
templates.env.globals["support_email"] = settings.support_email
templates.env.filters["fromjson"] = __import__("json").loads
templates.env.globals["statuses"] = [s.value for s in AttendanceStatus]
templates.env.globals["signup_enabled"] = settings.public_signup
templates.env.globals["edition"] = settings.edition


class NotAuthenticated(Exception):
    pass


class Forbidden(Exception):
    pass


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("uid")
    user = db.get(User, uid) if uid else None
    if user is None or not user.is_active or user.org_id is None or not user.org.is_active:
        raise NotAuthenticated()
    db.info["actor"] = user.username  # recorded in the attendance ledger
    return user


def superadmin(user: User = Depends(current_user)) -> User:
    if not user.is_superadmin:
        raise Forbidden()
    return user


def require(*roles: Role):
    def checker(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise Forbidden()
        return user

    return checker


admin_only = require(Role.admin)
staff = require(Role.admin, Role.teacher)


def course_scope(db: Session, user: User) -> list[int]:
    """Course ids the user may see (always limited to the user's organization)."""
    if user.role == Role.admin:
        return list(db.scalars(select(Course.id).where(Course.org_id == user.org_id)).all())
    if user.role == Role.teacher:
        return list(db.scalars(select(Course.id).where(Course.teacher_id == user.id)).all())
    if user.student_id:
        return list(db.scalars(select(Enrollment.course_id).where(Enrollment.student_id == user.student_id)).all())
    return []


def can_manage_course(user: User, course: Course) -> bool:
    if course is None or course.org_id != user.org_id:
        return False
    return user.role == Role.admin or (user.role == Role.teacher and course.teacher_id == user.id)


def get_student(db: Session, user: User, sid: int) -> Student:
    """Load a student of the user's organization, or 404 (never reveal other organizations' data)."""
    st = db.get(Student, sid)
    if st is None or st.org_id != user.org_id:
        raise HTTPException(404, "Not found")
    return st


def get_course(db: Session, user: User, cid: int) -> Course:
    c = db.get(Course, cid)
    if c is None or c.org_id != user.org_id:
        raise HTTPException(404, "Not found")
    return c


def get_session(db: Session, user: User, sid: int) -> ClassSession:
    s = db.get(ClassSession, sid)
    if s is None or s.course.org_id != user.org_id:
        raise HTTPException(404, "Not found")
    return s


def flash(request: Request, message: str, category: str = "success") -> None:
    request.session.setdefault("flash", []).append([category, message])


def render(request: Request, name: str, user: User | None = None, status_code: int = 200, **ctx):
    from app.terminology import terms

    flashes = request.session.pop("flash", []) if "session" in request.scope else []
    org = user.org if user is not None else None
    ctx.setdefault("t", terms(org.kind if org else None))
    if org is not None:
        from app.services.billing import subscription_problem

        ctx.setdefault("billing_problem", subscription_problem(org))
    return templates.TemplateResponse(
        request, name, {"user": user, "org": org, "flashes": flashes, **ctx}, status_code=status_code
    )


def require_active_subscription(user: User) -> None:
    """Recognition and enrollment need an active plan; viewing and exporting data always keeps working."""
    from app.services.billing import subscription_problem

    problem = subscription_problem(user.org)
    if problem:
        raise HTTPException(402, problem)
