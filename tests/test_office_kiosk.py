"""Universal product features: office / gym terminology, kiosk check-in & check-out, scheduled groups."""
from datetime import datetime, timedelta

from app import database
from app.models import Attendance, ClassSession, Course, Enrollment, SessionState, Student
from app.services import attendance as att
from app.tenancy import create_org
from tests.conftest import data_url, face_image, noisy


def office_signup(client, monkeypatch, kind="office"):
    from app.config import settings
    monkeypatch.setattr(settings, "public_signup", True)
    r = client.post("/signup", data={"org_name": "Acme Ltd", "kind": kind, "full_name": "Owner", "email": "owner@acme.io",
                                     "password": "password123", "terms": "on"}, follow_redirects=False)
    assert r.status_code == 303


def add_person(client, code, name, seed):
    r = client.post("/students/new", data={"student_code": code, "name": name, "department": "Sales", "consent": "on",
                                           "auto_enroll": "on", "semester": 1, "section": "A"}, follow_redirects=False)
    sid = int(r.headers["location"].split("/")[2])
    imgs = [data_url(noisy(face_image(seed), k)) for k in range(3)]
    assert client.post(f"/api/students/{sid}/faces", json={"images": imgs}).json()["accepted"] == 3
    return sid


def kiosk_visit(client, seed, base):
    last = None
    for k in range(5):
        last = client.post("/api/kiosk/frame", json={"image": data_url(noisy(face_image(seed), base + k))}).json()
    return last


def test_office_vocabulary_and_kiosk_check_in_out(client, monkeypatch):
    office_signup(client, monkeypatch)
    html = client.get("/students").text
    assert "Employees" in html and "Semester" not in html
    assert "Teams" in client.get("/courses").text
    client.post("/courses/new", data={"code": "SALES", "name": "Sales team", "department": "Sales",
                                      "schedule_start": "00:00", "schedule_days": [str(d) for d in range(7)],
                                      "schedule_minutes": 1439, "semester": 1, "section": "A"})
    add_person(client, "E-001", "Ali Khan", 1)
    client.post("/settings", data={"checkout_after_minutes": "0", "liveness_mode": "motion"})  # instant check-out in the test
    client.post("/settings", data={"checkout_after_minutes": "0"})

    first = kiosk_visit(client, 1, 100)
    ev = [e for e in client.post("/api/kiosk/frame", json={"image": data_url(noisy(face_image(1), 200))}).json()["events"]]
    assert first["faces"][0]["state"] in ("marked", "checking") or ev
    with database.SessionLocal() as db:
        rec = db.query(Attendance).one()
        assert rec.marked_at is not None and rec.checked_out_at is None
        sess = db.get(ClassSession, rec.session_id)
        assert sess.created_by is None and sess.course.code == "SALES"  # opened automatically by the kiosk

    org_id = rec.course.org_id if False else None
    with database.SessionLocal() as db:
        org_id = db.query(Course).filter_by(code="SALES").one().org_id
    att.live_trackers.pop(-org_id)  # the employee leaves; later a new visit starts a new face track
    second = kiosk_visit(client, 1, 300)
    assert second["faces"][0]["state"] == "checked_out"
    assert "checked out" in second["faces"][0]["message"]
    with database.SessionLocal() as db:
        rec = db.query(Attendance).one()
        assert rec.checked_out_at is not None and rec.checked_out_at >= rec.marked_at

    # reports show check-out and hours for offices
    report = client.get(f"/reports/session/{rec.session_id}").text
    assert "Check-out" in report and "Hours" in report
    assert "Hours on site" in client.get("/reports/monthly").text


def test_kiosk_general_group_for_people_without_group(client, monkeypatch):
    office_signup(client, monkeypatch, kind="event")
    assert "Members" in client.get("/students").text
    add_person(client, "M-1", "Sara", 4)
    client.post("/settings", data={"liveness_mode": "motion", "checkout_after_minutes": "30"})
    kiosk_visit(client, 4, 10)
    kiosk_visit(client, 4, 20)
    with database.SessionLocal() as db:
        rec = db.query(Attendance).one()
        assert rec.course.code == "GENERAL" and rec.status.value == "present"
        assert rec.checked_out_at is None  # second sighting within 30 minutes is not a check-out


def test_scheduled_sessions_open_and_close_with_absentees(db):
    org = create_org(db, "Shift Co", "office")
    course = Course(org_id=org.id, code="NIGHT", name="Night shift", schedule_start="09:00",
                    schedule_days="0,1,2,3,4,5,6", schedule_minutes=60)
    db.add(course)
    people = [Student(org_id=org.id, student_code=f"N{i}", name=f"N{i}", consent_given=True) for i in range(3)]
    db.add_all(people)
    db.flush()
    db.add_all(Enrollment(student_id=p.id, course_id=course.id) for p in people)
    db.commit()
    day = datetime(2026, 9, 30)
    att.sync_scheduled_sessions(db, org.id, day.replace(hour=8, minute=59))
    assert db.query(ClassSession).count() == 0  # not started yet
    att.sync_scheduled_sessions(db, org.id, day.replace(hour=9, minute=5))
    sess = db.query(ClassSession).one()
    assert sess.state == SessionState.active and sess.start_time == day.replace(hour=9)
    att.mark_attendance(db, sess, people[0].id, when=day.replace(hour=9, minute=5))
    att.mark_attendance(db, sess, people[1].id, when=day.replace(hour=9, minute=15))
    att.sync_scheduled_sessions(db, org.id, day.replace(hour=10, minute=1))
    db.refresh(sess)
    assert sess.state == SessionState.closed
    statuses = sorted(r.status.value for r in sess.attendance)
    assert statuses == ["absent", "late", "present"]
    # the next day a new session is opened again
    att.sync_scheduled_sessions(db, org.id, (day + timedelta(days=1)).replace(hour=9, minute=30))
    assert db.query(ClassSession).count() == 2


def test_check_out_only_for_offices(db):
    org = create_org(db, "School", "school")
    course = Course(org_id=org.id, code="C", name="C")
    st = Student(org_id=org.id, student_code="S", name="S", consent_given=True)
    db.add_all([course, st])
    db.flush()
    db.add(Enrollment(student_id=st.id, course_id=course.id))
    t0 = datetime(2026, 9, 30, 9, 0)
    sess = ClassSession(course_id=course.id, date=t0.date(), start_time=t0, state=SessionState.active)
    db.add(sess)
    db.commit()
    att.mark_attendance(db, sess, st.id, when=t0)
    res = att.mark_attendance(db, sess, st.id, when=t0 + timedelta(hours=2))
    assert res.duplicate and res.kind == "check_in" and res.record.checked_out_at is None
