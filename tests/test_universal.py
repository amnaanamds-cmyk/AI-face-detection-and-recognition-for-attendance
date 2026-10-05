"""Universal product: any school or college signs up, sets itself up, pays locally and recovers passwords."""
import re
from datetime import timedelta

from fastapi.testclient import TestClient

from app import database
from app.models import Course, Enrollment, ManualPayment, Organization, User, now
from app.services.billing import subscription_problem
from tests.conftest import login

STAFF_CSV = ("Subject code,Subject,Class,Section,Teacher,Teacher username,Teacher email\n"
             "MATH-9A,Mathematics,Class 9,A,Ayesha Khan,,ayesha@school.pk\n"
             "ENG-9A,English,9,A,Imran Ali,,\n"
             "PHY-9A,Physics,IX,A,Ayesha Khan,,\n")
STUDENTS_CSV = "Admission No,Name,Class,Section\nGHS-1,Amina,9,A\nGHS-2,Bilal,9,A\nGHS-3,Sana,10,A\n"


def saas(monkeypatch, **extra):
    from app.config import settings

    monkeypatch.setattr(settings, "edition", "saas")
    monkeypatch.setattr(settings, "public_signup", True)
    monkeypatch.setattr(settings, "admin_password", "not-the-demo-password")   # the production start-up check
    monkeypatch.setattr(settings, "public_base_url", "https://attend.example.pk")
    for k, v in extra.items():
        monkeypatch.setattr(settings, k, v)


def signup(client, name, email, kind):
    r = client.post("/signup", data={"org_name": name, "kind": kind, "full_name": "Principal " + name, "email": email,
                                     "password": "password123", "terms": "on"}, follow_redirects=False)
    assert r.status_code == 303, r.text


def org(name):
    with database.SessionLocal() as db:
        return db.query(Organization).filter_by(name=name).one()


def test_school_and_college_words(client, monkeypatch):
    saas(monkeypatch)
    signup(client, "Govt High School", "head@ghs.pk", "school")
    o = org("Govt High School")
    assert (o.kind.value, o.institution) == ("school", "school")
    page = client.get("/").text
    assert "Subjects" in page and "Courses" not in page
    client.get("/logout")
    signup(client, "City College", "principal@cc.edu.pk", "college")
    assert org("City College").institution == "college"
    assert "Courses" in client.get("/").text
    # the type can be changed later
    client.post("/settings", data={"org_name": "City College", "institution": "school"})
    assert org("City College").institution == "school"


def test_setup_checklist_and_staff_import(client, monkeypatch):
    saas(monkeypatch)
    signup(client, "Model School", "head@model.pk", "school")
    page = client.get("/").text
    assert "Set up Model School" in page and "0 of 5 done" in page

    client.post("/students/import", files={"file": ("s.csv", STUDENTS_CSV.encode())}, data={"consent": "on", "auto_enroll": "on"})
    r = client.post("/setup/staff", files={"file": ("staff.csv", STAFF_CSV.encode())})
    assert r.status_code == 200
    logins = re.findall(r"<td>([A-Za-z ]+)</td><td><code>([a-z.0-9]+)</code></td><td><code>([a-z0-9]{8})</code></td>", r.text)
    assert [(n, u) for n, u, _ in logins] == [("Ayesha Khan", "ayesha.khan"), ("Imran Ali", "imran.ali")]
    assert "3</strong> subjects created" in r.text

    with database.SessionLocal() as db:
        o = db.query(Organization).filter_by(name="Model School").one()
        math = db.query(Course).filter_by(org_id=o.id, code="MATH-9A").one()
        phy = db.query(Course).filter_by(org_id=o.id, code="PHY-9A").one()
        assert math.semester == 9 and phy.semester == 9 and math.teacher_id == phy.teacher_id
        assert db.query(Enrollment).filter_by(course_id=math.id).count() == 2      # class 9-A only, not Sana
        assert db.query(User).filter_by(username="ayesha.khan").one().email == "ayesha@school.pk"

    page = client.get("/").text
    assert "2 of 5 done" in page                     # staff + subjects, students; faces and attendance to go
    # a new teacher can log in straight away and sees only their subjects
    client.get("/logout")
    name, user, password = logins[0]
    login(client, user, password)
    assert "Mathematics" in client.get("/courses").text and "English" not in client.get("/courses").text
    client.get("/logout")
    login(client, "head@model.pk", "password123")
    # re-importing reuses teachers, updates subjects
    r = client.post("/setup/staff", files={"file": ("staff.csv", STAFF_CSV.encode())})
    assert "0</strong> new teachers, 2 existing" in r.text and "3</strong> updated" in r.text
    client.post("/setup/dismiss")
    assert "Set up Model School" not in client.get("/").text


