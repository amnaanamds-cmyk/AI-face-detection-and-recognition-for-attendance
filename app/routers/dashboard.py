from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import course_scope, current_user, render
from app.models import Attendance, Notification, Role, User
from app.services import analytics

router = APIRouter()


@router.get("/")
def home(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.role == Role.student:
        return RedirectResponse("/me", status_code=303)
    scope = course_scope(db, user)
    data = analytics.dashboard(db, scope)
    nq = select(Notification).where(Notification.is_read.is_(False))
    if scope is not None:
        nq = nq.where(Notification.course_id.in_(scope))
    alerts = db.scalars(nq.order_by(Notification.created_at.desc()).limit(5)).all()
    return render(request, "dashboard.html", user, d=data, alerts=alerts,
                  trend=analytics.daily_trend(db, 14, scope))


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
    return render(request, "me.html", user, s=summary, history=history, notes=notes, student=user.student)
