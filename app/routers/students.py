from __future__ import annotations

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import admin_only, course_scope, flash, render, staff
from app.models import Attendance, Course, Enrollment, Role, Student, User
from app.security import hash_password
from app.services import analytics, faces
from app.vision.backends import ModelsMissingError
from app.vision.base import decode_image

router = APIRouter()

FIELDS = ("student_code", "roll_number", "name", "department", "semester", "section", "email", "phone")


def _get_student(db: Session, sid: int) -> Student:
    st = db.get(Student, sid)
    if st is None:
        raise HTTPException(404, "Student not found")
    return st


def auto_enroll(db: Session, student: Student) -> int:
    """Enroll the student in every course of their department/semester/section."""
    courses = db.scalars(select(Course).where(
        Course.department == student.department, Course.semester == student.semester, Course.section == student.section
    )).all()
    have = {e.course_id for e in student.enrollments}
    n = 0
    for c in courses:
        if c.id not in have:
            db.add(Enrollment(student_id=student.id, course_id=c.id))
            n += 1
    db.commit()
    return n


@router.get("/students")
def list_students(request: Request, q: str = "", semester: str = "", section: str = "",
                  user: User = Depends(staff), db: Session = Depends(get_db)):
    query = select(Student)
    scope = course_scope(db, user)
    if scope is not None:
        query = query.join(Enrollment).where(Enrollment.course_id.in_(scope)).distinct()
    if q:
        like = f"%{q}%"
        query = query.where(or_(Student.name.ilike(like), Student.student_code.ilike(like), Student.roll_number.ilike(like)))
    if semester:
        query = query.where(Student.semester == int(semester))
    if section:
        query = query.where(Student.section == section)
    students = db.scalars(query.order_by(Student.student_code)).all()
    return render(request, "students/list.html", user, students=students, q=q, semester=semester, section=section)


@router.get("/students/new")
def new_student(request: Request, user: User = Depends(admin_only)):
    return render(request, "students/form.html", user, student=None)