def test_teacher_usernames_unique_across_schools(client, monkeypatch):
    saas(monkeypatch)
    from app.main import app

    signup(client, "School One", "one@s.pk", "school")
    client.post("/setup/staff", files={"file": ("s.csv", STAFF_CSV.encode())})
    with TestClient(app) as other:
        signup(other, "School Two", "two@s.pk", "school")
        r = other.post("/setup/staff", files={"file": ("s.csv", STAFF_CSV.encode())})
        assert "<code>school-two.ayesha.khan</code>" in r.text


def test_local_payment_approved_by_operator(client, monkeypatch):
    saas(monkeypatch, local_payment_details="Bank: Test Bank | IBAN: PK00TEST0000 | JazzCash: 0300-0000000")
    from app.main import app

    signup(client, "Paying School", "pay@s.pk", "school")
    page = client.get("/billing").text
    assert "PKR 3,000" in page and "IBAN: PK00TEST0000" in page
    r = client.post("/billing/local", data={"plan": "pro", "months": 12, "method": "jazzcash", "reference": "TXN12345"},
                    follow_redirects=True)
    assert "PKR 90,000" in r.text                              # 12 months for the price of 10
    assert "already submitted" in client.post("/billing/local", data={"plan": "pro", "months": 1, "method": "bank",
                                                                         "reference": "TXN12345"}, follow_redirects=True).text
    assert org("Paying School").plan == "trial"

    with TestClient(app) as op:                                 # the operator confirms it
        login(op)                                       # the first administrator operates the service
        assert "TXN12345" in op.get("/platform").text
        with database.SessionLocal() as db:
            pid = db.query(ManualPayment).filter_by(reference="TXN12345").one().id
        op.post(f"/platform/payments/{pid}", data={"action": "approve"})
    o = org("Paying School")
    assert (o.plan, o.plan_status) == ("pro", "active")
    assert timedelta(days=359) < o.paid_until - now() <= timedelta(days=360)
    assert subscription_problem(o) is None
    o.paid_until = now() - timedelta(days=1)
    assert "paid period ended" in subscription_problem(o)


def test_forgot_and_reset_password(client, monkeypatch):
    from app.routers import auth
    from app.services import notifications

    sent = {}
    monkeypatch.setattr(notifications, "send_email", lambda to, subject, body: sent.update(to=to, body=body) or True)
    login(client)
    client.post("/users/new", data={"username": "t.sara", "full_name": "Sara", "password": "teacher123", "role": "teacher",
                                    "email": "sara@school.pk"})
    client.get("/logout")
    assert "If an account" in client.post("/forgot", data={"login": "nobody"}).text and not sent
    client.post("/forgot", data={"login": "t.sara"})
    token = re.search(r"token=(\S+)", sent["body"]).group(1)
    assert sent["to"] == "sara@school.pk"
    assert client.post("/reset", data={"token": token, "new": "short", "confirm": "short"}).status_code == 400
    assert client.post("/reset", data={"token": token, "new": "brand-new-pass", "confirm": "brand-new-pass"},
                       follow_redirects=False).status_code == 303
    login(client, "t.sara", "brand-new-pass")
    client.get("/logout")
    # the link works only once
    assert client.post("/reset", data={"token": token, "new": "another-pass", "confirm": "another-pass"}).status_code == 400
    assert client.get("/reset?token=garbage").status_code == 200 and "invalid" in client.get("/reset?token=x").text
    assert auth.RESET_MAX_AGE == 3600


def test_hosting_database_url():
    from app.database import normalize_url

    assert normalize_url("postgres://u:p@h:5432/db") == "postgresql+psycopg://u:p@h:5432/db"
    assert normalize_url("sqlite:///x.db") == "sqlite:///x.db"
