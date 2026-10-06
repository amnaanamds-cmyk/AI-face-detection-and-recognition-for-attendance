from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.terminology import terms_of
from app.deps import admin_only, can_manage_course, course_scope, flash, get_course, get_student, render, staff
from app.models import ClassSession, Course, Enrollment, Role, Student, User
from app.services import analytics

router = APIRouter()


def _t(user):
    return terms_of(user.org)


def _teachers(db: Session, org_id: int):
    return db.scalars(select(User).where(User.org_id == org_id, User.role.in_([Role.teacher, Role.admin]),
                                         User.is_active.is_(True)).order_by(User.full_name)).all()


@router.get("/courses")
def list_courses(request: Request, user: User = Depends(staff), db: Session = Depends(get_db)):
    q = select(Course).where(Course.id.in_(course_scope(db, user))).order_by(Course.code)
    return render(request, "courses/list.html", user, courses=db.scalars(q).all())


@router.get("/courses/new")
def new_course(request: Request, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    return render(request, "courses/form.html", user, course=None, teachers=_teachers(db, user.org_id))


def _apply(db: Session, user: User, course: Course, code, name, department, semester, section, teacher_id,
           schedule_start="", schedule_days=None, schedule_minutes=480):
    course.code, course.name = code.strip(), name.strip()
    course.department, course.semester, course.section = department.strip(), semester, (section or "").strip()
    teacher = db.get(User, teacher_id) if teacher_id else None
    course.teacher_id = teacher.id if teacher and teacher.org_id == user.org_id else None
    course.schedule_start = schedule_start.strip() or None
    days = [d for d in (schedule_days or []) if d in "0123456"]
    course.schedule_days = ",".join(sorted(set(days))) if days else "0,1,2,3,4"
    course.schedule_minutes = max(5, int(schedule_minutes or 480))


@router.post("/courses/new")
def create_course(request: Request, code: str = Form(...), name: str = Form(...), department: str = Form("Computer Science"),
                  semester: int = Form(1), section: str = Form(""), teacher_id: int = Form(0),
                  enroll_matching: str = Form(""), schedule_start: str = Form(""), schedule_days: list[str] = Form([]),
                  schedule_minutes: int = Form(480), user: User = Depends(admin_only), db: Session = Depends(get_db)):
    c = Course(org_id=user.org_id)
    _apply(db, user, c, code, name, department, semester, section, teacher_id, schedule_start, schedule_days, schedule_minutes)
    db.add(c)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        flash(request, f"{_t(user).group} code {code} already exists", "danger")
        return RedirectResponse("/courses/new", status_code=303)
    if enroll_matching:
        n = _enroll_matching(db, c)
        flash(request, f"Enrolled {n} matching {_t(user).people.lower()}")
    flash(request, f"{_t(user).group} {c.code} created")
    return RedirectResponse(f"/courses/{c.id}", status_code=303)


def _enroll_matching(db: Session, c: Course) -> int:
    have = set(db.scalars(select(Enrollment.student_id).where(Enrollment.course_id == c.id)).all())
    same_dept = Student.department == c.department if c.department else True   # no department = whole class
    students = db.scalars(select(Student).where(Student.org_id == c.org_id, same_dept,
                                                Student.semester == c.semester,
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
    c = get_course(db, user, cid)
    if not can_manage_course(user, c):
        return render(request, "error.html", user, status_code=403, message="This is not your course.")
    summary = analytics.course_summary(db, cid)
    sessions = db.scalars(select(ClassSession).where(ClassSession.course_id == cid).order_by(ClassSession.start_time.desc())).all()
    enrolled = {r["student"].id for r in summary["rows"]}
    others = db.scalars(select(Student).where(Student.org_id == user.org_id, Student.is_active.is_(True),
                                              Student.staff_user_id.is_(None))
                        .order_by(Student.student_code)).all()
    return render(request, "courses/detail.html", user, course=c, summary=summary, sessions=sessions,
                  candidates=[s for s in others if s.id not in enrolled])


@router.get("/courses/{cid}/edit")
def edit_course(cid: int, request: Request, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    return render(request, "courses/form.html", user, course=get_course(db, user, cid), teachers=_teachers(db, user.org_id))


@router.post("/courses/{cid}/edit")
def update_course(cid: int, request: Request, code: str = Form(...), name: str = Form(...),
                  department: str = Form("Computer Science"), semester: int = Form(1), section: str = Form(""),
                  teacher_id: int = Form(0), schedule_start: str = Form(""), schedule_days: list[str] = Form([]),
                  schedule_minutes: int = Form(480), user: User = Depends(admin_only), db: Session = Depends(get_db)):
    c = get_course(db, user, cid)
    _apply(db, user, c, code, name, department, semester, section, teacher_id, schedule_start, schedule_days, schedule_minutes)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        flash(request, f"{_t(user).group} code already in use", "danger")
    return RedirectResponse(f"/courses/{cid}", status_code=303)


@router.post("/courses/{cid}/delete")
def delete_course(cid: int, request: Request, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    c = get_course(db, user, cid)
    db.delete(c)
    db.commit()
    flash(request, f"{_t(user).group} {c.code} deleted")
    return RedirectResponse("/courses", status_code=303)


@router.post("/courses/{cid}/enroll")
def enroll(cid: int, request: Request, student_id: int = Form(0), action: str = Form("add"),
           user: User = Depends(admin_only), db: Session = Depends(get_db)):
    c = get_course(db, user, cid)
    if action == "matching":
        flash(request, f"Enrolled {_enroll_matching(db, c)} matching {_t(user).people.lower()}")
    elif student_id:
        get_student(db, user, student_id)  # same organization only
        existing = db.scalar(select(Enrollment).where(Enrollment.course_id == cid, Enrollment.student_id == student_id))
        if action == "add" and not existing:
            db.add(Enrollment(course_id=cid, student_id=student_id))
        elif action == "remove" and existing:
            db.delete(existing)
        db.commit()
    return RedirectResponse(f"/courses/{cid}", status_code=303)
