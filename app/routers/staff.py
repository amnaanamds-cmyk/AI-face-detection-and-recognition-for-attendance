"""Staff attendance page: teachers check in by face at the kiosk."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import admin_only, flash, render
from app.models import Role, User, now
from app.services import staff as staff_service
from app.services.timetable import DAYS

router = APIRouter()


@router.get("/staff")
def staff_page(request: Request, month: str = "", user: User = Depends(admin_only), db: Session = Depends(get_db)):
    today = now().date()
    try:
        y, m = (int(x) for x in (month or today.strftime("%Y-%m")).split("-"))
        first = date(y, m, 1)
    except ValueError:
        first = today.replace(day=1)
    import calendar

    last = min(today, first.replace(day=calendar.monthrange(first.year, first.month)[1]))
    course = staff_service.staff_course(db, user.org_id)
    rows = sorted(staff_service.summary(db, user.org_id, first, last, today).values(),
                  key=lambda r: (r["user"].role != Role.teacher, r["user"].full_name))
    return render(request, "staff.html", user, rows=rows, course=course, days=DAYS, month=first.strftime("%Y-%m"),
                  first=first, today=today, active_days=[int(d) for d in (course.schedule_days or "").split(",") if d])


@router.post("/staff/schedule")
async def schedule(request: Request, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    form = await request.form()
    try:
        c = staff_service.set_schedule(db, user.org_id, str(form.get("start", "")), [int(d) for d in form.getlist("days")],
                                       int(form.get("minutes") or 420))
        flash(request, f"Check-in time {c.schedule_start}; staff not checked in after {c.schedule_minutes // 60} h "
                       f"{c.schedule_minutes % 60} min are marked absent.")
    except ValueError as exc:
        flash(request, f"Not saved: {exc}", "danger")
    return RedirectResponse("/staff", status_code=303)


@router.post("/staff/{uid}/face")
def register_face(uid: int, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    """Create the teacher's staff record (once) and open the face registration page."""
    target = db.get(User, uid)
    if target is None or target.org_id != user.org_id or target.role == Role.student:
        raise HTTPException(404)
    person = staff_service.ensure_person(db, target)
    return RedirectResponse(f"/students/{person.id}/enroll", status_code=303)