@router.post("/students/new")
async def create_student(request: Request, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    form = await request.form()
    data = {k: (form.get(k) or "").strip() for k in FIELDS}
    if not data["student_code"] or not data["name"]:
        flash(request, "Student ID and name are required", "danger")
        return RedirectResponse("/students/new", status_code=303)
    st = Student(**{**data, "semester": int(data["semester"] or 1), "section": data["section"] or "A",
                    "department": data["department"] or "Computer Science"},
                 consent_given=form.get("consent") == "on")
    db.add(st)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        flash(request, f"Student ID {data['student_code']} already exists", "danger")
        return RedirectResponse("/students/new", status_code=303)
    if form.get("auto_enroll") == "on":
        auto_enroll(db, st)
    if form.get("create_account") == "on" and form.get("password"):
        db.add(User(username=st.student_code, password_hash=hash_password(str(form.get("password"))),
                    full_name=st.name, email=st.email, role=Role.student, student_id=st.id))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            flash(request, "Login account not created: username already taken", "warning")
    flash(request, f"Student {st.name} registered. Next: capture face images.")
    return RedirectResponse(f"/students/{st.id}/enroll", status_code=303)


@router.get("/students/{sid}")
def student_detail(sid: int, request: Request, user: User = Depends(staff), db: Session = Depends(get_db)):
    st = _get_student(db, sid)
    scope = course_scope(db, user)
    if scope is not None and not ({e.course_id for e in st.enrollments} & set(scope)):
        return render(request, "error.html", user, status_code=403, message="Student is not in your courses.")
    summary = analytics.student_summary(db, sid, scope)
    hq = select(Attendance).where(Attendance.student_id == sid)
    if scope is not None:
        hq = hq.where(Attendance.course_id.in_(scope))
    history = db.scalars(hq.order_by(Attendance.date.desc()).limit(50)).all()
    all_courses = db.scalars(select(Course).order_by(Course.code)).all() if user.role == Role.admin else []
    return render(request, "students/detail.html", user, student=st, s=summary, history=history,
                  templates_count=faces.count_templates(db, sid), all_courses=all_courses)


@router.get("/students/{sid}/edit")
def edit_student(sid: int, request: Request, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    return render(request, "students/form.html", user, student=_get_student(db, sid))


@router.post("/students/{sid}/edit")
async def update_student(sid: int, request: Request, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    st = _get_student(db, sid)
    form = await request.form()
    for k in FIELDS:
        if k not in form:
            continue
        value = str(form[k]).strip()
        if k == "semester":
            st.semester = int(value or 1)
        elif k in ("email", "phone", "roll_number"):
            setattr(st, k, value or None)
        elif value:
            setattr(st, k, value)
    st.consent_given = form.get("consent") == "on"
    st.is_active = form.get("is_active") == "on"
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        flash(request, "Student ID already in use", "danger")
        return RedirectResponse(f"/students/{sid}/edit", status_code=303)
    faces.gallery_cache.invalidate()
    flash(request, "Student updated")
    return RedirectResponse(f"/students/{sid}", status_code=303)


@router.post("/students/{sid}/delete")
def delete_student(sid: int, request: Request, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    st = _get_student(db, sid)
    db.delete(st)
    db.commit()
    faces.gallery_cache.invalidate()
    flash(request, f"Student {st.name} and all their biometric data were deleted")
    return RedirectResponse("/students", status_code=303)


@router.post("/students/{sid}/courses")
def set_courses(sid: int, request: Request, course_id: int = Form(...), action: str = Form("add"),
                user: User = Depends(admin_only), db: Session = Depends(get_db)):
    st = _get_student(db, sid)
    existing = db.scalar(select(Enrollment).where(Enrollment.student_id == sid, Enrollment.course_id == course_id))
    if action == "add" and not existing:
        db.add(Enrollment(student_id=st.id, course_id=course_id))
    elif action == "remove" and existing:
        db.delete(existing)
    db.commit()
    return RedirectResponse(f"/students/{sid}", status_code=303)


# ------------------------------------------------------------------ face enrollment
@router.get("/students/{sid}/enroll")
def enroll_page(sid: int, request: Request, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    st = _get_student(db, sid)
    return render(request, "students/enroll.html", user, student=st, templates_count=faces.count_templates(db, sid),
                  min_images=settings.min_enrollment_images)


def _enroll(db: Session, st: Student, images) -> dict:
    if not st.consent_given:
        raise HTTPException(400, "Biometric consent has not been recorded for this student")
    if not images:
        raise HTTPException(400, "No images received")
    try:
        decoded = [decode_image(i) for i in images]
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        rep = faces.enroll_images(db, st, decoded)
    except ModelsMissingError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"accepted": rep.accepted, "rejected": rep.rejected, "total_templates": rep.total_templates}


@router.post("/api/students/{sid}/faces")
def api_enroll(sid: int, payload: dict = Body(...), user: User = Depends(admin_only), db: Session = Depends(get_db)):
    """JSON body: {"images": ["data:image/jpeg;base64,...", ...]} (captured by the browser camera)."""
    return _enroll(db, _get_student(db, sid), payload.get("images") or [])


@router.post("/students/{sid}/faces/upload")
async def upload_faces(sid: int, request: Request, files: list[UploadFile] = File(...),
                       user: User = Depends(admin_only), db: Session = Depends(get_db)):
    st = _get_student(db, sid)
    try:
        res = _enroll(db, st, [await f.read() for f in files])
        msg = f"{res['accepted']} face template(s) added."
        if res["rejected"]:
            msg += " Skipped: " + "; ".join(res["rejected"])
        flash(request, msg, "success" if res["accepted"] else "warning")
    except HTTPException as exc:
        flash(request, str(exc.detail), "danger")
    return RedirectResponse(f"/students/{sid}/enroll", status_code=303)


@router.post("/students/{sid}/faces/delete")
def delete_faces(sid: int, request: Request, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    n = faces.delete_faces(db, _get_student(db, sid).id)
    flash(request, f"Deleted {n} face template(s)")
    return RedirectResponse(f"/students/{sid}", status_code=303)
