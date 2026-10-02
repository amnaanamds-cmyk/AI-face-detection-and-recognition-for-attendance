"""Exam impersonation guard.

An exam session is an attendance session with stricter rules:
* liveness is always required (no photos / screens);
* a registered person who is NOT a candidate of this exam raises an "impersonation suspected" alert
  (the classic case: someone sitting the exam for a friend);
* an unregistered face that stays in front of the camera raises an "unregistered person" alert;
* two faces matching one candidate raise the proxy alert (see proxy.identity_clash);
* marking a candidate present by hand needs a written reason, which goes into the tamper-evident ledger;
* the invigilation report states how every candidate was verified and is digitally signed, so a
  board or university can check it at /verify.
"""
from __future__ import annotations

import io

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Attendance, AttendanceStatus, ClassSession, Enrollment, Notification, Student, now
from app.services import certificates, ledger

LEVEL = "exam"


def alert(db: Session, session: ClassSession, message: str, student: Student | None = None) -> Notification:
    n = Notification(level=LEVEL, course_id=session.course_id, student_id=student.id if student else None,
                     message=f"{session.course.code} exam {session.date:%d %b}: {message}")
    db.add(n)
    db.commit()
    return n


def not_a_candidate(db: Session, session: ClassSession, student: Student) -> None:
    alert(db, session, f"impersonation suspected - {student.name} ({student.student_code}) was recognised in the exam hall at "
                       f"{now():%H:%M} but is not a candidate of this exam.", student)


def unregistered_face(db: Session, session: ClassSession) -> None:
    alert(db, session, f"an unregistered person was in front of the camera at {now():%H:%M}. Check their identity.")


def alerts(db: Session, session: ClassSession) -> list[Notification]:
    from datetime import datetime, timedelta

    start = datetime.combine(session.date, datetime.min.time())
    return db.scalars(select(Notification).where(Notification.course_id == session.course_id,
                                                 Notification.level.in_((LEVEL, "proxy")),
                                                 Notification.created_at >= start,
                                                 Notification.created_at < start + timedelta(days=1))
                      .order_by(Notification.created_at)).all()


def rows(db: Session, session: ClassSession) -> list[dict]:
    candidates = db.scalars(select(Student).join(Enrollment).where(Enrollment.course_id == session.course_id)
                            .order_by(Student.student_code)).all()
    recs = {r.student_id: r for r in db.scalars(select(Attendance).where(Attendance.session_id == session.id))}
    out = []
    for st in candidates:
        r = recs.get(st.id)
        if r is None or r.status not in (AttendanceStatus.present, AttendanceStatus.late):
            how = r.status.value if r else "not seen"
        elif r.method in ("face", "kiosk"):
            how = (f"face {r.marked_at:%H:%M}, match {r.confidence:.2f}" if r.confidence is not None else f"face {r.marked_at:%H:%M}")
            if r.liveness_score is not None:
                how += f", liveness {r.liveness_score:.2f}"
        else:
            by = next((e.actor for e in reversed(ledger.history(db, r.id))), "?")
            how = f"BY HAND by {by}: {r.note or 'no reason'}"
        out.append({"code": st.student_code, "name": st.name, "status": r.status.value if r else "absent",
                    "verified": bool(r and r.method in ("face", "kiosk") and r.status in (AttendanceStatus.present, AttendanceStatus.late)),
                    "how": how})
    return out


def report_data(db: Session, session: ClassSession) -> dict:
    rs = rows(db, session)
    org = session.course.org
    return {"v": 1, "kind": "exam", "org": org.name if org else "", "exam": f"{session.course.code} {session.course.name}",
            "date": session.date.isoformat(), "start": session.start_time.strftime("%H:%M"),
            "candidates": len(rs), "face_verified": sum(r["verified"] for r in rs),
            "by_hand": sum(1 for r in rs if r["how"].startswith("BY HAND")),
            "absent": sum(1 for r in rs if r["status"] in ("absent", "not seen")),
            "alerts": len(alerts(db, session)), "issued": now().replace(microsecond=0).isoformat(),
            "ledger": ledger.fingerprint(db, session.course.org_id), "kid": certificates.key_id()}


def report_pdf(db: Session, session: ClassSession, verify_url: str) -> bytes:
    from html import escape as e

    import segno
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    data = report_data(db, session)
    token = certificates.sign(data)
    qr = io.BytesIO()
    segno.make(f"{verify_url}?c={token}", error="l").save(qr, kind="png", scale=4, border=2)
    qr.seek(0)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title="Invigilation report", leftMargin=15 * mm, rightMargin=15 * mm)
    st = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=st["Normal"], fontSize=8, leading=10)
    code = ParagraphStyle("code", parent=st["Normal"], fontName="Courier", fontSize=6, leading=7, wordWrap="CJK")
    story = [Paragraph(e(data["org"]), st["Heading2"]), Paragraph("Exam invigilation report", st["Title"]),
             Paragraph(f"{e(data['exam'])} &middot; {data['date']} {data['start']} &middot; {data['candidates']} candidates: "
                       f"<b>{data['face_verified']}</b> verified by face, <b>{data['by_hand']}</b> by hand, "
                       f"<b>{data['absent']}</b> absent, <b>{data['alerts']}</b> alert(s).", st["Normal"]), Spacer(1, 8)]
    table = [["ID", "Candidate", "Status", "How identity was verified"]] + [
        [r["code"], Paragraph(e(r["name"]), small), r["status"], Paragraph(e(r["how"]), small)] for r in rows(db, session)]
    t = Table(table, colWidths=[28 * mm, 48 * mm, 20 * mm, 84 * mm], repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E79")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                           ("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("FONTSIZE", (0, 0), (-1, -1), 8)]))
    story.append(t)
    al = alerts(db, session)
    if al:
        story += [Spacer(1, 8), Paragraph("Alerts", st["Heading3"])] + [
            Paragraph(f"{a.created_at:%H:%M} - {e(a.message)}", small) for a in al]
    story += [Spacer(1, 10), Image(qr, width=38 * mm, height=38 * mm),
              Paragraph(f"Digitally signed (key {data['kid']}); scan or open {verify_url} to check it has not been altered. "
                        f"Attendance ledger {data['ledger']}.", small), Spacer(1, 4), Paragraph(token, code)]
    doc.build(story)
    return buf.getvalue()
