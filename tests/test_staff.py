"""Teachers check in by face at the kiosk; the principal sees their own attendance."""
from datetime import date, datetime, timedelta

from app import database
from app.models import Attendance, AttendanceStatus, ClassSession, Course, Holiday, Student, User
from app.services import attendance as att
from app.services import overview as ov
from app.services import staff as staff_service
from app.services.billing import people_count
from tests.conftest import data_url, face_image, login, noisy


def test_teacher_checks_in_at_kiosk(client):
    login(client)
    client.post("/settings", data={"liveness_mode": "motion"})
    client.post("/settings", data={})                                  # liveness switch off (unchecked box)
    client.post("/users/new", data={"username": "t.ayesha", "full_name": "Ayesha Khan", "password": "teacher123",
                                    "role": "teacher"})
    client.post("/users/new", data={"username": "t.imran", "full_name": "Imran Ali", "password": "teacher123",
                                    "role": "teacher"})
    # check-in time = now, every day, so the test does not depend on the clock
    start = (datetime.now() - timedelta(minutes=1)).strftime("%H:%M")
    client.post("/staff/schedule", data={"start": start, "minutes": 600, "days": [str(d) for d in range(7)]})
    with database.SessionLocal() as db:
        ayesha = db.query(User).filter_by(username="t.ayesha").one()
    r = client.post(f"/staff/{ayesha.id}/face", follow_redirects=False)
    sid = int(r.headers["location"].split("/")[2])
    assert r.headers["location"].endswith("/enroll")
    imgs = [data_url(noisy(face_image(7), k)) for k in range(3)]
    assert client.post(f"/api/students/{sid}/faces", json={"images": imgs}).json()["accepted"] == 3

    # staff are not students: not listed, not counted, not billed
    assert "Ayesha" not in client.get("/students").text
    with database.SessionLocal() as db:
        assert people_count(db, ayesha.org_id) == 0
    assert "Ayesha Khan" in client.get("/staff").text

    for k in range(6):                                                 # she looks at the kiosk
        client.post("/api/kiosk/frame", json={"image": data_url(noisy(face_image(7), 50 + k))})
    with database.SessionLocal() as db:
        rec = db.query(Attendance).one()
        assert rec.student.staff_user_id == ayesha.id and rec.status == AttendanceStatus.present
        assert rec.course.code == "STAFF"
        today = date.today()
        d = ov.overview(db, ayesha.org_id, today, today)
        row = next(x for x in d["teachers"] if x["user"].id == ayesha.id)
        assert d["has_staff"] and row["own"]["rate"] == 100.0 and row["own"]["today"] is not None
        assert all(x["course"].code != "STAFF" for x in d["subjects"])
    assert "Own attendance" in client.get("/overview").text
    client.get("/logout")
    login(client, "t.ayesha", "teacher123")
    assert "My attendance this month" in client.get("/").text


def test_staff_absent_when_not_checked_in_except_holidays(client):
    login(client)
    client.post("/users/new", data={"username": "t1", "full_name": "T One", "password": "teacher123", "role": "teacher"})
    db = database.SessionLocal()
    t1 = db.query(User).filter_by(username="t1").one()
    staff_service.ensure_person(db, t1)
    org_id = t1.org_id
    monday = datetime(2026, 10, 5, 8, 0)
    att.sync_scheduled_sessions(db, org_id, when=monday + timedelta(minutes=5))        # school day opens
    att.sync_scheduled_sessions(db, org_id, when=monday + timedelta(hours=8))          # and ends: T One never came
    rec = db.query(Attendance).one()
    assert rec.status == AttendanceStatus.absent and rec.student.staff_user_id == t1.id
    db.add(Holiday(org_id=org_id, start=date(2026, 10, 6), end=date(2026, 10, 6), name="Holiday"))
    db.commit()
    att.sync_scheduled_sessions(db, org_id, when=monday + timedelta(days=1, minutes=5))
    assert db.query(ClassSession).filter_by(date=date(2026, 10, 6)).count() == 0       # nothing on a holiday
    # a deactivated teacher is not expected any more
    client.post(f"/users/{t1.id}/toggle")
    db.expire_all()
    assert db.query(Student).filter_by(staff_user_id=t1.id).one().is_active is False
    db.close()
