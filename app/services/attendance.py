"""Attendance rules: status windows, duplicate prevention, closing sessions."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings as env
from app.models import (
    Course,
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
from app.terminology import terms
from app.vision.backends import get_backend
from app.vision.liveness import LIVE, SPOOF, LivenessChecker
from app.vision.pipeline import FaceTracker, RecognitionPipeline, is_accepted

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- rules
def status_for_arrival(session: ClassSession, when: datetime) -> AttendanceStatus:
    """09:00-09:10 -> Present, 09:10-09:20 -> Late, afterwards -> Absent (configurable per session)."""
    minutes = (when - session.start_time).total_seconds() / 60.0
    if minutes <= session.present_window_minutes:
        return AttendanceStatus.present
    if minutes <= session.late_window_minutes:
        return AttendanceStatus.late
    org = session.course.org if session.course is not None else None
    if org is not None and terms(org.kind).check_out:
        return AttendanceStatus.late  # offices / gyms: a very late arrival is still an arrival
    return AttendanceStatus.absent


def enrolled_student_ids(db: Session, course_id: int) -> set[int]:
    return set(db.scalars(select(Enrollment.student_id).where(Enrollment.course_id == course_id)).all())


@dataclass
class MarkResult:
    ok: bool
    duplicate: bool
    message: str
    record: Attendance | None = None
    kind: str = "check_in"      # check_in | check_out


def worked_minutes(rec: Attendance) -> int | None:
    if rec.marked_at is None or rec.checked_out_at is None:
        return None
    return max(0, int((rec.checked_out_at - rec.marked_at).total_seconds() // 60))


def format_duration(minutes: int | None) -> str:
    if minutes is None:
        return "--"
    return f"{minutes // 60}h {minutes % 60:02d}m"


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
        org = student.org
        if (org is not None and terms(org.kind).check_out and existing.marked_at is not None
                and existing.status in (AttendanceStatus.present, AttendanceStatus.late)):
            gap = float(app_settings.get_setting(db, org.id, "checkout_after_minutes"))
            if (when - existing.marked_at).total_seconds() >= gap * 60:
                # seen again later: check-out (the last sighting of the day counts)
                existing.checked_out_at = when
                student.last_seen_at = when
                db.commit()
                return MarkResult(True, True, f"{student.name}: checked out at {when:%H:%M} "
                                  f"(on site {format_duration(worked_minutes(existing))})", existing, "check_out")
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
    student.last_seen_at = when
    try:
        db.commit()
    except IntegrityError:  # the UNIQUE(student, session) constraint caught a race
        db.rollback()
        existing = db.scalar(
            select(Attendance).where(Attendance.student_id == student_id, Attendance.session_id == session.id)
        )
        return MarkResult(False, True, f"{student.name}: attendance already marked", existing)
    try:
        from app.services.proxy import check_impossible_presence  # local import avoids a cycle
        if check_impossible_presence(db, rec):
            db.commit()
    except Exception:  # noqa: BLE001 - a proxy check must never block marking
        log.exception("proxy check failed")
        db.rollback()
    verb = "checked in" if terms(student.org.kind).check_out and student.org else status.value.title()
    if status != AttendanceStatus.present and terms(student.org.kind).check_out:
        verb = f"checked in ({status.value})"
    return MarkResult(True, False, f"{student.name}: {verb} at {when:%H:%M}", rec)


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
    from app.services.messaging import queue_for_session  # local import avoids a cycle
    try:
        queue_for_session(db, session)
    except Exception:  # noqa: BLE001 - a messaging problem must never block closing attendance
        log.exception("could not queue parent messages")
    return len(missing)


# ------------------------------------------------------------------ scheduled groups & kiosk
def _parse_hhmm(value: str):
    return datetime.strptime(value, "%H:%M").time()


def scheduled_today(course: Course, day) -> bool:
    return bool(course.schedule_start) and str(day.weekday()) in (course.schedule_days or "").split(",")


def get_or_open_today(db: Session, course: Course, when: datetime) -> ClassSession:
    """Today's automatic session of a group (opened by the kiosk or the scheduler)."""
    day = when.date()
    sess = db.scalar(select(ClassSession).where(ClassSession.course_id == course.id, ClassSession.date == day,
                                                ClassSession.created_by.is_(None))
                     .order_by(ClassSession.start_time.desc()).limit(1))
    if sess is not None and sess.state != SessionState.closed:
        return sess
    cfg = app_settings.all_settings(db, course.org_id)
    if scheduled_today(course, day):
        start, minutes = datetime.combine(day, _parse_hhmm(course.schedule_start)), course.schedule_minutes
    else:  # unscheduled groups: open until midnight, arrival counts as on time
        start, minutes = when.replace(second=0), max(5, int((datetime.combine(day, datetime.max.time()) - when).total_seconds() // 60))
    sess = ClassSession(course_id=course.id, date=day, start_time=start, duration_minutes=minutes,
                        present_window_minutes=int(cfg["present_window_minutes"]),
                        late_window_minutes=int(cfg["late_window_minutes"]) if scheduled_today(course, day) else minutes,
                        liveness_required=bool(cfg["liveness_enabled"]), state=SessionState.active, created_by=None)
    db.add(sess)
    db.commit()
    return sess


def sync_scheduled_sessions(db: Session, org_id: int, when: datetime | None = None) -> None:
    """Open today's sessions of scheduled groups once they start (so absentees are recorded even if nobody
    checks in) and close automatic sessions whose time is over."""
    when = when or now()
    for course in db.scalars(select(Course).where(Course.org_id == org_id, Course.schedule_start.is_not(None))).all():
        if scheduled_today(course, when.date()) and when >= datetime.combine(when.date(), _parse_hhmm(course.schedule_start)):
            exists = db.scalar(select(ClassSession.id).where(ClassSession.course_id == course.id,
                                                             ClassSession.date == when.date(),
                                                             ClassSession.created_by.is_(None)))
            if not exists:
                get_or_open_today(db, course, when)
    due = db.scalars(select(ClassSession).join(Course).where(
        Course.org_id == org_id, ClassSession.state == SessionState.active, ClassSession.created_by.is_(None))).all()
    for sess in due:
        if sess.end_time <= when:
            close_session(db, sess)


def general_group(db: Session, org_id: int) -> Course:
    """Fallback group for people who are not in any group (e.g. gym members, visitors)."""
    c = db.scalar(select(Course).where(Course.org_id == org_id, Course.code == "GENERAL"))
    if c is None:
        c = Course(org_id=org_id, code="GENERAL", name="General check-in", department="", section="A")
        db.add(c)
        db.commit()
    return c


def kiosk_session_for(db: Session, student: Student, when: datetime) -> ClassSession:
    """Pick the right group session for a person seen at the kiosk."""
    courses = [e.course for e in student.enrollments]
    if not courses:
        course = general_group(db, student.org_id)
        db.add(Enrollment(student_id=student.id, course_id=course.id))
        db.commit()
        courses = [course]
    today = [c for c in courses if scheduled_today(c, when.date())]
    if today:  # the scheduled group whose start is closest to now
        course = min(today, key=lambda c: abs((datetime.combine(when.date(), _parse_hhmm(c.schedule_start)) - when).total_seconds()))
    else:
        course = sorted(courses, key=lambda c: (c.code != "GENERAL", c.id))[-1]
    return get_or_open_today(db, course, when)


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


def build_pipeline(db: Session, org_id: int) -> RecognitionPipeline:
    cfg = app_settings.all_settings(db, org_id)
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


def _recognize(db: Session, org_id: int, tracker_key: int, liveness_required: bool, frame: np.ndarray,
               t: float | None, session_for, audit_session_id: int | None, guests: bool = False):
    """Shared by the live session page and the kiosk: run the AI pipeline on one frame and apply the rules.

    ``session_for(student)`` returns the session a recognised person is marked in.
    """
    pipeline = build_pipeline(db, org_id)
    gallery = faces.get_gallery(db, org_id)
    tracker, lock = live_trackers.get(tracker_key)
    names = {}
    events = []
    with lock:
        results = pipeline.process(frame, gallery, tracker, liveness_required, t)
        out = []
        for r in results:
            track = r.track
            sid = r.student_id or r.candidate_id
            if sid and sid not in names:
                st = db.get(Student, sid)
                names[sid] = (st.name, st.student_code) if st and st.org_id == org_id else ("?", "?")

            if r.clash is not None:
                label, state = "Same person twice?", "spoof"
                track.outcome = "Two faces match the same person - possible proxy (photo or look-alike). Not accepted."
                if not track.marked:
                    from app.services.proxy import identity_clash
                    student = db.get(Student, r.clash)
                    if student is not None and student.org_id == org_id:
                        identity_clash(db, student, db.get(ClassSession, audit_session_id) if audit_session_id else None)
                    _log(db, track, "clash", audit_session_id, "clash", r.clash, r.similarity)
            elif r.liveness == SPOOF:
                label, state = "Spoof suspected", "spoof"
                track.outcome = "Rejected: liveness check failed" + (
                    f" - {track.liveness.reason}" if track.liveness.reason else "")
                _log(db, track, "spoof", audit_session_id, "spoof", sid, r.similarity, r.liveness_score)
            elif r.student_id is None:
                settled = (r.candidate_id is None and len(track.votes) == track.votes.maxlen
                           and all(v is None for v, _ in track.votes))
                if guests and settled and not track.guest and track.embedding is not None:
                    from app.services import visitors   # local import avoids a cycle
                    v = visitors.match(db, org_id, track.embedding)
                    track.guest = f"{v.id}:{v.name}" if v else "-"
                if guests and track.guest and track.guest != "-":
                    vid, gname = track.guest.split(":", 1)
                    label = f"{gname} (guest)"
                    if r.liveness not in (LIVE, "disabled"):
                        state, track.outcome = "checking", "Liveness check: please look at the camera"
                    elif not track.marked:
                        from app.models import Visitor
                        from app.services import visitors
                        vis = db.get(Visitor, int(vid))
                        if vis is not None and vis.embedding is not None:
                            track.final_state, track.outcome = visitors.seen(db, org_id, vis)
                        else:
                            track.final_state, track.outcome = "rejected", "Visitor pass has ended"
                        track.marked = True
                        events.append({"name": label, "state": track.final_state, "message": track.outcome})
                        state = track.final_state
                    else:
                        state = track.final_state
                elif settled:
                    label, state = "Unknown", "unknown"
                    track.outcome = "Face not registered"
                    if audit_session_id and "unknown" not in track.logged:
                        exam_sess = db.get(ClassSession, audit_session_id)
                        if exam_sess is not None and exam_sess.is_exam:
                            from app.services import exam
                            exam.unregistered_face(db, exam_sess)
                            track.outcome = "Not registered - identity check needed (invigilator alerted)"
                    _log(db, track, "unknown", audit_session_id, "unknown", None, r.similarity)
                else:
                    label, state = "Identifying…", "checking"
            elif not is_accepted(r):
                label, state = names[r.student_id][0], "checking"
                track.outcome = ("Liveness check: turn your head left, hold, then right"
                                 if "motion" in pipeline.liveness.mode else "Liveness check: please look at the camera")
            else:
                label = names[r.student_id][0]
                if not track.marked:
                    student = db.get(Student, r.student_id)
                    session = session_for(student)
                    res = mark_attendance(db, session, r.student_id, similarity=r.confirmed_similarity,
                                          liveness_score=r.liveness_score)
                    track.marked = True
                    track.outcome = res.message
                    if res.ok and res.kind == "check_out":
                        _log(db, track, "out", session.id, "check_out", r.student_id, r.confirmed_similarity, r.liveness_score)
                        track.final_state = "checked_out"
                    elif res.ok:
                        _log(db, track, "marked", session.id, "marked", r.student_id, r.confirmed_similarity, r.liveness_score)
                        track.final_state = "marked"
                        if r.liveness == LIVE and track.embedding is not None:   # never learn from unverified faces
                            try:
                                faces.learn_from_sighting(db, student, track.embedding, r.confirmed_similarity)
                            except Exception:  # noqa: BLE001 - learning is optional, marking is not
                                log.exception("adaptive template not stored")
                    elif res.duplicate:
                        _log(db, track, "dup", session.id, "duplicate", r.student_id, r.confirmed_similarity)
                        track.final_state = "duplicate"
                    else:
                        track.final_state = "rejected"
                        if session.is_exam and "not enrolled" in res.message:
                            from app.services import exam
                            exam.not_a_candidate(db, session, student)
                            track.outcome = f"{student.name} is NOT a candidate of this exam - invigilator alerted"
                    events.append({"name": label, "state": track.final_state, "message": track.outcome})
                state = track.final_state

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
    return out, events, (pipeline.liveness.mode if liveness_required else "off")


def process_frame(db: Session, session: ClassSession, frame: np.ndarray, t: float | None = None) -> dict:
    """Live page of one session: run the AI pipeline on one camera frame and apply attendance rules."""
    if session.state != SessionState.active:
        return {"error": "Session is not active", "faces": []}
    out, _events, mode = _recognize(db, session.course.org_id, session.id, session.liveness_required, frame, t,
                                    lambda _student: session, session.id)
    return {"faces": out, "summary": session_summary(db, session), "liveness_mode": mode}


def process_kiosk_frame(db: Session, org_id: int, frame: np.ndarray, t: float | None = None) -> dict:
    """Entrance kiosk: no session is started by hand - each person is checked in / out of the right group."""
    sync_scheduled_sessions(db, org_id)
    liveness = bool(app_settings.get_setting(db, org_id, "liveness_enabled"))
    when = now()
    out, events, mode = _recognize(db, org_id, -org_id, liveness, frame, t,
                                   lambda student: kiosk_session_for(db, student, when), None, guests=True)
    return {"faces": out, "events": events, "liveness_mode": mode}


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
