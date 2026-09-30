from datetime import date, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy.exc import IntegrityError

from app.models import (Attendance, AttendanceStatus, ClassSession, Course, Enrollment, FaceEmbedding, Notification,
                        SessionState, Student)
from app.security import decrypt_embedding, encrypt_embedding, hash_password, verify_password
from app.services import analytics, attendance as att, faces, reports
from tests.conftest import face_image, noisy

START = datetime(2026, 9, 28, 9, 0)


def make_course(db, n_students=3):
    course = Course(code="CS-401", name="Artificial Intelligence", semester=7, section="A")
    db.add(course)
    students = [Student(student_code=f"BSCS-2023-{i:03d}", name=f"Student {i}", semester=7, section="A",
                        consent_given=True) for i in range(1, n_students + 1)]
    db.add_all(students)
    db.flush()
    db.add_all([Enrollment(student_id=s.id, course_id=course.id) for s in students])
    sess = ClassSession(course_id=course.id, date=START.date(), start_time=START, state=SessionState.active,
                        present_window_minutes=10, late_window_minutes=20, liveness_required=False)
    db.add(sess)
    db.commit()
    return course, students, sess


@pytest.mark.parametrize("minutes,expected", [(0, "present"), (10, "present"), (11, "late"), (20, "late"), (25, "absent")])
def test_status_windows(db, minutes, expected):
    _, _, sess = make_course(db)
    assert att.status_for_arrival(sess, START + timedelta(minutes=minutes)).value == expected


def test_duplicate_prevention(db):
    _, (s1, *_), sess = make_course(db)
    first = att.mark_attendance(db, sess, s1.id, similarity=0.8, when=START + timedelta(minutes=3))
    assert first.ok and first.record.status == AttendanceStatus.present
    for _ in range(4):  # recognised four more times during the same class
        again = att.mark_attendance(db, sess, s1.id, when=START + timedelta(minutes=30))
        assert not again.ok and again.duplicate and "already marked" in again.message
    assert db.query(Attendance).filter_by(student_id=s1.id).count() == 1


def test_database_unique_constraint(db):
    _, (s1, *_), sess = make_course(db)
    kw = dict(student_id=s1.id, session_id=sess.id, course_id=sess.course_id, date=sess.date, status=AttendanceStatus.present)
    db.add(Attendance(**kw))
    db.commit()
    db.add(Attendance(**kw))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_not_enrolled_and_closed(db):
    course, students, sess = make_course(db)
    outsider = Student(student_code="X-1", name="Outsider")
    db.add(outsider)
    db.commit()
    assert "not enrolled" in att.mark_attendance(db, sess, outsider.id).message
    att.mark_attendance(db, sess, students[0].id, when=START + timedelta(minutes=12))
    n = att.close_session(db, sess)
    assert n == 2 and sess.state == SessionState.closed
    statuses = {r.student_id: r.status for r in sess.attendance}
    assert statuses[students[0].id] == AttendanceStatus.late
    assert statuses[students[1].id] == AttendanceStatus.absent
    assert not att.mark_attendance(db, sess, students[1].id).ok  # closed


def test_manual_override(db):
    _, (s1, *_), sess = make_course(db)
    rec = att.set_status(db, sess, s1.id, AttendanceStatus.excused, "medical")
    assert rec.method == "manual" and rec.status == AttendanceStatus.excused


def test_rate_excludes_excused_and_leave():
    S = AttendanceStatus
    assert analytics.rate([S.present, S.late, S.absent, S.excused, S.leave]) == pytest.approx(66.7)
    assert analytics.rate([S.excused]) is None


def _history(db, course, students, days=5):
    """Student 1 always present, student 2 absent 3/5, student 3 always absent."""
    for d in range(days):
        day = date(2026, 9, 1) + timedelta(days=d)
        s = ClassSession(course_id=course.id, date=day, start_time=datetime.combine(day, START.time()),
                         state=SessionState.active)
        db.add(s)
        db.commit()
        att.mark_attendance(db, s, students[0].id, when=s.start_time)
        if d >= 3:
            att.mark_attendance(db, s, students[1].id, when=s.start_time + timedelta(minutes=15))
        att.close_session(db, s)


