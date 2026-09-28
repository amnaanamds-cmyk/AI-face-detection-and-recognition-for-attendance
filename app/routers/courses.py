from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import admin_only, can_manage_course, course_scope, flash, render, staff
from app.models import ClassSession, Course, Enrollment, Role, Student, User
from app.services import analytics

router = APIRouter()


def _teachers(db: Session):
    return db.scalars(select(User).where(User.role == Role.teacher, User.is_active.is_(True)).order_by(User.full_name)).all()


def _get(db: Session, cid: int) -> Course:
    c = db.get(Course, cid)
    if c is None:
        raise HTTPException(404, "Course not found")
    return c


@router.get("/courses")
def list_courses(request: Request, user: User = Depends(staff), db: Session = Depends(get_db)):
    q = select(Course).order_by(Course.code)
    scope = course_scope(db, user)
    if scope is not None:
        q = q.where(Course.id.in_(scope))
    return render(request, "courses/list.html", user, courses=db.scalars(q).all())


@router.get("/courses/new")
def new_course(request: Request, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    return render(request, "courses/form.html", user, course=None, teachers=_teachers(db))


def _apply(course: Course, code, name, department, semester, section, teacher_id):
    course.code, course.name = code.strip(), name.strip()
    course.department, course.semester, course.section = department.strip(), semester, section.strip()
    course.teacher_id = teacher_id or None


@router.post("/courses/new")
def create_course(request: Request, code: str = Form(...), name: str = Form(...), department: str = Form("Computer Science"),
                  semester: int = Form(1), section: str = Form("A"), teacher_id: int = Form(0),
                  enroll_matching: str = Form(""), user: User = Depends(admin_only), db: Session = Depends(get_db)):
    c = Course()
    _apply(c, code, name, department, semester, section, teacher_id)
    db.add(c)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        flash(request, f"Course code {code} already exists", "danger")
        return RedirectResponse("/courses/new", status_code=303)
    if enroll_matching:
        n = _enroll_matching(db, c)
        flash(request, f"Enrolled {n} student(s) of semester {c.semester}{c.section}")
    flash(request, f"Course {c.code} created")
    return RedirectResponse(f"/courses/{c.id}", status_code=303)


def _enroll_matching(db: Session, c: Course) -> int:
    have = set(db.scalars(select(Enrollment.student_id).where(Enrollment.course_id == c.id)).all())
    students = db.scalars(select(Student).where(Student.department == c.department, Student.semester == c.semester,
                                                Student.section == c.section, Student.is_active.is_(True))).all()
    n = 0
    for s in students:
        if s.id not in have:
            db.add(Enrollment(student_id=s.id, course_id=c.id))
            n += 1
    db.commit()
    return n


@router.get("/courses/{cid}")
def course_detail(cid: int, request: Request, user: User = Depends(staff), db: Session = Depends(get_db)):
    c = _get(db, cid)
    if not can_manage_course(user, c):
        return render(request, "error.html", user, status_code=403, message="This is not your course.")
    summary = analytics.course_summary(db, cid)
    sessions = db.scalars(select(ClassSession).where(ClassSession.course_id == cid).order_by(ClassSession.start_time.desc())).all()
    enrolled = {r["student"].id for r in summary["rows"]}
    others = db.scalars(select(Student).where(Student.is_active.is_(True)).order_by(Student.student_code)).all()
    return render(request, "courses/detail.html", user, course=c, summary=summary, sessions=sessions,
                  candidates=[s for s in others if s.id not in enrolled])


@router.get("/courses/{cid}/edit")
def edit_course(cid: int, request: Request, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    return render(request, "courses/form.html", user, course=_get(db, cid), teachers=_teachers(db))


@router.post("/courses/{cid}/edit")
def update_course(cid: int, request: Request, code: str = Form(...), name: str = Form(...),
                  department: str = Form("Computer Science"), semester: int = Form(1), section: str = Form("A"),
                  teacher_id: int = Form(0), user: User = Depends(admin_only), db: Session = Depends(get_db)):
    c = _get(db, cid)
    _apply(c, code, name, department, semester, section, teacher_id)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        flash(request, "Course code already in use", "danger")
    return RedirectResponse(f"/courses/{cid}", status_code=303)


@router.post("/courses/{cid}/delete")
def delete_course(cid: int, request: Request, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    c = _get(db, cid)
    db.delete(c)
    db.commit()
    flash(request, f"Course {c.code} deleted")
    return RedirectResponse("/courses", status_code=303)


@router.post("/courses/{cid}/enroll")
def enroll(cid: int, request: Request, student_id: int = Form(0), action: str = Form("add"),
           user: User = Depends(admin_only), db: Session = Depends(get_db)):
    c = _get(db, cid)
    if action == "matching":
        flash(request, f"Enrolled {_enroll_matching(db, c)} matching student(s)")
    elif student_id:
        existing = db.scalar(select(Enrollment).where(Enrollment.course_id == cid, Enrollment.student_id == student_id))
        if action == "add" and not existing:
            db.add(Enrollment(course_id=cid, student_id=student_id))
        elif action == "remove" and existing:
            db.delete(existing)
        db.commit()
    return RedirectResponse(f"/courses/{cid}", status_code=303)
