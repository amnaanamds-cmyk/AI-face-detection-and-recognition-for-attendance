"""Proxy watch: catching attendance that cannot be true.

* Identity clash   - two faces in the same camera frame are confirmed as the same person
                     (one is a photo, a screen, a twin or a look-alike). Neither is accepted.
* Impossible presence - a person is recognised in two different classes whose times overlap
                     (two cameras, two rooms, the same minute).
* Manual overrides - who marks people "present" by hand, and how often, taken from the
                     tamper-evident ledger (camera never saw the person, a staff member said so).
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Attendance, AttendanceStatus, ClassSession, LedgerEntry, Notification, Student, now

LEVEL = "proxy"


def raise_alert(db: Session, student: Student, course_id: int | None, message: str) -> Notification | None:
    """One open alert per person, group and day (repeated frames do not flood the list)."""
    day_start = datetime.combine(now().date(), datetime.min.time())
    exists = db.scalar(select(Notification).where(Notification.level == LEVEL, Notification.student_id == student.id,
                                                  Notification.course_id == course_id,
                                                  Notification.created_at >= day_start))
    if exists:
        return None
    n = Notification(student_id=student.id, course_id=course_id, level=LEVEL, message=message)
    db.add(n)
    return n


def identity_clash(db: Session, student: Student, session: ClassSession | None) -> None:
    where = f" in {session.course.code}" if session else " at the kiosk"
    raise_alert(db, student, session.course_id if session else None,
                f"Possible proxy - two faces in the same camera frame{where} matched {student.name} "
                f"({student.student_code}) at {now():%H:%M}. One of them is a photo, a screen or a look-alike; "
                "neither was accepted.")


def check_impossible_presence(db: Session, rec: Attendance) -> Notification | None:
    """After a face/kiosk mark: was the same person also recognised in another class running at the same time?"""
    if rec.method not in ("face", "kiosk") or rec.marked_at is None:
        return None
    sess = rec.session or db.get(ClassSession, rec.session_id)
    others = db.scalars(select(Attendance).where(
        Attendance.student_id == rec.student_id, Attendance.date == rec.date, Attendance.id != rec.id,
        Attendance.session_id != rec.session_id, Attendance.method.in_(("face", "kiosk")),
        Attendance.marked_at.is_not(None))).all()
    for o in others:
        other = o.session
        if other is None or other.course_id == sess.course_id:
            continue
        overlap = sess.start_time < other.end_time and other.start_time < sess.end_time
        close_in_time = abs((rec.marked_at - o.marked_at).total_seconds()) <= 15 * 60
        if overlap and close_in_time:
            st = rec.student
            return raise_alert(db, st, rec.course_id,
                               f"Possible proxy - {st.name} ({st.student_code}) was recognised in {other.course.code} at "
                               f"{o.marked_at:%H:%M} and in {sess.course.code} at {rec.marked_at:%H:%M}, but the two "
                               "classes run at the same time. Check both rooms.")
    return None


def manual_overrides(db: Session, org_id: int, days: int = 30) -> list[dict]:
    """Per staff member: how many people they marked present/late by hand vs. all such marks."""
    since = now() - timedelta(days=days)
    per = defaultdict(lambda: {"manual": 0, "total": 0})
    for e in db.scalars(select(LedgerEntry).where(LedgerEntry.org_id == org_id, LedgerEntry.at >= since,
                                                  LedgerEntry.action.in_(("create", "update")))):
        p = json.loads(e.payload)
        if p.get("status") not in (AttendanceStatus.present.value, AttendanceStatus.late.value):
            continue
        per[e.actor]["total"] += 1
        if p.get("method") == "manual":
            per[e.actor]["manual"] += 1
    rows = [{"actor": a, **v, "share": round(100 * v["manual"] / v["total"], 1) if v["total"] else 0.0}
            for a, v in per.items() if v["manual"]]
    return sorted(rows, key=lambda r: -r["manual"])