def test_analytics_and_low_attendance_alerts(db):
    course, students, sess = make_course(db)
    db.delete(sess)
    db.commit()
    _history(db, course, students)
    cs = analytics.course_summary(db, course.id)
    rates = {r["student"].id: r["rate"] for r in cs["rows"]}
    assert rates == {students[0].id: 100.0, students[1].id: 40.0, students[2].id: 0.0}
    alerts = db.query(Notification).all()
    assert {n.student_id for n in alerts} == {students[1].id, students[2].id}
    assert len(alerts) == 2  # not repeated after every session
    ranking = analytics.student_ranking(db)
    assert ranking["lowest"][0]["student"].id == students[2].id
    assert analytics.monthly_trend(db)[0]["month"] == "2026-09"
    assert sum(x["absences"] for x in analytics.absence_by_weekday(db)) == 8


def test_reports_export(db):
    course, students, sess = make_course(db)
    db.delete(sess)
    db.commit()
    _history(db, course, students)
    rep = reports.monthly_report(db, 2026, 9)
    assert rep.rows[0][:2] == ["BSCS-2023-001", "Student 1"] and rep.rows[0][-1] == "100.0%"
    daily = reports.daily_report(db, date(2026, 9, 1))
    assert len(daily.rows) == 3
    assert reports.to_csv(rep).decode("utf-8-sig").splitlines()[3].startswith("Student ID")
    assert reports.to_xlsx(rep)[:2] == b"PK"
    assert reports.to_pdf(rep)[:4] == b"%PDF"


def test_security_helpers():
    h = hash_password("s3cret-pass")
    assert verify_password("s3cret-pass", h) and not verify_password("wrong", h)
    v = np.random.rand(128).astype(np.float32)
    blob = encrypt_embedding(v)
    assert v.tobytes() not in blob
    assert np.array_equal(decrypt_embedding(blob), v)


def test_enrollment_stores_only_encrypted_embeddings(db):
    _, (s1, s2, _), _ = make_course(db)
    rep = faces.enroll_images(db, s1, [noisy(face_image(1), i) for i in range(3)] + [np.zeros((50, 50, 3), np.uint8)])
    assert rep.accepted == 3 and len(rep.rejected) == 1
    row = db.query(FaceEmbedding).first()
    assert np.linalg.norm(decrypt_embedding(row.embedding)) == pytest.approx(1.0, abs=1e-5)
    # images of two different people in one enrollment are refused
    bad = faces.enroll_images(db, s2, [face_image(2), face_image(3), face_image(4)])
    assert bad.accepted == 0 and faces.count_templates(db, s2.id) == 0
    g = faces.load_gallery(db)
    assert len(g) == 3 and set(g.labels.tolist()) == {s1.id}


def test_alert_resolved_when_attendance_recovers(db):
    course, students, sess = make_course(db, n_students=1)
    db.delete(sess)
    db.commit()
    st = students[0]
    days = [date(2026, 9, 1) + timedelta(days=d) for d in range(9)]
    for i, day in enumerate(days):
        s = ClassSession(course_id=course.id, date=day, start_time=datetime.combine(day, START.time()),
                         state=SessionState.active)
        db.add(s)
        db.commit()
        if i >= 3:  # absent for the first 3 classes, then always present
            att.mark_attendance(db, s, st.id, when=s.start_time)
        att.close_session(db, s)
        if i == 2:
            assert db.query(Notification).filter_by(student_id=st.id, is_read=False).count() == 1
    # 6/9 = 66.7 % -> still below 75 %; one more class would not yet recover it
    assert db.query(Notification).filter_by(student_id=st.id, is_read=False).count() == 1
    for d in range(9, 12):
        day = date(2026, 9, 1) + timedelta(days=d)
        s = ClassSession(course_id=course.id, date=day, start_time=datetime.combine(day, START.time()),
                         state=SessionState.active)
        db.add(s)
        db.commit()
        att.mark_attendance(db, s, st.id, when=s.start_time)
        att.close_session(db, s)
    # 9/12 = 75 % -> recovered, the old warning is resolved
    assert db.query(Notification).filter_by(student_id=st.id, is_read=False).count() == 0
