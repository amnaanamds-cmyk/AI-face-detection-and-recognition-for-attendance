"""Attendance forecast & recovery planner.

For every student and course: where the attendance percentage is heading by the end of the term,
how many of the remaining classes the student must attend to stay above the required percentage,
a 0-100 risk score, and the behaviour patterns behind it - each explained in plain words so a
teacher (or the student) can check every number. No black box: the projection uses the student's
recent attendance rate for the remaining classes of the term.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Attendance, AttendanceStatus, ClassSession, Course, Enrollment, Student
from app.services import app_settings
from app.services.analytics import ATTENDED, NEUTRAL

RECENT = 8            # "recent" = the last 8 counted classes
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@dataclass
class Forecast:
    course: Course
    attended: int
    counted: int
    current: float | None           # % now
    recent: float | None            # % over the last RECENT classes
    remaining: int                  # classes left this term (estimate)
    projected: float | None         # % at the end of term if the recent rate continues
    threshold: float
    need: int | None                # classes of the remaining that must be attended (None = cannot recover)
    can_miss: int | None            # classes that may still be missed while staying above the threshold
    risk: int                       # 0 (safe) .. 100 (will certainly fall below)
    patterns: list[str] = field(default_factory=list)

    @property
    def level(self) -> str:
        return "high" if self.risk >= 70 else "medium" if self.risk >= 40 else "low"

    @property
    def advice(self) -> str:
        t = f"{self.threshold:.0f}%"
        if self.counted == 0:
            return "No classes recorded yet."
        if self.need is None:
            return f"Cannot reach {t} this term even with full attendance - talk to the student now."
        if self.need <= 0:
            return f"Safe: stays above {t} even if all {self.remaining} remaining classes are missed."
        return (f"Must attend {self.need} of the remaining {self.remaining} classes to stay above {t} "
                f"(can miss at most {self.can_miss or 0}).")


def term_remaining(db: Session, course: Course, term_weeks: int, today: date) -> int:
    """Remaining classes this term: classes per week (from the schedule, or observed) x weeks left."""
    first, held = db.execute(select(func.min(ClassSession.date), func.count(ClassSession.id))
                             .where(ClassSession.course_id == course.id)).one()
    if not held:
        return 0
    if course.schedule_start and course.schedule_days:
        per_week = len([d for d in course.schedule_days.split(",") if d != ""])
    else:
        weeks = max(1.0, ((today - first).days + 1) / 7)
        per_week = held / weeks
    weeks_left = max(0.0, term_weeks - ((today - first).days + 1) / 7)
    return max(0, round(per_week * weeks_left))


def _patterns(recs: list[Attendance]) -> list[str]:
    out = []
    counted = [r for r in recs if r.status not in NEUTRAL]
    absences = [r for r in counted if r.status == AttendanceStatus.absent]
    # absences concentrated on one weekday
    by_day = defaultdict(lambda: [0, 0])
    for r in counted:
        by_day[r.date.weekday()][1] += 1
        if r.status == AttendanceStatus.absent:
            by_day[r.date.weekday()][0] += 1
    overall = len(absences) / len(counted) if counted else 0
    for d, (a, n) in sorted(by_day.items(), key=lambda kv: -kv[1][0]):
        if a >= 3 and n and a / n >= max(0.5, 2 * overall * 0.9) and a / n > overall:
            out.append(f"Often absent on {DAYS[d]}s ({a} of {n}).")
            break
    # lateness
    attended = [r for r in counted if r.status in ATTENDED]
    late = sum(1 for r in attended if r.status == AttendanceStatus.late)
    if len(attended) >= 4 and late / len(attended) >= 0.3:
        out.append(f"Frequently late ({late} of {len(attended)} attended classes).")
    # current streak of absences
    streak = 0
    for r in reversed(counted):
        if r.status != AttendanceStatus.absent:
            break
        streak += 1
    if streak >= 3:
        out.append(f"Absent from the last {streak} classes in a row.")
    # trend
    if len(counted) >= RECENT + 4:
        before = counted[:-RECENT]
        b = sum(r.status in ATTENDED for r in before) / len(before)
        a = sum(r.status in ATTENDED for r in counted[-RECENT:]) / RECENT
        if a <= b - 0.15:
            out.append(f"Attendance is dropping: {a:.0%} in the last {RECENT} classes vs {b:.0%} before.")
        elif a >= b + 0.15:
            out.append(f"Improving: {a:.0%} in the last {RECENT} classes vs {b:.0%} before.")
    return out


def forecast_course(course: Course, recs: list[Attendance], remaining: int, threshold: float) -> Forecast:
    recs = sorted(recs, key=lambda r: (r.date, r.session.start_time if r.session else r.date, r.id or 0))
    counted = [r for r in recs if r.status not in NEUTRAL]
    attended = sum(1 for r in counted if r.status in ATTENDED)
    n = len(counted)
    current = 100.0 * attended / n if n else None
    last = counted[-RECENT:]
    recent = 100.0 * sum(1 for r in last if r.status in ATTENDED) / len(last) if last else None
    t = threshold / 100.0
    total = n + remaining
    projected = need = can_miss = None
    if n:
        projected = 100.0 * (attended + (recent or 0) / 100.0 * remaining) / total if total else current
        need_raw = math.ceil(t * total - attended - 1e-9)
        need = need_raw if need_raw <= remaining else None
        can_miss = math.floor(attended + remaining - t * total + 1e-9)
        if can_miss < 0:
            can_miss = None
    # risk: how far the projection falls below the threshold, how much of the remaining term must be
    # attended, and current momentum - squashed to 0..100
    if not n:
        risk = 0
    elif need is None:
        risk = 100
    else:
        gap = (threshold - (projected if projected is not None else threshold)) / 10.0       # +1 per 10 points short
        pressure = (max(need, 0) / remaining) if remaining else (1.0 if (current or 0) < threshold else 0.0)
        momentum = ((current or 0) - (recent or 0)) / 20.0                                   # +1 per 20 points worse recently
        z = 1.6 * gap + 2.2 * (pressure - 0.6) + 0.8 * momentum
        risk = round(100 / (1 + math.exp(-2.0 * z)))
    return Forecast(course=course, attended=attended, counted=n, current=current, recent=recent, remaining=remaining,
                    projected=projected, threshold=threshold, need=need, can_miss=can_miss, risk=int(risk),
                    patterns=_patterns(recs))


def student_forecasts(db: Session, student: Student, today: date | None = None,
                      course_ids: list[int] | None = None) -> list[Forecast]:
    today = today or date.today()
    threshold = float(app_settings.get_setting(db, student.org_id, "low_attendance_threshold"))
    weeks = int(app_settings.get_setting(db, student.org_id, "term_weeks"))
    q = select(Course).join(Enrollment).where(Enrollment.student_id == student.id)
    if course_ids is not None:
        q = q.where(Course.id.in_(course_ids))
    out = []
    for course in db.scalars(q).all():
        recs = db.scalars(select(Attendance).where(Attendance.student_id == student.id,
                                                   Attendance.course_id == course.id)).all()
        if recs:
            out.append(forecast_course(course, list(recs), term_remaining(db, course, weeks, today), threshold))
    return sorted(out, key=lambda f: -f.risk)


def at_risk(db: Session, org_id: int, course_ids: list[int] | None = None, limit: int = 10,
            today: date | None = None, min_risk: int = 40) -> list[dict]:
    """Students most likely to end the term below the required attendance, highest risk first."""
    today = today or date.today()
    threshold = float(app_settings.get_setting(db, org_id, "low_attendance_threshold"))
    weeks = int(app_settings.get_setting(db, org_id, "term_weeks"))
    cq = select(Course).where(Course.org_id == org_id)
    if course_ids is not None:
        cq = cq.where(Course.id.in_(course_ids))
    rows = []
    for course in db.scalars(cq).all():
        remaining = term_remaining(db, course, weeks, today)
        by_student = defaultdict(list)
        for r in db.scalars(select(Attendance).where(Attendance.course_id == course.id)).all():
            by_student[r.student_id].append(r)
        for sid, recs in by_student.items():
            f = forecast_course(course, recs, remaining, threshold)
            if f.risk >= min_risk:
                rows.append({"student": recs[0].student, "forecast": f})
    rows.sort(key=lambda x: (-x["forecast"].risk, x["forecast"].current or 0))
    return rows[:limit]
