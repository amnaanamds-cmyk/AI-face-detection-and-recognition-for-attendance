"""Tamper-evident ledger and signed attendance certificates - attacked on purpose."""
import re

import pytest
from sqlalchemy import text

from app import database
from app.models import Attendance, LedgerEntry, Student
from app.services import certificates, ledger
from tests.conftest import login
from tests.test_multitenancy import build_org


@pytest.fixture(autouse=True)
def _key(tmp_path, monkeypatch):
    monkeypatch.setattr(certificates, "KEY_FILE", tmp_path / "signing.pem")


def school(client):
    login(client)
    sid, sess = build_org(client, "LG", 3)
    client.post(f"/sessions/{sess}/close")              # the student was not seen: auto-absent
    return sid, sess


def report():
    with database.SessionLocal() as db:
        return ledger.verify(db, 1)


def test_changes_are_chained_with_the_actor(client):
    sid, sess = school(client)
    client.post(f"/sessions/{sess}/override", data={"student_id": sid, "status": "excused", "note": "doctor"})
    with database.SessionLocal() as db:
        entries = db.query(LedgerEntry).order_by(LedgerEntry.seq).all()
    assert [e.action for e in entries] == ["create", "update"]
    assert entries[0].actor == "admin" and '"method": "auto-absent"' in entries[0].payload
    assert '"status": "excused"' in entries[1].payload and entries[1].prev_hash == entries[0].hash
    r = report()
    assert r["ok"] and r["head_seq"] == 2
    page = client.get("/integrity").text
    assert "match the ledger" in page and r["head_hash"] in page


def test_direct_database_edit_is_detected(client):
    school(client)
    with database.engine.begin() as c:
        c.execute(text("UPDATE attendance SET status = 'present'"))
    r = report()
    assert not r["ok"] and "changed outside FaceAttend (status: absent -> present)" in r["problems"][0]
    assert "integrity problem" in client.get("/integrity").text


def test_rewritten_or_deleted_history_is_detected(client):
    sid, sess = school(client)
    client.post(f"/sessions/{sess}/override", data={"student_id": sid, "status": "late", "note": ""})
    with database.engine.begin() as c:   # attacker rewrites the first entry to hide the absence
        c.execute(text("UPDATE attendance_ledger SET payload = replace(payload, 'absent', 'present') WHERE seq = 1"))
    assert any("Entry #1" in p and "altered" in p for p in report()["problems"])
    with database.engine.begin() as c:
        c.execute(text("UPDATE attendance_ledger SET payload = replace(payload, 'present', 'absent') WHERE seq = 1"))
    assert report()["ok"]                # restored -> valid again
    with database.engine.begin() as c:
        c.execute(text("DELETE FROM attendance_ledger WHERE seq = 1"))
    assert any("missing" in p or "altered" in p for p in report()["problems"])


def test_deleted_record_is_detected(client):
    school(client)
    with database.engine.begin() as c:
        c.execute(text("DELETE FROM attendance"))   # removed behind FaceAttend's back
    assert any("deleted outside FaceAttend" in p for p in report()["problems"])


def test_inserted_record_is_detected(client):
    sid, sess = school(client)
    with database.engine.begin() as c:
        c.execute(text("DELETE FROM attendance"))
        # a fake "present" for the student, written straight into the database (and the deletion hidden)
        c.execute(text(f"INSERT INTO attendance (id, student_id, session_id, course_id, date, status, method) "
                       f"VALUES (999, {sid}, {sess}, (SELECT course_id FROM sessions WHERE id = {sess}), date('now'), 'present', 'face')"))
    problems = report()["problems"]
    assert any("record 999 is not in the ledger" in p for p in problems)


def test_normal_deletions_are_not_false_alarms(client):
    sid, sess = school(client)
    client.post(f"/students/{sid}/delete")
    r = report()
    assert r["ok"], r["problems"]
    with database.SessionLocal() as db:
        assert db.query(Attendance).count() == 0 and db.query(LedgerEntry).filter_by(action="delete").count() == 1


def test_certificate_is_signed_and_verifiable(client):
    sid, sess = school(client)
    client.post(f"/sessions/{sess}/override", data={"student_id": sid, "status": "present", "note": ""})
    r = client.get(f"/students/{sid}/certificate")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    with database.SessionLocal() as db:
        st = db.get(Student, sid)
        from datetime import date
        data = certificates.build(db, st, date(2000, 1, 1), date.today())
    assert data["overall"] == 100.0 and data["groups"][0]["code"] == "CS-401"
    token = certificates.sign(data)
    assert certificates.verify(token).valid
    client.cookies.clear()                                   # anyone can verify, no login
    page = client.get("/verify", params={"c": token}).text
    assert "Genuine certificate" in page and "LG Student" in page
    # forge: raise the percentage but keep the signature
    import base64, json
    body, sig = token.split(".")
    forged = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    forged["groups"][0]["pct"] = 99.9
    fb = base64.urlsafe_b64encode(json.dumps(forged, sort_keys=True, separators=(",", ":")).encode()).decode().rstrip("=")
    v = certificates.verify(fb + "." + sig)
    assert not v.valid and "altered" in v.reason
    assert "Not valid" in client.get("/verify", params={"c": fb + "." + sig}).text
    assert "not a FaceAttend certificate" in certificates.verify("garbage").reason


def test_reports_carry_the_fingerprint(client):
    school(client)
    pdf = client.get("/reports/monthly?fmt=pdf").content
    assert pdf.startswith(b"%PDF")
