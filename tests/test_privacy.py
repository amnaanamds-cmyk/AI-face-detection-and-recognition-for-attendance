"""Compliance: retention, data export, legal pages."""
import json
from datetime import date, datetime, timedelta

from app import database
from app.models import Attendance, AttendanceStatus, ClassSession, Course, FaceEmbedding, OrgSetting, SessionState, Student
from app.security import encrypt_embedding
from app.services.privacy import apply_retention
from app.tenancy import create_org
from tests.conftest import login

import numpy as np


def test_retention_deletes_old_templates_and_records(db):
    org = create_org(db, "Ret School")
    t0 = datetime(2026, 1, 1)
    recent = Student(org_id=org.id, student_code="R1", name="Recent", last_seen_at=t0 - timedelta(days=10), registration_date=t0 - timedelta(days=900))
    stale = Student(org_id=org.id, student_code="R2", name="Stale", last_seen_at=t0 - timedelta(days=400), registration_date=t0 - timedelta(days=900))
    never = Student(org_id=org.id, student_code="R3", name="Never seen", registration_date=t0 - timedelta(days=500))
    gone = Student(org_id=org.id, student_code="R4", name="Left", is_active=False, last_seen_at=t0, registration_date=t0)
    db.add_all([recent, stale, never, gone])
    db.flush()
    for p in (recent, stale, never, gone):
        db.add(FaceEmbedding(student_id=p.id, embedding=encrypt_embedding(np.ones(4)), model_name="m"))
    c = Course(org_id=org.id, code="C", name="C")
    db.add(c)
    db.flush()
    s_old = ClassSession(course_id=c.id, date=date(2023, 1, 1), start_time=datetime(2023, 1, 1, 9), state=SessionState.closed)
    s_new = ClassSession(course_id=c.id, date=date(2025, 12, 1), start_time=datetime(2025, 12, 1, 9), state=SessionState.closed)
    db.add_all([s_old, s_new])
    db.flush()
    for s in (s_old, s_new):
        db.add(Attendance(student_id=recent.id, session_id=s.id, course_id=c.id, date=s.date, status=AttendanceStatus.present))
    db.add(OrgSetting(org_id=org.id, key="attendance_retention_days", value="730"))
    db.commit()
    out = apply_retention(db, org.id, when=t0)
    assert out == {"templates": 3, "records": 1}
    left = {e.student.student_code for e in db.query(FaceEmbedding).all()}
    assert left == {"R1"}
    assert [a.date for a in db.query(Attendance).all()] == [date(2025, 12, 1)]


def test_export_and_legal_pages(client):
    login(client)
    client.post("/courses/new", data={"code": "C1", "name": "C"})
    r = client.post("/students/new", data={"student_code": "E1", "name": "Export Me", "consent": "on", "auto_enroll": "on",
                                           "create_account": "on", "password": "student123"}, follow_redirects=False)
    sid = int(r.headers["location"].split("/")[2])
    data = json.loads(client.get(f"/students/{sid}/export").content)
    assert data["person"]["name"] == "Export Me" and data["biometric_consent"]["given"] is True
    assert data["biometric_consent"]["recorded_at"] is not None and "embedding" not in json.dumps(data["face_templates"])
    client.get("/logout")
    login(client, "E1", "student123")
    mine = json.loads(client.get("/me/export").content)
    assert mine["person"]["id"] == "E1"
    for page in ("privacy", "terms", "dpa", "consent-form"):
        r = client.get(f"/legal/{page}")
        assert r.status_code == 200 and "not legal advice" in r.text
    assert client.get("/legal/unknown").status_code == 404
