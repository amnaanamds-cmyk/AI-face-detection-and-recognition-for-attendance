from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Body, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.terminology import terms
from app.deps import require_active_subscription, can_manage_course, course_scope, flash, render, staff
from app.models import AttendanceStatus, ClassSession, Course, Enrollment, SessionState, Student, User
from app.services import app_settings
from app.services import attendance as att
from app.vision.backends import ModelsMissingError
from app.vision.base import decode_image

router = APIRouter()


def _t(user):
    return terms(user.org.kind)


def _get(db: Session, sid: int, user: User) -> ClassSession:
    s = db.get(ClassSession, sid)
    if s is None:
        raise HTTPException(404, "Session not found")
    if not can_manage_course(user, s.course):
        raise HTTPException(403, "Not your course")
    return s


@router.get("/sessions")
def list_sessions(request: Request, user: User = Depends(staff), db: Session = Depends(get_db)):
    q = select(ClassSession).order_by(ClassSession.start_time.desc())
    scope = course_scope(db, user)
    if scope is not None:
        q = q.where(ClassSession.course_id.in_(scope))
    return render(request, "sessions/list.html", user, sessions=db.scalars(q.limit(200)).all())


@router.get("/sessions/new")
def new_session(request: Request, course_id: int = 0, user: User = Depends(staff), db: Session = Depends(get_db)):
    q = select(Course).order_by(Course.code)
    scope = course_scope(db, user)
    if scope is not None:
        q = q.where(Course.id.in_(scope))
    cfg = app_settings.all_settings(db)
    return render(request, "sessions/form.html", user, courses=db.scalars(q).all(), course_id=course_id,
                  today=date.today().isoformat(), now=datetime.now().strftime("%H:%M"), cfg=cfg)


@router.post("/sessions/new")
def create_session(request: Request, course_id: int = Form(...), day: str = Form(...), start: str = Form(...),
                   duration: int = Form(60), present_window: int = Form(10), late_window: int = Form(20),
                   liveness: str = Form(""), start_now: str = Form(""),
                   user: User = Depends(staff), db: Session = Depends(get_db)):
    course = db.get(Course, course_id)
    if course is None or not can_manage_course(user, course):
        raise HTTPException(403, "Not your course")
    if late_window < present_window:
        flash(request, "Late window must be greater than or equal to the present window", "danger")
        return RedirectResponse(f"/sessions/new?course_id={course_id}", status_code=303)
    d = date.fromisoformat(day)
    s = ClassSession(
        course_id=course_id, date=d, start_time=datetime.combine(d, datetime.strptime(start, "%H:%M").time()),
        duration_minutes=duration, present_window_minutes=present_window, late_window_minutes=late_window,
        liveness_required=liveness == "on", created_by=user.id,
    )
    db.add(s)
    db.commit()
    if start_now:
        att.start_session(db, s)
        return RedirectResponse(f"/sessions/{s.id}/live", status_code=303)
    flash(request, f"{_t(user).session} created")
    return RedirectResponse(f"/sessions/{s.id}", status_code=303)


@router.get("/sessions/{sid}")
def session_detail(sid: int, request: Request, user: User = Depends(staff), db: Session = Depends(get_db)):
    s = _get(db, sid, user)
    students = db.scalars(select(Student).join(Enrollment).where(Enrollment.course_id == s.course_id)
                          .order_by(Student.student_code)).all()
    recs = {r.student_id: r for r in s.attendance}
    return render(request, "sessions/detail.html", user, session=s, students=students, recs=recs,
                  summary=att.session_summary(db, s))


@router.post("/sessions/{sid}/start")
def start(sid: int, user: User = Depends(staff), db: Session = Depends(get_db)):
    s = _get(db, sid, user)
    if s.state == SessionState.closed:
        raise HTTPException(400, "Session already closed")
    att.start_session(db, s)
    return RedirectResponse(f"/sessions/{sid}/live", status_code=303)


@router.post("/sessions/{sid}/close")
def close(sid: int, request: Request, user: User = Depends(staff), db: Session = Depends(get_db)):
    s = _get(db, sid, user)
    n = att.close_session(db, s)
    flash(request, f"{_t(user).session} closed. {n} {_t(user).people.lower()} automatically marked absent.")
    return RedirectResponse(f"/sessions/{sid}", status_code=303)


@router.post("/sessions/{sid}/delete")
def delete(sid: int, request: Request, user: User = Depends(staff), db: Session = Depends(get_db)):
    s = _get(db, sid, user)
    att.live_trackers.pop(sid)
    db.delete(s)
    db.commit()
    flash(request, f"{_t(user).session} deleted")
    return RedirectResponse("/sessions", status_code=303)


@router.post("/sessions/{sid}/override")
def override(sid: int, request: Request, student_id: int = Form(...), status: str = Form(...), note: str = Form(""),
             user: User = Depends(staff), db: Session = Depends(get_db)):
    s = _get(db, sid, user)
    if student_id not in att.enrolled_student_ids(db, s.course_id):
        raise HTTPException(400, "Student not enrolled")
    att.set_status(db, s, student_id, AttendanceStatus(status), note or None)
    flash(request, "Attendance updated")
    return RedirectResponse(f"/sessions/{sid}", status_code=303)


@router.get("/sessions/{sid}/live")
def live(sid: int, request: Request, user: User = Depends(staff), db: Session = Depends(get_db)):
    s = _get(db, sid, user)
    if s.state == SessionState.scheduled:
        att.start_session(db, s)
    return render(request, "sessions/live.html", user, session=s)


@router.post("/api/sessions/{sid}/frame")
def api_frame(sid: int, payload: dict = Body(...), user: User = Depends(staff), db: Session = Depends(get_db)):
    """Body: {"image": "data:image/jpeg;base64,..."} - one camera frame from the browser."""
    s = _get(db, sid, user)
    require_active_subscription(user)
    try:
        frame = decode_image(payload.get("image", ""))
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, "Invalid image") from exc
    try:
        return att.process_frame(db, s, frame)
    except ModelsMissingError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/api/sessions/{sid}/summary")
def api_summary(sid: int, user: User = Depends(staff), db: Session = Depends(get_db)):
    return att.session_summary(db, _get(db, sid, user))
