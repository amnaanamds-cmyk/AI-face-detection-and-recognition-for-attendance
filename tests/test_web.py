from datetime import date, datetime

from app import database
from app.services import attendance as att
from app.models import Attendance, RecognitionEvent, Student
from tests.conftest import data_url, face_image, login, noisy


def setup_class(client, liveness=False):
    login(client)
    client.post("/users/new", data={"username": "drahmad", "full_name": "Dr. Ahmad", "password": "teacher123",
                                    "role": "teacher"})
    client.post("/courses/new", data={"code": "CS-401", "name": "Artificial Intelligence", "semester": 7,
                                      "section": "A", "teacher_id": 2})
    ids = []
    for i, name in enumerate(["Amina Bibi", "Ali Khan", "Sara Ahmed"], start=1):
        r = client.post("/students/new", data={"student_code": f"BSCS-2023-00{i}", "name": name, "semester": 7,
                                               "section": "A", "department": "Computer Science", "consent": "on",
                                               "auto_enroll": "on", "create_account": "on", "password": "student123"},
                        follow_redirects=False)
        sid = int(r.headers["location"].split("/")[2])
        ids.append(sid)
    now = datetime.now()
    r = client.post("/sessions/new", data={"course_id": 1, "day": date.today().isoformat(), "start": now.strftime("%H:%M"),
                                           "duration": 60, "present_window": 10, "late_window": 20,
                                           "liveness": "on" if liveness else "", "start_now": "1"},
                    follow_redirects=False)
    assert r.headers["location"] == "/sessions/1/live"
    return ids


def test_login_required_and_roles(client):
    assert client.get("/", follow_redirects=False).headers["location"].startswith("/login")
    assert client.post("/api/sessions/1/frame", json={}).status_code == 401
    assert client.post("/login", data={"username": "admin", "password": "nope"}).status_code == 401
    setup_class(client)
    client.get("/logout")
    login(client, "drahmad", "teacher123")
    assert client.get("/users").status_code == 403
    assert client.get("/sessions/1").status_code == 200       # own course
    client.get("/logout")
    login(client, "BSCS-2023-001", "student123")
    assert client.get("/", follow_redirects=False).headers["location"] == "/me"
    assert client.get("/me").status_code == 200
    assert client.get("/students").status_code == 403


def test_end_to_end_face_attendance(client):
    ids = setup_class(client)
    # consent is required before biometric enrollment
    client.post(f"/students/{ids[2]}/edit", data={"student_code": "BSCS-2023-003", "name": "Sara Ahmed",
                                                  "semester": 7, "section": "A", "is_active": "on"})
    assert client.post(f"/api/students/{ids[2]}/faces", json={"images": [data_url(face_image(3))]}).status_code == 400

    for n, sid in enumerate(ids[:2], start=1):
        imgs = [data_url(noisy(face_image(n), k)) for k in range(4)]
        r = client.post(f"/api/students/{sid}/faces", json={"images": imgs})
        assert r.status_code == 200 and r.json()["accepted"] == 4

    def frame(img):
        r = client.post("/api/sessions/1/frame", json={"image": data_url(img)})
        assert r.status_code == 200, r.text
        return r.json()

    states = [frame(noisy(face_image(1), 100 + k))["faces"][0]["state"] for k in range(5)]
    assert states == ["checking", "checking", "marked", "marked", "marked"]  # 3 agreeing votes, then marked once
    att.live_trackers.pop(1)  # the student leaves and is seen again later as a new face track
    again = [frame(noisy(face_image(1), 200 + k))["faces"][0] for k in range(3)]
    assert again[-1]["state"] == "duplicate" and "already marked" in again[-1]["message"]

    unknown = [frame(noisy(face_image(99), k)) for k in range(6)]
    assert unknown[-1]["faces"][0]["state"] == "unknown"

    empty = frame(face_image(0) * 0)
    assert empty["faces"] == [] and empty["summary"]["attended"] == 1

    with database.SessionLocal() as db:
        recs = db.query(Attendance).all()
        assert [(r.student_id, r.status.value, r.method) for r in recs] == [(ids[0], "present", "face")]
        assert recs[0].confidence > 0.9
        events = {e.event for e in db.query(RecognitionEvent).all()}
        assert {"marked", "unknown"} <= events

    r = client.post("/sessions/1/close", follow_redirects=False)
    assert r.status_code == 303
    with database.SessionLocal() as db:
        assert db.query(Attendance).count() == 3
    assert client.post("/api/sessions/1/frame", json={"image": data_url(face_image(1))}).json()["error"]

    for fmt, magic in (("xlsx", b"PK"), ("pdf", b"%PDF"), ("csv", b"\xef\xbb\xbf")):
        assert client.get(f"/reports/session/1?fmt={fmt}").content.startswith(magic)
    assert "Amina Bibi" in client.get(f"/reports/daily?day={date.today().isoformat()}").text
    for page in ["/", "/analytics", "/sessions/1", "/students/1", "/courses/1", "/audit", "/api/analytics"]:
        assert client.get(page).status_code == 200, page


def test_liveness_blocks_static_photo(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "liveness_timeout_seconds", 0.0)
    ids = setup_class(client, liveness=True)
    client.post(f"/api/students/{ids[0]}/faces", json={"images": [data_url(noisy(face_image(1), k)) for k in range(3)]})
    photo = noisy(face_image(1), 7)  # the very same image shown again and again = a flat photo
    last = None
    for _ in range(8):
        last = client.post("/api/sessions/1/frame", json={"image": data_url(photo)}).json()
    assert last["faces"][0]["state"] == "spoof"
    with database.SessionLocal() as db:
        assert db.query(Attendance).count() == 0
        assert db.query(RecognitionEvent).filter_by(event="spoof").count() == 1


def test_delete_student_removes_biometrics(client):
    ids = setup_class(client)
    client.post(f"/api/students/{ids[0]}/faces", json={"images": [data_url(noisy(face_image(1), k)) for k in range(3)]})
    client.post(f"/students/{ids[0]}/delete")
    with database.SessionLocal() as db:
        assert db.get(Student, ids[0]) is None
        from app.models import FaceEmbedding
        assert db.query(FaceEmbedding).count() == 0
