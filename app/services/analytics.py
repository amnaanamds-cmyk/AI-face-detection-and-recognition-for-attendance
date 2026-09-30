"""Attendance statistics.

Attendance % = (Present + Late) / (all records - Excused - Leave) * 100.
Excused and Leave records are neutral: they neither help nor hurt a student.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Attendance, AttendanceStatus, ClassSession, Course, Enrollment, Student

ATTENDED = {AttendanceStatus.present, AttendanceStatus.late}
NEUTRAL = {AttendanceStatus.excused, AttendanceStatus.leave}


def rate(statuses) -> float | None:
    statuses = list(statuses)
    counted = [s for s in statuses if s not in NEUTRAL]
    if not counted:
        return None
    return round(100.0 * sum(1 for s in counted if s in ATTENDED) / len(counted), 1)


def _records(db: Session, course_ids: list[int] | None = None, start: date | None = None, end: date | None = None,
             student_id: int | None = None):
    q = select(Attendance)
    if course_ids is not None:
        q = q.where(Attendance.course_id.in_(course_ids))
    if start:
        q = q.where(Attendance.date >= start)
    if end:
        q = q.where(Attendance.date <= end)
    if student_id:
        q = q.where(Attendance.student_id == student_id)
    return db.scalars(q).all()


def counts(records) -> dict[str, int]:
    c = {s.value: 0 for s in AttendanceStatus}
    for r in records:
        c[r.status.value] += 1
    return c


def student_summary(db: Session, student_id: int, course_ids: list[int] | None = None) -> dict:
    recs = _records(db, course_ids, student_id=student_id)
    per_course = defaultdict(list)
    for r in recs:
        per_course[r.course_id].append(r.status)
    courses = {c.id: c for c in db.scalars(select(Course).where(Course.id.in_(list(per_course)))).all()}
    return {
        "total_classes": len(recs),
        "counts": counts(recs),
        "rate": rate(r.status for r in recs),
        "courses": [
            {"course": courses[cid], "total": len(st), "attended": sum(1 for s in st if s in ATTENDED),
             "rate": rate(st)}
            for cid, st in sorted(per_course.items())
        ],
    }


def course_summary(db: Session, course_id: int) -> dict:
    """Per-student table for one course (used by reports and the analytics page)."""
    students = db.scalars(
        select(Student).join(Enrollment).where(Enrollment.course_id == course_id).order_by(Student.student_code)
    ).all()
    recs = _records(db, [course_id])
    by_student = defaultdict(list)
    for r in recs:
        by_student[r.student_id].append(r.status)
    n_sessions = db.query(ClassSession).filter(ClassSession.course_id == course_id).count()
    rows = []
    for s in students:
        st = by_student.get(s.id, [])
        c = {k.value: st.count(k) for k in AttendanceStatus}
        rows.append({"student": s, **c, "total": len(st), "rate": rate(st)})
    return {"sessions": n_sessions, "rows": rows, "rate": rate(r.status for r in recs)}


def dashboard(db: Session, course_ids: list[int] | None = None, today: date | None = None,
              org_id: int | None = None) -> dict:
    """org_id given (administrators): count every active person of the organization."""
    today = today or date.today()
    sq = select(ClassSession).where(ClassSession.date == today)
    if course_ids is not None:
        sq = sq.where(ClassSession.course_id.in_(course_ids))
    sessions_today = db.scalars(sq).all()

    expected = 0
    for s in sessions_today:
        expected += db.query(Enrollment).filter(Enrollment.course_id == s.course_id).count()
    recs_today = _records(db, course_ids, start=today, end=today)
    c = counts(recs_today)
    attended = c["present"] + c["late"]

    student_q = select(Student).where(Student.is_active.is_(True))
    if org_id is not None:
        student_q = student_q.where(Student.org_id == org_id)
    elif course_ids is not None:
        student_q = student_q.join(Enrollment).where(Enrollment.course_id.in_(course_ids)).distinct()
    students = db.scalars(student_q).all()

    recent = sorted((r for r in recs_today if r.marked_at), key=lambda r: r.marked_at, reverse=True)[:10]
    return {
        "total_students": len(students),
        "faces_registered": sum(1 for s in students if s.face_registered),
        "sessions_today": sessions_today,
        "expected_today": expected,
        "present_today": attended,
        "absent_today": c["absent"],
        "late_today": c["late"],
        "rate_today": round(100.0 * attended / expected, 1) if expected else None,
        "recent": recent,
        "overall_rate": rate(r.status for r in _records(db, course_ids)),
    }


def daily_trend(db: Session, days: int = 30, course_ids: list[int] | None = None, today: date | None = None) -> list[dict]:
    today = today or date.today()
    start = today - timedelta(days=days - 1)
    by_day = defaultdict(list)
    for r in _records(db, course_ids, start=start, end=today):
        by_day[r.date].append(r.status)
    return [{"date": d.isoformat(), "rate": rate(st), "records": len(st)} for d, st in sorted(by_day.items())]


def weekly_trend(db: Session, weeks: int = 12, course_ids: list[int] | None = None, today: date | None = None) -> list[dict]:
    today = today or date.today()
    start = today - timedelta(weeks=weeks)
    by_week = defaultdict(list)
    for r in _records(db, course_ids, start=start, end=today):
        y, w, _ = r.date.isocalendar()
        by_week[f"{y}-W{w:02d}"].append(r.status)
    return [{"week": k, "rate": rate(v)} for k, v in sorted(by_week.items())]


def monthly_trend(db: Session, course_ids: list[int] | None = None) -> list[dict]:
    by_month = defaultdict(list)
    for r in _records(db, course_ids):
        by_month[r.date.strftime("%Y-%m")].append(r.status)
    return [{"month": k, "rate": rate(v)} for k, v in sorted(by_month.items())]


def course_rates(db: Session, course_ids: list[int] | None = None) -> list[dict]:
    q = select(Course)
    if course_ids is not None:
        q = q.where(Course.id.in_(course_ids))
    out = []
    for c in db.scalars(q.order_by(Course.code)).all():
        out.append({"course": f"{c.code} - {c.name}", "rate": rate(r.status for r in _records(db, [c.id]))})
    return out


def student_ranking(db: Session, course_ids: list[int] | None = None, limit: int = 5) -> dict:
    by_student = defaultdict(list)
    for r in _records(db, course_ids):
        by_student[r.student_id].append(r.status)
    students = {s.id: s for s in db.scalars(select(Student).where(Student.id.in_(list(by_student)))).all()}
    ranked = [
        {"student": students[sid], "rate": rate(st), "absences": st.count(AttendanceStatus.absent)}
        for sid, st in by_student.items() if rate(st) is not None
    ]
    ranked.sort(key=lambda x: (x["rate"], -x["absences"]))
    return {"lowest": ranked[:limit], "highest": list(reversed(ranked[-limit:]))}


def absence_by_weekday(db: Session, course_ids: list[int] | None = None) -> list[dict]:
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    c = [0] * 7
    for r in _records(db, course_ids):
        if r.status == AttendanceStatus.absent:
            c[r.date.weekday()] += 1
    return [{"day": names[i], "absences": c[i]} for i in range(7)]
