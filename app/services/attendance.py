"""Attendance rules: status windows, duplicate prevention, closing sessions."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings as env
from app.models import (
    Attendance,
    AttendanceStatus,
    ClassSession,
    Enrollment,
    RecognitionEvent,
    SessionState,
    Student,
    now,
)
from app.services import app_settings, faces
from app.services.notifications import check_low_attendance
from app.vision.backends import get_backend
from app.vision.liveness import SPOOF, LivenessChecker
from app.vision.pipeline import FaceTracker, RecognitionPipeline, is_accepted


# --------------------------------------------------------------------------- rules
def status_for_arrival(session: ClassSession, when: datetime) -> AttendanceStatus:
    """09:00-09:10 -> Present, 09:10-09:20 -> Late, afterwards -> Absent (configurable per session)."""
    minutes = (when - session.start_time).total_seconds() / 60.0
    if minutes <= session.present_window_minutes:
        return AttendanceStatus.present
    if minutes <= session.late_window_minutes:
        return AttendanceStatus.late
    return AttendanceStatus.absent


def enrolled_student_ids(db: Session, course_id: int) -> set[int]:
    return set(db.scalars(select(Enrollment.student_id).where(Enrollment.course_id == course_id)).all())


@dataclass
class MarkResult:
    ok: bool
    duplicate: bool
    message: str
    record: Attendance | None = None


def mark_attendance(
    db: Session,
    session: ClassSession,
    student_id: int,
    *,
    similarity: float | None = None,
    liveness_score: float | None = None,
    when: datetime | None = None,
    method: str = "face",
) -> MarkResult:
    when = when or now()
    student = db.get(Student, student_id)
    if student is None or not student.is_active:
        return MarkResult(False, False, "Unknown or inactive student")
    if session.state == SessionState.closed:
        return MarkResult(False, False, "Session is closed")
    if student_id not in enrolled_student_ids(db, session.course_id):
        return MarkResult(False, False, f"{student.name} is not enrolled in this course")

    existing = db.scalar(
        select(Attendance).where(Attendance.student_id == student_id, Attendance.session_id == session.id)
    )
    if existing:
        return MarkResult(False, True, f"{student.name}: attendance already marked", existing)

    status = status_for_arrival(session, when)
    rec = Attendance(
        student_id=student_id,
        session_id=session.id,
        course_id=session.course_id,
        date=session.date,
        marked_at=when,
        status=status,
        confidence=similarity,
        liveness_score=liveness_score,
        method=method,
        note="arrived after late cut-off" if status == AttendanceStatus.absent else None,
    )
    db.add(rec)
    try:
        db.commit()
    except IntegrityError:  # the UNIQUE(student, session) constraint caught a race
        db.rollback()
        existing = db.scalar(
            select(Attendance).where(Attendance.student_id == student_id, Attendance.session_id == session.id)
        )
        return MarkResult(False, True, f"{student.name}: attendance already marked", existing)
    return MarkResult(True, False, f"{student.name}: {status.value.title()} at {when:%H:%M}", rec)


def set_status(db: Session, session: ClassSession, student_id: int, status: AttendanceStatus, note: str | None = None) -> Attendance:
    """Manual override by a teacher/admin (e.g. Excused, Leave, correcting a miss)."""
    rec = db.scalar(select(Attendance).where(Attendance.student_id == student_id, Attendance.session_id == session.id))
    if rec is None:
        rec = Attendance(student_id=student_id, session_id=session.id, course_id=session.course_id, date=session.date)
        db.add(rec)
    rec.status = status
    rec.method = "manual"
    rec.marked_at = rec.marked_at or now()
    rec.note = note
    db.commit()
    return rec


def start_session(db: Session, session: ClassSession) -> None:
    session.state = SessionState.active
    db.commit()


def close_session(db: Session, session: ClassSession) -> int:
    """Mark every enrolled student without a record as Absent and close the session."""
    have = set(db.scalars(select(Attendance.student_id).where(Attendance.session_id == session.id)).all())
    missing = enrolled_student_ids(db, session.course_id) - have
    for sid in missing:
        db.add(Attendance(
            student_id=sid, session_id=session.id, course_id=session.course_id, date=session.date,
            status=AttendanceStatus.absent, method="auto-absent",
        ))
    session.state = SessionState.closed
    db.commit()
    live_trackers.pop(session.id, None)
    check_low_attendance(db, session.course_id)
    return len(missing)


# ------------------------------------------------------------------ live recognition
class _TrackerRegistry:
    """One face tracker per running session (in memory)."""

    def __init__(self):
        self._trackers: dict[int, FaceTracker] = {}
        self._locks: dict[int, threading.Lock] = {}
        self._guard = threading.Lock()

    def get(self, session_id: int) -> tuple[FaceTracker, threading.Lock]:
        with self._guard:
            if session_id not in self._trackers:
                self._trackers[session_id] = FaceTracker()
                self._locks[session_id] = threading.Lock()
            return self._trackers[session_id], self._locks[session_id]

    def pop(self, session_id: int, default=None):
        with self._guard:
            self._locks.pop(session_id, None)
            return self._trackers.pop(session_id, default)


live_trackers = _TrackerRegistry()


def resolve_liveness_mode(requested: str, backend) -> str:
    has_cnn = bool(getattr(backend, "has_antispoof", False))
    if requested == "auto":
        return "cnn" if has_cnn else "motion"
    if "cnn" in requested and not has_cnn:
        return "motion"
    return requested


def build_pipeline(db: Session) -> RecognitionPipeline:
    cfg = app_settings.all_settings(db)
    backend = get_backend()
    checker = LivenessChecker(
        min_frames=env.liveness_min_frames,
        motion_threshold=float(cfg["liveness_motion_threshold"]),
        timeout_seconds=env.liveness_timeout_seconds,
        min_sharpness=env.liveness_min_sharpness,
        mode=resolve_liveness_mode(str(cfg["liveness_mode"]), backend),
        cnn_threshold=float(cfg["antispoof_threshold"]),
    )
    return RecognitionPipeline(
        backend, checker, float(cfg["match_threshold"]), float(cfg["match_margin"]), int(cfg["votes_required"])
    )


def _log(db: Session, track, key: str, session_id: int, event: str, student_id=None, similarity=None, liveness=None):
    if key in track.logged:
        return
    track.logged.add(key)
    db.add(RecognitionEvent(session_id=session_id, student_id=student_id, event=event,
                            similarity=similarity, liveness_score=liveness))


def process_frame(db: Session, session: ClassSession, frame: np.ndarray, t: float | None = None) -> dict:
    """Run the AI pipeline on one camera frame and apply attendance rules."""
    if session.state != SessionState.active:
        return {"error": "Session is not active", "faces": []}

    pipeline = build_pipeline(db)
    gallery = faces.get_gallery(db)
    tracker, lock = live_trackers.get(session.id)
    names = {}
    with lock:
        results = pipeline.process(frame, gallery, tracker, session.liveness_required, t)
        out = []
        for r in results:
            track = r.track
            sid = r.student_id or r.candidate_id
            if sid and sid not in names:
                st = db.get(Student, sid)
                names[sid] = (st.name, st.student_code) if st else ("?", "?")

            if r.liveness == SPOOF:
                label, state = "Spoof suspected", "spoof"
                track.outcome = "Rejected: liveness check failed" + (
                    f" - {track.liveness.reason}" if track.liveness.reason else "")
                _log(db, track, "spoof", session.id, "spoof", sid, r.similarity, r.liveness_score)
            elif r.student_id is None:
                if r.candidate_id is None and len(track.votes) == track.votes.maxlen and all(v is None for v, _ in track.votes):
                    label, state = "Unknown", "unknown"
                    track.outcome = "Face not registered"
                    _log(db, track, "unknown", session.id, "unknown", None, r.similarity)
                else:
                    label, state = "Identifying…", "checking"
            elif not is_accepted(r):
                label, state = names[r.student_id][0], "checking"
                track.outcome = ("Liveness check: turn your head left, hold, then right"
                                 if "motion" in pipeline.liveness.mode else "Liveness check: please look at the camera")
            else:
                label, state = names[r.student_id][0], "accepted"
                if not track.marked:
                    res = mark_attendance(db, session, r.student_id, similarity=r.confirmed_similarity,
                                          liveness_score=r.liveness_score)
                    track.marked = True
                    track.outcome = res.message
                    if res.ok:
                        _log(db, track, "marked", session.id, "marked", r.student_id, r.confirmed_similarity, r.liveness_score)
                        state = "marked"
                    elif res.duplicate:
                        _log(db, track, "dup", session.id, "duplicate", r.student_id, r.confirmed_similarity)
                        state = "duplicate"
                    else:
                        state = "rejected"
                else:
                    state = "duplicate" if "already" in track.outcome else "marked"

            out.append({
                "track_id": r.track_id,
                "bbox": list(r.bbox),
                "label": label,
                "student_code": names.get(r.student_id, (None, None))[1] if r.student_id else None,
                "similarity": round(r.similarity, 3),
                "liveness": r.liveness,
                "liveness_score": round(r.liveness_score, 2),
                "state": state,
                "message": track.outcome,
            })
        db.commit()
    return {"faces": out, "summary": session_summary(db, session),
            "liveness_mode": pipeline.liveness.mode if session.liveness_required else "off"}


def session_summary(db: Session, session: ClassSession) -> dict:
    recs = db.scalars(select(Attendance).where(Attendance.session_id == session.id)).all()
    expected = len(enrolled_student_ids(db, session.course_id))
    counts = {s.value: 0 for s in AttendanceStatus}
    for r in recs:
        counts[r.status.value] += 1
    attended = counts["present"] + counts["late"]
    return {
        "expected": expected,
        "attended": attended,
        "not_yet": max(0, expected - len(recs)),
        "counts": counts,
        "rate": round(100.0 * attended / expected, 1) if expected else 0.0,
        "recent": [
            {"name": r.student.name, "code": r.student.student_code, "status": r.status.value,
             "time": r.marked_at.strftime("%H:%M:%S") if r.marked_at else "--"}
            for r in sorted(recs, key=lambda r: r.marked_at or datetime.min, reverse=True)[:10]
        ],
    }
