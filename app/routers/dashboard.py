from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import settings
from app.deps import NotAuthenticated, course_scope, current_user, render
from app.models import Attendance, Notification, Role, User
from app.services import analytics
from app.services.forecast import at_risk, student_forecasts
from app.services.billing import PLANS, TRIAL_DAYS
from app.services.privacy import apply_retention, export_person
from app.services.attendance import sync_scheduled_sessions

router = APIRouter()
_last_retention: dict[int, float] = {}


def _maybe_retention(db: Session, org_id: int) -> None:
    """Apply the retention rules at most every 6 hours per organization."""
    if time.monotonic() - _last_retention.get(org_id, -1e9) > 6 * 3600:
        _last_retention[org_id] = time.monotonic()
        apply_retention(db, org_id)


@router.get("/")
def home(request: Request, db: Session = Depends(get_db)):
    try:
        user = current_user(request, db)
    except NotAuthenticated:
        if settings.public_signup:  # SaaS: visitors see the product page, not a login box
            return render(request, "landing.html", None, plans=[p for k, p in PLANS.items() if k not in ("trial", "selfhosted")],
                          trial_days=TRIAL_DAYS)
        raise
    if user.role == Role.student:
        return RedirectResponse("/me", status_code=303)
    sync_scheduled_sessions(db, user.org_id)
    _maybe_retention(db, user.org_id)
    scope = course_scope(db, user)
    data = analytics.dashboard(db, scope, org_id=user.org_id if user.role == Role.admin else None)
    nq = select(Notification).where(Notification.is_read.is_(False))
    if scope is not None:
        nq = nq.where(Notification.course_id.in_(scope))
    alerts = db.scalars(nq.order_by(Notification.created_at.desc()).limit(5)).all()
    return render(request, "dashboard.html", user, d=data, alerts=alerts,
                  trend=analytics.daily_trend(db, 14, scope), risky=at_risk(db, user.org_id, scope, limit=6))


@router.get("/me")
def my_attendance(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.student_id is None:
        return RedirectResponse("/", status_code=303)
    summary = analytics.student_summary(db, user.student_id)
    history = db.scalars(
        select(Attendance).where(Attendance.student_id == user.student_id).order_by(Attendance.date.desc()).limit(50)
    ).all()
    notes = db.scalars(
        select(Notification).where(Notification.student_id == user.student_id, Notification.is_read.is_(False))
        .order_by(Notification.created_at.desc())
    ).all()
    return render(request, "me.html", user, s=summary, history=history, notes=notes, student=user.student,
                  forecasts=student_forecasts(db, user.student))


@router.get("/me/export")
def my_data(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """A person can download all data stored about them."""
    if user.student is None:
        return RedirectResponse("/", status_code=303)
    body = json.dumps(export_person(db, user.student), indent=2, ensure_ascii=False)
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="my_attendance_data.json"'})
