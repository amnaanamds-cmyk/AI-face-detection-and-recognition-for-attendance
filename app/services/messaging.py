"""Messages to parents / guardians when a student is absent (or late).

When a session closes, one message per absent student is put in the outbox and delivered by the
channel chosen in Admin > Parent messages:

* ``phone``    - free: the FaceAttend Android app on a school phone fetches the messages and sends
                 them as normal SMS from its SIM card (``/api/gateway/*``)
* ``sms``      - Twilio SMS        (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_SMS_FROM)
* ``whatsapp`` - Twilio WhatsApp   (... TWILIO_WHATSAPP_FROM)
* ``manual``   - nothing is sent automatically; the outbox page has one-click WhatsApp / SMS links
plus e-mail to the guardian (SMTP) when enabled.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import database
from app.config import settings
from app.models import Attendance, AttendanceStatus, ClassSession, Organization, OutboxMessage, Student, now
from app.services import app_settings
from app.services.notifications import send_email

log = logging.getLogger(__name__)
RUN_IN_THREAD = True        # tests deliver synchronously
SENDING_TIMEOUT = timedelta(minutes=10)


class _Keep(dict):
    def __missing__(self, key):  # unknown {placeholders} stay visible instead of crashing
        return "{" + key + "}"


def normalize_phone(raw: str | None, country_code: str = "92") -> str | None:
    """'0300-1234567' -> '+923001234567'; '+44 20 ...' stays international. None if unusable."""
    if not raw:
        return None
    s = raw.strip()
    digits = re.sub(r"\D", "", s)
    if len(digits) < 7:
        return None
    if s.startswith("+"):
        return "+" + digits
    if digits.startswith("00"):
        return "+" + digits[2:]
    cc = re.sub(r"\D", "", country_code or "")
    if digits.startswith("0"):
        return f"+{cc}{digits[1:]}"
    if cc and digits.startswith(cc) and len(digits) > 10:
        return "+" + digits
    return f"+{cc}{digits}"


def render(template: str, **values) -> str:
    return template.format_map(_Keep({k: ("" if v is None else v) for k, v in values.items()}))


def message_text(db: Session, rec: Attendance) -> str:
    st, course = rec.student, rec.course
    org = db.get(Organization, st.org_id)
    tmpl = str(app_settings.get_setting(db, st.org_id, "parent_template"))
    when = rec.session.start_time if rec.session else None
    return render(tmpl, parent=st.guardian_name or "Parent", student=st.name, status=rec.status.value,
                  group=f"{course.code} {course.name}" if course else "", date=rec.date.strftime("%d %b %Y"),
                  time=when.strftime("%H:%M") if when else "", org=org.name if org else "")


def queue_for_session(db: Session, session: ClassSession, force: bool = False) -> int:
    """Put absence (and optionally lateness) messages for one session in the outbox.

    Each record gets at most one message per channel, so calling this again never double-sends.
    ``force`` ignores the 'off' setting (the teacher pressed "Message parents" by hand)."""
    org_id = session.course.org_id
    mode = str(app_settings.get_setting(db, org_id, "parent_alerts"))
    if mode == "off" and not force:
        return 0
    statuses = {AttendanceStatus.absent} | ({AttendanceStatus.late} if mode == "absent+late" else set())
    channel = str(app_settings.get_setting(db, org_id, "parent_channel"))
    email_too = bool(app_settings.get_setting(db, org_id, "parent_email"))
    cc = str(app_settings.get_setting(db, org_id, "country_code"))
    have = {(m.attendance_id, m.channel) for m in db.scalars(
        select(OutboxMessage).join(Attendance, OutboxMessage.attendance_id == Attendance.id)
        .where(Attendance.session_id == session.id))}
    added = 0
    for rec in db.scalars(select(Attendance).where(Attendance.session_id == session.id)):
        if rec.status not in statuses:
            continue
        st = rec.student
        body = None
        phone = normalize_phone(st.guardian_phone, cc)
        if phone and (rec.id, channel) not in have:
            body = message_text(db, rec)
            db.add(OutboxMessage(org_id=org_id, student_id=st.id, attendance_id=rec.id, channel=channel,
                                 recipient=phone, body=body, status="manual-pending" if channel == "manual" else "pending"))
            added += 1
        if email_too and st.guardian_email and (rec.id, "email") not in have:
            db.add(OutboxMessage(org_id=org_id, student_id=st.id, attendance_id=rec.id, channel="email",
                                 recipient=st.guardian_email, body=body or message_text(db, rec)))
            added += 1
    db.commit()
    if added:
        deliver_later(org_id)
    return added


# ------------------------------------------------------------------ delivery
class DeliveryError(RuntimeError):
    pass


def twilio_configured(channel: str) -> bool:
    sender = settings.twilio_whatsapp_from if channel == "whatsapp" else settings.twilio_sms_from
    return bool(settings.twilio_account_sid and settings.twilio_auth_token and sender)


def send_twilio(channel: str, to: str, body: str) -> None:
    if not twilio_configured(channel):
        raise DeliveryError("Twilio is not configured (TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / sender number)")
    sender = settings.twilio_whatsapp_from if channel == "whatsapp" else settings.twilio_sms_from
    prefix = "whatsapp:" if channel == "whatsapp" else ""
    data = urllib.parse.urlencode({"To": prefix + to, "From": prefix + sender, "Body": body}).encode()
    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json"
    auth = base64.b64encode(f"{settings.twilio_account_sid}:{settings.twilio_auth_token}".encode()).decode()
    req = urllib.request.Request(url, data=data, headers={"Authorization": "Basic " + auth})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        try:
            detail = json.loads(detail).get("message", detail)
        except ValueError:
            pass
        raise DeliveryError(f"Twilio {exc.code}: {detail}"[:250]) from exc
    except OSError as exc:
        raise DeliveryError(f"network error: {exc}"[:250]) from exc


def deliver_pending(db: Session, org_id: int) -> dict[str, int]:
    """Send everything the server can send itself (Twilio, e-mail). Phone-gateway messages wait
    for the Android app; manual ones for a click."""
    done = {"sent": 0, "failed": 0}
    for msg in db.scalars(select(OutboxMessage).where(OutboxMessage.org_id == org_id, OutboxMessage.status == "pending",
                                                      OutboxMessage.channel.in_(("sms", "whatsapp", "email")))).all():
        msg.attempts += 1
        try:
            if msg.channel == "email":
                if not send_email(msg.recipient, "Attendance notice", msg.body):
                    raise DeliveryError("e-mail not sent (check the SMTP_* settings)")
            else:
                send_twilio(msg.channel, msg.recipient, msg.body)
            msg.status, msg.sent_at, msg.error = "sent", now(), None
            done["sent"] += 1
        except DeliveryError as exc:
            msg.status, msg.error = "failed", str(exc)
            done["failed"] += 1
        db.commit()
    return done


def deliver_later(org_id: int) -> None:
    def work():
        with database.SessionLocal() as db:
            try:
                deliver_pending(db, org_id)
            except Exception:  # noqa: BLE001 - delivery must never break attendance
                log.exception("message delivery failed")

    if RUN_IN_THREAD:
        threading.Thread(target=work, daemon=True).start()
    else:
        work()


# ------------------------------------------------------------------ Android phone gateway
def gateway_pull(db: Session, org_id: int, limit: int = 20) -> list[OutboxMessage]:
    """Hand the next messages to the phone. Messages a phone took but never reported on
    (switched off, no signal) are offered again after 10 minutes."""
    stale = now() - SENDING_TIMEOUT
    for m in db.scalars(select(OutboxMessage).where(OutboxMessage.org_id == org_id, OutboxMessage.channel == "phone",
                                                    OutboxMessage.status == "sending", OutboxMessage.sent_at < stale)):
        m.status = "pending"
    msgs = db.scalars(select(OutboxMessage).where(OutboxMessage.org_id == org_id, OutboxMessage.channel == "phone",
                                                  OutboxMessage.status == "pending")
                      .order_by(OutboxMessage.id).limit(limit)).all()
    for m in msgs:
        m.status, m.attempts, m.sent_at = "sending", m.attempts + 1, now()  # sent_at = taken at, until reported
    db.commit()
    return msgs


def gateway_report(db: Session, org_id: int, msg_id: int, ok: bool, error: str | None = None) -> bool:
    m = db.get(OutboxMessage, msg_id)
    if m is None or m.org_id != org_id:
        return False
    m.status, m.error, m.sent_at = ("sent", None, now()) if ok else ("failed", (error or "phone could not send")[:250], m.sent_at)
    db.commit()
    return True


def whatsapp_link(msg: OutboxMessage) -> str:
    return f"https://wa.me/{msg.recipient.lstrip('+')}?text={urllib.parse.quote(msg.body)}"


def sms_link(msg: OutboxMessage) -> str:
    return f"sms:{msg.recipient}?body={urllib.parse.quote(msg.body)}"


def students_without_contact(db: Session, org_id: int) -> int:
    return len(db.scalars(select(Student.id).where(Student.org_id == org_id, Student.is_active.is_(True),
                                                   Student.guardian_phone.is_(None), Student.guardian_email.is_(None))).all())
