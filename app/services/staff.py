"""Staff attendance: teachers check in by face at the kiosk, like students.

Each teacher who registers a face gets a "staff person" record (Student.staff_user_id = their login),
enrolled only in the organization's STAFF group. That group has a schedule (check-in time and
days), so the existing machinery does the rest: the kiosk marks the teacher present or late, and
whoever has not checked in when the school day ends is marked absent (holidays excepted).
Staff never appear in student lists, counts, the principal's subject tables or the plan limit.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Attendance, AttendanceStatus, Course, Enrollment, Role, Student, User, now
from app.services.analytics import ATTENDED, rate

CODE = "STAFF"


def staff_course(db: Session, org_id: int) -> Course:
    c = db.scalar(select(Course).where(Course.org_id == org_id, Course.code == CODE))
    if c is None:
        c = Course(org_id=org_id, code=CODE, name="Staff attendance", department="Staff", semester=0, section="-",
                   schedule_start="08:00", schedule_days="0,1,2,3,4,5", schedule_minutes=420)
        db.add(c)
        db.commit()
    return c


def person_for(db: Session, user: User) -> Student | None:
    return db.scalar(select(Student).where(Student.staff_user_id == user.id))


def ensure_person(db: Session, user: User) -> Student:
    """The staff record of a teacher (created the first time), enrolled in the STAFF group."""
    st = person_for(db, user)
    if st is None:
        st = Student(org_id=user.org_id, student_code=f"STAFF-{user.username}"[:40], name=user.full_name,
                     department="Staff", semester=0, section="-", email=user.email, staff_user_id=user.id,
                     consent_given=True, consent_at=now())
        db.add(st)
        db.flush()
    course = staff_course(db, user.org_id)
    if not db.scalar(select(Enrollment.id).where(Enrollment.student_id == st.id, Enrollment.course_id == course.id)):
        db.add(Enrollment(student_id=st.id, course_id=course.id))
    db.commit()
    return st


def set_schedule(db: Session, org_id: int, start: str, days: list[int], minutes: int) -> Course:
    from app.services.timetable import parse_time

    c = staff_course(db, org_id)
    c.schedule_start = parse_time(start)
    c.schedule_days = ",".join(str(d) for d in sorted(set(days)) if 0 <= d <= 6)
    c.schedule_minutes = max(30, min(int(minutes), 900))
    db.commit()
    return c


def summary(db: Session, org_id: int, start: date | None, end: date, today: date | None = None) -> dict[int, dict]:
    """Per teacher (user id): their staff record, attendance % in the range and today's check-in."""
    today = today or now().date()
    staff = {u.id: u for u in db.scalars(select(User).where(User.org_id == org_id, User.is_active.is_(True),
                                                            User.role.in_((Role.teacher, Role.admin))))}
    people = {s.staff_user_id: s for s in db.scalars(select(Student).where(Student.org_id == org_id,
                                                                            Student.staff_user_id.is_not(None)))}
    course = db.scalar(select(Course).where(Course.org_id == org_id, Course.code == CODE))
    recs: dict[int, list[Attendance]] = {}
    if course is not None and people:
        q = select(Attendance).where(Attendance.course_id == course.id, Attendance.date <= max(end, today))
        for r in db.scalars(q):
            recs.setdefault(r.student_id, []).append(r)
    out = {}
    for uid, u in staff.items():
        p = people.get(uid)
        mine = recs.get(p.id, []) if p else []
        in_range = [r for r in mine if (start is None or r.date >= start) and r.date <= end]
        out[uid] = {"user": u, "person": p, "face": bool(p and p.face_registered),
                    "rate": rate(r.status for r in in_range),
                    "attended": sum(1 for r in in_range if r.status in ATTENDED),
                    "late": sum(1 for r in in_range if r.status == AttendanceStatus.late),
                    "absent": sum(1 for r in in_range if r.status == AttendanceStatus.absent),
                    "today": next((r for r in mine if r.date == today), None)}
    return out
