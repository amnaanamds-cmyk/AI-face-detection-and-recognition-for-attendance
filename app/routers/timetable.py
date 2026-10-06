"""Weekly timetable (periods), one-tap Start of a period, and the holiday calendar."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import admin_only, can_manage_course, course_scope, flash, render, require_active_subscription, staff
from app.models import Course, Holiday, TimetableSlot, User, now
from app.services import importer
from app.services import timetable as tt

router = APIRouter()


@router.get("/timetable")
def timetable_page(request: Request, cls: str = "", user: User = Depends(staff), db: Session = Depends(get_db)):
    scope = course_scope(db, user)
    all_slots = tt.slots(db, user.org_id, scope)
    classes = sorted({f"{s.course.semester}-{s.course.section}" for s in all_slots})
    if cls:
        all_slots = [s for s in all_slots if f"{s.course.semester}-{s.course.section}" == cls]
    by_day = {i: [s for s in all_slots if s.weekday == i] for i in range(7)}
    courses = db.scalars(select(Course).where(Course.org_id == user.org_id, Course.code != "GENERAL")
                         .order_by(Course.semester, Course.section, Course.code)).all()
    today = now().date()
    return render(request, "timetable.html", user, by_day=by_day, days=tt.DAYS, courses=courses, classes=classes,
                  cls=cls, holiday=tt.holiday_on(db, user.org_id, today),
                  today_periods=tt.periods(db, user.org_id, today, today, scope))


@router.post("/timetable/new")
async def add_slots(request: Request, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    form = await request.form()
    course = db.get(Course, int(form.get("course_id") or 0))
    if course is None or course.org_id != user.org_id:
        raise HTTPException(404)
    days = [int(d) for d in form.getlist("days")]
    return _add(request, db, user, course, days, str(form.get("start", "")), int(form.get("minutes") or 40),
                str(form.get("room", "")))


def _add(request, db, user, course, days, start, minutes, room):
    try:
        start = tt.parse_time(start)
        if not days:
            raise ValueError("choose at least one day")
        if not 5 <= minutes <= 600:
            raise ValueError("minutes must be between 5 and 600")
    except ValueError as exc:
        flash(request, f"Not added: {exc}", "danger")
        return RedirectResponse("/timetable", status_code=303)
    for d in days:
        db.add(TimetableSlot(org_id=user.org_id, course_id=course.id, weekday=d, start=start, minutes=minutes, room=room[:40]))
    db.commit()
    flash(request, f"{course.code}: {len(days)} period(s) added at {start}")
    return RedirectResponse(f"/timetable?cls={course.semester}-{course.section}", status_code=303)


@router.post("/timetable/{slot_id}/delete")
def delete_slot(slot_id: int, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    slot = db.get(TimetableSlot, slot_id)
    if slot is None or slot.org_id != user.org_id:
        raise HTTPException(404)
    db.delete(slot)
    db.commit()
    return RedirectResponse("/timetable", status_code=303)


@router.get("/timetable/template.csv")
def template(user: User = Depends(admin_only)):
    return Response(tt.TEMPLATE_CSV.encode("utf-8-sig"), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="timetable_template.csv"'})


@router.post("/timetable/import")
async def import_timetable(request: Request, file: UploadFile = File(...), replace: str = Form(""),
                           user: User = Depends(admin_only), db: Session = Depends(get_db)):
    try:
        n, errors = tt.import_rows(db, user.org_id, importer.read_table(file.filename or "", await file.read()),
                                   replace=replace == "on")
    except Exception as exc:
        db.rollback()
        n, errors = 0, [f"could not read file: {exc}"]
    flash(request, f"{n} period(s) imported." + (" Problems: " + "; ".join(errors[:8]) if errors else ""),
          "warning" if errors else "success")
    return RedirectResponse("/timetable", status_code=303)


@router.post("/timetable/{slot_id}/start")
def start_period(slot_id: int, request: Request, user: User = Depends(staff), db: Session = Depends(get_db)):
    """One tap from "My periods today": opens (or reopens) today's session of this period."""
    slot = db.get(TimetableSlot, slot_id)
    if slot is None or slot.org_id != user.org_id or not can_manage_course(user, slot.course):
        raise HTTPException(404)
    require_active_subscription(user)
    today = now().date()
    if tt.holiday_on(db, user.org_id, today):
        flash(request, "Today is a holiday in the school calendar.", "warning")
    p = next((p for p in tt.periods(db, user.org_id, today, today, [slot.course_id]) if p.slot.id == slot.id), None)
    if p is not None and p.session is not None:
        return RedirectResponse(f"/sessions/{p.session.id}" + ("/live" if p.state != "done" else ""), status_code=303)
    s = tt.start_period(db, slot, user.id, today)
    return RedirectResponse(f"/sessions/{s.id}/live", status_code=303)


# ------------------------------------------------------------------ holidays
@router.get("/calendar")
def calendar_page(request: Request, user: User = Depends(staff), db: Session = Depends(get_db)):
    hs = db.scalars(select(Holiday).where(Holiday.org_id == user.org_id).order_by(Holiday.start.desc())).all()
    return render(request, "calendar.html", user, holidays=hs, today=now().date())


@router.post("/calendar/add")
def add_holiday(request: Request, start: str = Form(...), end: str = Form(""), name: str = Form(...),
                user: User = Depends(admin_only), db: Session = Depends(get_db)):
    try:
        s = date.fromisoformat(start)
        e = date.fromisoformat(end) if end else s
    except ValueError:
        flash(request, "Choose valid dates", "danger")
        return RedirectResponse("/calendar", status_code=303)
    if e < s:
        s, e = e, s
    db.add(Holiday(org_id=user.org_id, start=s, end=e, name=name.strip()[:120] or "Holiday"))
    db.commit()
    flash(request, f"Holiday added: {name} ({s:%d %b}" + (f" - {e:%d %b %Y})" if e != s else f" {s:%Y})"))
    return RedirectResponse("/calendar", status_code=303)


@router.post("/calendar/{hid}/delete")
def delete_holiday(hid: int, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    h = db.get(Holiday, hid)
    if h is None or h.org_id != user.org_id:
        raise HTTPException(404)
    db.delete(h)
    db.commit()
    return RedirectResponse("/calendar", status_code=303)
