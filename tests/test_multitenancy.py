"""Multi-tenant isolation: organizations must never see or match each other's data."""
import sqlite3
from datetime import date, datetime

from fastapi.testclient import TestClient

from app import database
from app.models import Attendance, Organization, OrgSetting, Student, User
from tests.conftest import data_url, face_image, login, noisy


def signup(client, org_name, email, kind="school"):
    r = client.post("/signup", data={"org_name": org_name, "kind": kind, "full_name": "Owner " + org_name,
                                     "email": email, "password": "password123", "terms": "on"}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return client


def build_org(client, code_prefix, face_seed):
    """One course, one student with a registered face, one running session."""
    client.post("/courses/new", data={"code": "CS-401", "name": "AI", "semester": 7, "section": "A"})
    r = client.post("/students/new", data={"student_code": f"{code_prefix}-001", "name": f"{code_prefix} Student",
                                           "semester": 7, "section": "A", "department": "Computer Science",
                                           "consent": "on", "auto_enroll": "on"}, follow_redirects=False)
    sid = int(r.headers["location"].split("/")[2])
    imgs = [data_url(noisy(face_image(face_seed), k)) for k in range(3)]
    assert client.post(f"/api/students/{sid}/faces", json={"images": imgs}).json()["accepted"] == 3
    r = client.post("/sessions/new", data={"course_id": _first_course_id(client), "day": date.today().isoformat(),
                                           "start": datetime.now().strftime("%H:%M"), "duration": 60,
                                           "present_window": 10, "late_window": 20, "start_now": "1"},
                    follow_redirects=False)
    sess_id = int(r.headers["location"].split("/")[2])
    return sid, sess_id


def _first_course_id(client):
    import re
    html = client.get("/courses").text
    return int(re.search(r'href="/courses/(\d+)"', html).group(1))


def test_signup_and_full_isolation(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "public_signup", True)
    from app.main import app

    a = signup(client, "Alpha College", "alpha@example.edu")
    a_student, a_session = build_org(a, "ALPHA", face_seed=1)

    with TestClient(app) as b:
        signup(b, "Beta Corp", "beta@example.com", kind="office")
        b_student, b_session = build_org(b, "BETA", face_seed=2)

        # same codes are allowed in different organizations
        assert b.post("/courses/new", data={"code": "CS-999", "name": "X"}).status_code in (200, 303)

        # B cannot open or change anything of A
        for url in [f"/students/{a_student}", f"/students/{a_student}/edit", f"/sessions/{a_session}",
                    f"/sessions/{a_session}/live", f"/reports/session/{a_session}", f"/api/sessions/{a_session}/summary"]:
            assert b.get(url).status_code in (403, 404), url
        assert b.post(f"/students/{a_student}/delete").status_code in (403, 404)
        assert b.post(f"/api/students/{a_student}/faces", json={"images": []}).status_code in (403, 404)
        assert b.post(f"/api/sessions/{a_session}/frame", json={"image": data_url(face_image(1))}).status_code in (403, 404)
        assert b.post(f"/courses/{_first_course_id(a)}/enroll", data={"student_id": b_student}).status_code in (403, 404)

        # lists only show own data
        assert "ALPHA-001" not in b.get("/students").text and "BETA-001" in b.get("/students").text
        assert "ALPHA-001" in a.get("/students").text and "BETA-001" not in a.get("/students").text
        assert "Alpha College" not in b.get("/").text

        # A's face shown in B's session is UNKNOWN (galleries are per organization)
        states = [b.post(f"/api/sessions/{b_session}/frame", json={"image": data_url(noisy(face_image(1), 50 + k))}).json()
                  for k in range(6)]
        assert states[-1]["faces"][0]["state"] == "unknown"
        # while B's own person is recognised
        own = [b.post(f"/api/sessions/{b_session}/frame", json={"image": data_url(noisy(face_image(2), 70 + k))}).json()
               for k in range(4)]
        assert own[-1]["faces"][0]["label"] == "BETA Student"

        # settings are per organization
        b.post("/settings", data={"match_threshold": "0.6", "liveness_mode": "motion"})
        with database.SessionLocal() as db:
            org_b = db.query(Organization).filter_by(name="Beta Corp").one()
            org_a = db.query(Organization).filter_by(name="Alpha College").one()
            assert db.query(OrgSetting).filter_by(org_id=org_b.id, key="match_threshold").one().value == "0.6"
            assert db.query(OrgSetting).filter(OrgSetting.org_id == org_a.id, OrgSetting.key != "biometric_key").count() == 0
            assert org_a.plan == "trial" and org_a.trial_ends_at is not None
            assert db.query(Attendance).join(Student).filter(Student.org_id == org_a.id).count() == 0

        # platform console is for the operator only
        assert b.get("/platform").status_code == 403


def test_signup_validation_and_login_by_email(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "public_signup", True)
    r = client.post("/signup", data={"org_name": "X", "kind": "school", "full_name": "Y", "email": "a@b.c",
                                     "password": "short", "terms": "on"})
    assert r.status_code == 400 and "at least 8" in r.text
    signup(client, "Gamma Gym", "Owner@Gamma.io", kind="event")
    client.get("/logout")
    assert client.post("/login", data={"username": "owner@gamma.io", "password": "password123"},
                       follow_redirects=False).status_code == 303
    r = client.post("/signup", data={"org_name": "Other", "kind": "school", "full_name": "Z", "email": "owner@gamma.io",
                                     "password": "password123", "terms": "on"})
    assert r.status_code == 400 and "already exists" in r.text


def test_signup_disabled_for_self_hosted(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "public_signup", False)
    assert client.get("/signup", follow_redirects=False).headers["location"] == "/login"


def test_operator_console(client):
    login(client)  # the bootstrap admin is the platform operator
    r = client.get("/platform")
    assert r.status_code == 200 and "Platform console" in r.text


def test_upgrade_of_single_school_database(tmp_path, monkeypatch):
    """A database created by the first (single-school) release is migrated automatically."""
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE students (id INTEGER PRIMARY KEY, student_code VARCHAR(40) UNIQUE, roll_number VARCHAR(40),
            name VARCHAR(120), department VARCHAR(120), semester INTEGER, section VARCHAR(10), email VARCHAR(120),
            phone VARCHAR(40), consent_given BOOLEAN, is_active BOOLEAN, registration_date DATETIME);
        CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(64) UNIQUE, password_hash VARCHAR(255),
            full_name VARCHAR(120), email VARCHAR(120), role VARCHAR(7), student_id INTEGER, is_active BOOLEAN,
            created_at DATETIME);
        CREATE TABLE courses (id INTEGER PRIMARY KEY, code VARCHAR(20) UNIQUE, name VARCHAR(120), department VARCHAR(120),
            semester INTEGER, section VARCHAR(10), teacher_id INTEGER);
        CREATE TABLE attendance (id INTEGER PRIMARY KEY, student_id INTEGER, session_id INTEGER, course_id INTEGER,
            date DATE, marked_at DATETIME, status VARCHAR(7), confidence FLOAT, liveness_score FLOAT, method VARCHAR(20),
            note VARCHAR(255));
        CREATE TABLE app_settings (key VARCHAR(60) PRIMARY KEY, value VARCHAR(255));
        INSERT INTO users VALUES (1, 'admin', 'x', 'Administrator', NULL, 'admin', NULL, 1, '2026-01-01');
        INSERT INTO students VALUES (1, 'BSCS-1', NULL, 'Old Student', 'CS', 7, 'A', NULL, NULL, 1, 1, '2026-01-01');
        INSERT INTO courses VALUES (1, 'CS-401', 'AI', 'CS', 7, 'A', 1);
        INSERT INTO app_settings VALUES ('match_threshold', '0.5');
    """)
    con.commit()
    con.close()
    database.configure(f"sqlite:///{path}")
    database.init_db()
    from app.tenancy import upgrade_database
    upgrade_database(database.engine)
    upgrade_database(database.engine)  # idempotent
    with database.SessionLocal() as db:
        org = db.query(Organization).one()
        assert db.get(Student, 1).org_id == org.id and db.get(User, 1).org_id == org.id
        assert db.get(User, 1).is_superadmin
        assert db.query(OrgSetting).filter_by(org_id=org.id, key="match_threshold").one().value == "0.5"
    database.engine.dispose()
