"""Emergency muster: an evacuation roll call by face.

When the alarm goes, FaceAttend already knows who is in the building (checked in today and not
checked out, plus visitors on site). Starting a muster freezes that list; at the assembly point a
phone or tablet camera ticks people off as they walk past, staff can tick others by hand, and the
screen always shows who is still unaccounted for - the list rescue teams need first.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (Attendance, AttendanceStatus, Course, MusterEntry, MusterEvent, OutboxMessage, Student,
                        Visitor, now)
from app.services import app_settings, faces, visitors
from app.services.messaging import deliver_later, normalize_phone


def on_site(db: Session, org_id: int, when: datetime | None = None) -> list[Student]:
    """People checked in today and not checked out (for schools: everyone marked present or late today)."""
    day = (when or now()).date()
    q = (select(Student).join(Attendance, Attendance.student_id == Student.id).join(Course, Course.id == Attendance.course_id)
         .where(Course.org_id == org_id, Attendance.date == day, Attendance.marked_at.is_not(None),
                Attendance.status.in_((AttendanceStatus.present, AttendanceStatus.late)))
         .distinct())
    people = []
    for st in db.scalars(q).all():
        recs = db.scalars(select(Attendance).where(Attendance.student_id == st.id, Attendance.date == day,
                                                   Attendance.marked_at.is_not(None))).all()
        if any(r.checked_out_at is None for r in recs):      # still inside in at least one group
            people.append(st)
    return sorted(people, key=lambda s: s.name)


def active(db: Session, org_id: int) -> MusterEvent | None:
    return db.scalar(select(MusterEvent).where(MusterEvent.org_id == org_id, MusterEvent.ended_at.is_(None))
                     .order_by(MusterEvent.id.desc()).limit(1))


def start(db: Session, org_id: int, by: str, note: str = "") -> MusterEvent:
    ev = active(db, org_id)
    if ev:
        return ev
    ev = MusterEvent(org_id=org_id, started_by=by, note=note[:200] or None)
    db.add(ev)
    db.flush()
    for st in on_site(db, org_id):
        db.add(MusterEntry(event_id=ev.id, kind="person", ref_id=st.id, name=st.name, detail=st.student_code))
    for v in visitors.on_site(db, org_id):
        db.add(MusterEntry(event_id=ev.id, kind="visitor", ref_id=v.id, name=v.name,
                           detail=f"guest of {v.host}" if v.host else "guest"))
    db.commit()
    db.refresh(ev)
    return ev


def mark_safe(db: Session, ev: MusterEvent, kind: str, ref_id: int, method: str, by: str) -> MusterEntry | None:
    entry = db.scalar(select(MusterEntry).where(MusterEntry.event_id == ev.id, MusterEntry.kind == kind,
                                                MusterEntry.ref_id == ref_id))
    if entry is None:   # found at the assembly point although not on the list (e.g. never checked in)
        if kind == "person":
            st = db.get(Student, ref_id)
            if st is None or st.org_id != ev.org_id:
                return None
            name, detail = st.name, st.student_code
        else:
            v = db.get(Visitor, ref_id)
            if v is None or v.org_id != ev.org_id:
                return None
            name, detail = v.name, "guest"
        entry = MusterEntry(event_id=ev.id, kind=kind, ref_id=ref_id, name=name, detail=detail, expected=False)
        db.add(entry)
    if entry.safe_at is None:
        entry.safe_at, entry.method, entry.marked_by = now(), method, by
    db.commit()
    return entry


def scan(db: Session, ev: MusterEvent, frame: np.ndarray, by: str) -> list[dict]:
    """One camera frame at the assembly point. Liveness is off: in an emergency speed matters more."""
    from app.services.attendance import build_pipeline, live_trackers

    pipeline = build_pipeline(db, ev.org_id)
    gallery = faces.get_gallery(db, ev.org_id)
    tracker, lock = live_trackers.get(-(10 ** 6 + ev.id))
    out = []
    with lock:
        for r in pipeline.process(frame, gallery, tracker, liveness_required=False):
            label, state = "Identifying…", "checking"
            if r.student_id is not None:
                e = mark_safe(db, ev, "person", r.student_id, "face", by)
                label, state = (e.name, "safe") if e else ("?", "unknown")
            elif r.candidate_id is None and len(r.track.votes) == r.track.votes.maxlen and r.track.embedding is not None:
                if not r.track.guest:
                    v = visitors.match(db, ev.org_id, r.track.embedding)
                    r.track.guest = f"{v.id}:{v.name}" if v else "-"
                if r.track.guest != "-":
                    vid, gname = r.track.guest.split(":", 1)
                    mark_safe(db, ev, "visitor", int(vid), "face", by)
                    label, state = f"{gname} (guest)", "safe"
                else:
                    label, state = "Not registered", "unknown"
            out.append({"bbox": list(r.bbox), "label": label, "state": state})
    return out


def summary(ev: MusterEvent) -> dict:
    expected = [e for e in ev.entries if e.expected]
    missing = [e for e in expected if e.safe_at is None]
    end = ev.ended_at or now()
    return {
        "id": ev.id, "active": ev.ended_at is None, "started": ev.started_at.strftime("%H:%M:%S"),
        "minutes": round((end - ev.started_at).total_seconds() / 60, 1),
        "expected": len(expected), "safe": len(expected) - len(missing), "extra": sum(1 for e in ev.entries if not e.expected),
        "missing": [{"id": e.id, "name": e.name, "detail": e.detail, "kind": e.kind} for e in missing],
        "safe_list": [{"id": e.id, "name": e.name, "detail": e.detail, "how": e.method,
                       "at": e.safe_at.strftime("%H:%M:%S"), "expected": e.expected}
                      for e in sorted((e for e in ev.entries if e.safe_at), key=lambda e: e.safe_at, reverse=True)],
    }


def notify_missing(db: Session, ev: MusterEvent) -> int:
    """Message the emergency contacts (guardians) of people still unaccounted for."""
    from app.models import Organization

    org = db.get(Organization, ev.org_id)
    channel = str(app_settings.get_setting(db, ev.org_id, "parent_channel"))
    cc = str(app_settings.get_setting(db, ev.org_id, "country_code"))
    n = 0
    for e in ev.entries:
        if e.safe_at is not None or e.kind != "person":
            continue
        st = db.get(Student, e.ref_id)
        phone = normalize_phone(st.guardian_phone, cc) if st else None
        if not phone:
            continue
        db.add(OutboxMessage(org_id=ev.org_id, student_id=st.id, channel=channel, recipient=phone,
                             status="manual-pending" if channel == "manual" else "pending",
                             body=f"{org.name}: emergency roll call in progress. {st.name} has not been accounted for yet "
                                  f"at the assembly point. If you know where {st.name} is, please call us now."))
        n += 1
    db.commit()
    if n:
        deliver_later(ev.org_id)
    return n


def end(db: Session, ev: MusterEvent) -> None:
    from app.services.attendance import live_trackers

    ev.ended_at = now()
    db.commit()
    live_trackers.pop(-(10 ** 6 + ev.id), None)
