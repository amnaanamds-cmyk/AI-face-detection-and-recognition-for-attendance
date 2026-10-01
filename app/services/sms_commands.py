"""Two-way SMS for parents: text the school phone, get an automatic answer.

    STATUS  (or ATTENDANCE, HAAZRI, HAZRI, حاضری, ?)   today's attendance of my child(ren) + this month's %
    REPORT  (or MONTH, RIPORT, رپورٹ)                   this month's % per class
    LEAVE <reason>  (or CHUTTI, CHHUTTI, چھٹی)          leave request for today - the school approves it
    HELP                                                 the list of commands

Only numbers registered as a guardian's mobile get information about their own children; anyone
else gets a polite "not registered" answer. Replies go out through the same phone (outbox), at
most a few per number per day, so nobody can run up the school's SMS bill.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (Attendance, AttendanceStatus, ClassSession, Enrollment, InboxMessage, Organization,
                        OutboxMessage, Student, now)
from app.services import app_settings
from app.services.analytics import rate
from app.services.messaging import normalize_phone

MAX_REPLIES_PER_DAY = 6
COMMANDS = {
    "status": ("status", "attendance", "haazri", "hazri", "haziri", "حاضری", "?"),
    "report": ("report", "month", "monthly", "riport", "رپورٹ"),
    "leave": ("leave", "chutti", "chhutti", "chuti", "چھٹی", "rukhsat", "رخصت"),
    "help": ("help", "madad", "مدد"),
}


def parse(body: str) -> tuple[str, str]:
    text = (body or "").strip()
    first, _, rest = text.partition(" ")
    word = re.sub(r"[^\w?؀-ۿ]", "", first.lower())
    for cmd, words in COMMANDS.items():
        if word in words:
            return cmd, rest.strip()
    return "unknown", text


def children_of(db: Session, org_id: int, sender: str, cc: str) -> list[Student]:
    want = normalize_phone(sender, cc)
    if not want:
        return []
    kids = db.scalars(select(Student).where(Student.org_id == org_id, Student.is_active.is_(True),
                                            Student.guardian_phone.is_not(None))).all()
    return [s for s in kids if normalize_phone(s.guardian_phone, cc) == want]


def _today_line(db: Session, st: Student, today: date) -> str:
    recs = db.scalars(select(Attendance).where(Attendance.student_id == st.id, Attendance.date == today)
                      .order_by(Attendance.marked_at)).all()
    if not recs:
        planned = db.scalar(select(func.count(ClassSession.id)).join(Enrollment, Enrollment.course_id == ClassSession.course_id)
                            .where(Enrollment.student_id == st.id, ClassSession.date == today))
        return f"{st.name}: no attendance recorded yet today" + ("" if planned else " (no classes today)") + "."
    parts = []
    for r in recs:
        when = f" {r.marked_at:%H:%M}" if r.marked_at and r.status in (AttendanceStatus.present, AttendanceStatus.late) else ""
        parts.append(f"{r.course.code} {r.status.value}{when}")
    return f"{st.name} today: " + ", ".join(parts) + "."


def _month_rate(db: Session, st: Student, today: date) -> float | None:
    start = today.replace(day=1)
    return rate(r.status for r in db.scalars(select(Attendance).where(Attendance.student_id == st.id,
                                                                      Attendance.date >= start)).all())


def answer(db: Session, org: Organization, sender: str, body: str, today: date | None = None) -> InboxMessage:
    """Record an incoming SMS and queue the reply (sent by the school phone)."""
    today = today or date.today()
    cc = str(app_settings.get_setting(db, org.id, "country_code"))
    cmd, arg = parse(body)
    kids = children_of(db, org.id, sender, cc)
    msg = InboxMessage(org_id=org.id, sender=normalize_phone(sender, cc) or sender[:40], body=body[:1000], command=cmd,
                       student_ids=",".join(str(k.id) for k in kids))
    db.add(msg)
    if not app_settings.get_setting(db, org.id, "parent_replies"):
        msg.state = "ignored"
        db.commit()
        return msg

    if not kids:
        reply = f"{org.name}: this number is not registered as a parent's mobile. Please contact the school office."
    elif cmd == "status":
        lines = [_today_line(db, k, today) for k in kids]
        lines += [f"{k.name} this month: {m:.0f}%." for k in kids if (m := _month_rate(db, k, today)) is not None]
        reply = " ".join(lines)
    elif cmd == "report":
        lines = []
        for k in kids:
            start = today.replace(day=1)
            per = {}
            for r in db.scalars(select(Attendance).where(Attendance.student_id == k.id, Attendance.date >= start)).all():
                per.setdefault(r.course.code, []).append(r.status)
            body_txt = ", ".join(f"{c} {rate(s):.0f}%" for c, s in sorted(per.items()) if rate(s) is not None)
            lines.append(f"{k.name} {today:%B}: {body_txt or 'no classes yet'}.")
        reply = " ".join(lines)
    elif cmd == "leave":
        msg.state = "leave-pending"
        names = ", ".join(k.name for k in kids)
        reply = (f"{org.name}: leave request for {names} today received"
                 + (f" ({arg[:60]})" if arg else "") + ". The school will confirm it.")
    else:
        reply = (f"{org.name} attendance - send: STATUS (today), REPORT (this month), "
                 "LEAVE <reason> (leave request today). Urdu: HAAZRI, CHUTTI.")
    msg.reply = reply

    day_start = datetime.combine(today, datetime.min.time())
    sent_today = db.scalar(select(func.count(OutboxMessage.id)).where(
        OutboxMessage.org_id == org.id, OutboxMessage.recipient == msg.sender, OutboxMessage.attendance_id.is_(None),
        OutboxMessage.student_id.is_(None), OutboxMessage.created_at >= day_start)) or 0
    if sent_today < MAX_REPLIES_PER_DAY:
        db.add(OutboxMessage(org_id=org.id, channel="phone", recipient=msg.sender, body=reply[:600]))
    else:
        msg.state = "rate-limited"
    db.commit()
    return msg


def approve_leave(db: Session, msg: InboxMessage, today: date | None = None) -> int:
    """Mark the children 'leave' in all of today's classes not attended yet. Returns records changed."""
    from app.services.attendance import set_status

    day = msg.created_at.date() if msg.created_at else (today or date.today())
    changed = 0
    for sid in [int(x) for x in msg.student_ids.split(",") if x]:
        sessions = db.scalars(select(ClassSession).join(Enrollment, Enrollment.course_id == ClassSession.course_id)
                              .where(Enrollment.student_id == sid, ClassSession.date == day)).all()
        for s in sessions:
            rec = db.scalar(select(Attendance).where(Attendance.student_id == sid, Attendance.session_id == s.id))
            if rec is None or rec.status in (AttendanceStatus.absent,):
                set_status(db, s, sid, AttendanceStatus.leave, note=f"leave by parent SMS: {msg.body[:120]}")
                changed += 1
    msg.state = "leave-approved"
    org = db.get(Organization, msg.org_id)
    db.add(OutboxMessage(org_id=msg.org_id, channel="phone", recipient=msg.sender,
                         body=f"{org.name if org else 'School'}: the leave request for {day:%d %b} was approved."))
    db.commit()
    return changed
