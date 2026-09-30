"""Low-attendance alerts (in-app, plus optional e-mail when SMTP is configured)."""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Course, Notification, now
from app.services import app_settings

log = logging.getLogger(__name__)

MIN_SESSIONS_FOR_ALERT = 3  # avoid alarming students after a single missed class


def send_email(to: str, subject: str, body: str) -> bool:
    if not (settings.smtp_host and to):
        return False
    msg = EmailMessage()
    msg["From"] = settings.smtp_sender or settings.smtp_user
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException) as exc:  # never break attendance because of e-mail
        log.warning("Could not send e-mail to %s: %s", to, exc)
        return False


def check_low_attendance(db: Session, course_id: int) -> list[Notification]:
    from app.services.analytics import course_summary  # local import avoids a cycle

    threshold = float(app_settings.get_setting(db, "low_attendance_threshold"))
    course = db.get(Course, course_id)
    created = []
    for row in course_summary(db, course_id)["rows"]:
        student, pct = row["student"], row["rate"]
        counted = row["total"] - row["excused"] - row["leave"]
        if pct is not None and pct >= threshold:
            # attendance recovered: older unread warnings for this course are no longer current
            for old in db.scalars(select(Notification).where(
                    Notification.student_id == student.id, Notification.course_id == course_id,
                    Notification.is_read.is_(False))).all():
                old.is_read = True
            continue
        if pct is None or counted < MIN_SESSIONS_FOR_ALERT:
            continue
        text = (f"Attendance alert - {student.name} ({student.student_code}) has {pct:.1f}% attendance in "
                f"{course.code} {course.name}, below the configured threshold of {threshold:.0f}%.")
        current = db.scalar(select(Notification).where(
            Notification.student_id == student.id, Notification.course_id == course_id,
            Notification.is_read.is_(False)))
        if current:  # one live alert per student and course, kept up to date
            current.message, current.created_at = text, now()
            continue
        n = Notification(student_id=student.id, course_id=course_id, level="warning", message=text)
        db.add(n)
        created.append(n)
        send_email(student.email or "", "Attendance alert", text)
    db.commit()
    return created
