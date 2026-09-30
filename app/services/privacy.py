"""Data-protection features: retention of face templates and per-person data export."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Attendance, FaceEmbedding, Student, now
from app.services import app_settings
from app.vision.matcher import gallery_cache


def apply_retention(db: Session, org_id: int, when: datetime | None = None) -> dict[str, int]:
    """Delete face templates that are no longer needed.

    * people not seen for `face_retention_days` (counted from their last recognition, or
      from registration if never seen) lose their templates;
    * deactivated people lose their templates immediately;
    * attendance records older than `attendance_retention_days` are deleted (0 = keep forever).
    """
    when = when or now()
    days = int(app_settings.get_setting(db, org_id, "face_retention_days"))
    removed_templates = removed_records = 0
    people = db.scalars(select(Student).where(Student.org_id == org_id)).all()
    for p in people:
        last = p.last_seen_at or p.registration_date
        expired = days > 0 and last is not None and last < when - timedelta(days=days)
        if (expired or not p.is_active) and p.embeddings:
            removed_templates += len(p.embeddings)
            for e in list(p.embeddings):
                db.delete(e)
    keep = int(app_settings.get_setting(db, org_id, "attendance_retention_days"))
    if keep > 0:
        cutoff = (when - timedelta(days=keep)).date()
        old = db.scalars(select(Attendance).join(Student).where(Student.org_id == org_id, Attendance.date < cutoff)).all()
        removed_records = len(old)
        for r in old:
            db.delete(r)
    db.commit()
    if removed_templates:
        gallery_cache.invalidate(org_id)
    return {"templates": removed_templates, "records": removed_records}


def export_person(db: Session, student: Student) -> dict:
    """Everything stored about one person (GDPR right of access / portability).

    Face templates are listed but not exported: they are encrypted numbers that are only
    meaningful to this system, and exporting them would create a new copy of biometric data.
    """
    templates = db.scalars(select(FaceEmbedding).where(FaceEmbedding.student_id == student.id)).all()
    recs = db.scalars(select(Attendance).where(Attendance.student_id == student.id).order_by(Attendance.date)).all()
    iso = lambda d: d.isoformat() if d else None  # noqa: E731
    return {
        "exported_at": now().isoformat(),
        "organization": student.org.name if student.org else None,
        "person": {
            "id": student.student_code, "name": student.name, "roll_number": student.roll_number,
            "department": student.department, "semester": student.semester, "section": student.section,
            "email": student.email, "phone": student.phone, "active": student.is_active,
            "registered": iso(student.registration_date), "last_seen": iso(student.last_seen_at),
        },
        "biometric_consent": {"given": student.consent_given, "recorded_at": iso(student.consent_at)},
        "face_templates": [{"created": iso(t.created_at), "model": t.model_name,
                            "note": "encrypted 128-number template; no photo is stored"} for t in templates],
        "groups": [e.course.code + " - " + e.course.name for e in student.enrollments],
        "attendance": [{"date": iso(r.date), "group": r.course.code if r.course else None, "status": r.status.value,
                        "check_in": iso(r.marked_at), "check_out": iso(r.checked_out_at), "method": r.method,
                        "note": r.note} for r in recs],
    }
