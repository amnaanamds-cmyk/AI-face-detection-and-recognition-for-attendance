"""Exam impersonation guard: non-candidates and strangers raise alerts, hand-marking needs a reason,
and the invigilation report is signed and verifiable."""
import re
from datetime import date, datetime

import pytest

from app import database
from app.models import ClassSession, Notification
from app.services import certificates, exam
from tests.conftest import data_url, face_image, login, noisy


@pytest.fixture(autouse=True)
def _key(tmp_path, monkeypatch):
    monkeypatch.setattr(certificates, "KEY_FILE", tmp_path / "signing.pem")


def person(client, code, name, seed, section):
    r = client.post("/students/new", data={"student_code": code, "name": name, "semester": 7, "section": section,
                                           "department": "Computer Science", "consent": "on", "auto_enroll": "on"},
                    follow_redirects=False)
    sid = int(r.headers["location"].split("/")[2])
    assert client.post(f"/api/students/{sid}/faces", json={"images": [data_url(noisy(face_image(seed), k)) for k in range(3)]}).json()["accepted"] == 3
    return sid


def stream(client, sess, seed, base, n=8):
    out = None
    for k in range(n):
        out = client.post(f"/api/sessions/{sess}/frame", json={"image": data_url(noisy(face_image(seed), base + k))}).json()
    return out


def test_exam_guard(client, monkeypatch):
    from app.vision.liveness import LIVE, LivenessChecker

    def live(self, state, *a, **k):                    # liveness itself is covered by tests/test_liveness.py
        state.decision, state.score = LIVE, 1.0
        return state
    monkeypatch.setattr(LivenessChecker, "update", live)
    login(client)
    client.post("/settings", data={"liveness_mode": "motion"})
    client.post("/courses/new", data={"code": "CS-401", "name": "AI", "semester": 7, "section": "A", "department": "Computer Science"})
    cand = person(client, "C-1", "Candidate One", 41, "A")
    absent = person(client, "C-2", "Candidate Two", 43, "A")
    person(client, "X-1", "Friend From B", 42, "B")                      # registered, not a candidate
    cid = int(re.search(r'href="/courses/(\d+)"', client.get("/courses").text).group(1))
    r = client.post("/sessions/new", data={"course_id": cid, "day": date.today().isoformat(), "start": datetime.now().strftime("%H:%M"),
                                           "duration": 120, "present_window": 15, "late_window": 30, "exam": "on",
                                           "start_now": "1"}, follow_redirects=False)
    sess = int(r.headers["location"].split("/")[2])
    with database.SessionLocal() as db:
        s = db.get(ClassSession, sess)
        assert s.is_exam and s.liveness_required                         # liveness forced on

    last = stream(client, sess, 42, 300)                                 # the friend sits the exam
    assert "NOT a candidate" in last["faces"][0]["message"]
    stream(client, sess, 99, 400)                                        # a stranger
    stream(client, sess, 41, 500)                                        # the real candidate
    with database.SessionLocal() as db:
        alerts = [n.message for n in db.query(Notification).filter_by(level="exam")]
    assert any("impersonation suspected - Friend From B (X-1)" in a for a in alerts)
    assert any("unregistered person" in a for a in alerts)

    # hand-marking a candidate present needs a reason
    client.post(f"/sessions/{sess}/override", data={"student_id": absent, "status": "present", "note": ""})
    with database.SessionLocal() as db:
        assert not db.get(ClassSession, sess).attendance or all(a.student_id != absent for a in db.get(ClassSession, sess).attendance)
    client.post(f"/sessions/{sess}/override", data={"student_id": absent, "status": "present", "note": "ID card checked"})
    page = client.get(f"/sessions/{sess}").text
    assert "Exam alerts" in page and "Invigilation report" in page

    with database.SessionLocal() as db:
        s = db.get(ClassSession, sess)
        rows = {r["code"]: r for r in exam.rows(db, s)}
        data = exam.report_data(db, s)
    assert rows["C-1"]["verified"] and rows["C-1"]["how"].startswith("face")
    assert rows["C-2"]["how"] == "BY HAND by admin: ID card checked"
    assert data["candidates"] == 2 and data["face_verified"] == 1 and data["by_hand"] == 1 and data["alerts"] >= 2
    pdf = client.get(f"/sessions/{sess}/exam-report")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    token = certificates.sign(data)
    client.cookies.clear()
    assert "Exam invigilation report" in client.get("/verify", params={"c": token}).text
