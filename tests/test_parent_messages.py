"""Messages to parents when a student is absent: outbox, Twilio, Android phone gateway, manual links."""
import json
import re
from datetime import date, datetime

import pytest

from app import database
from app.models import OutboxMessage, Student
from app.services import messaging
from tests.conftest import login


@pytest.fixture(autouse=True)
def _sync_delivery(monkeypatch):
    monkeypatch.setattr(messaging, "RUN_IN_THREAD", False)


def test_normalize_phone():
    assert messaging.normalize_phone("0300-1234567") == "+923001234567"
    assert messaging.normalize_phone("+44 20 7946 0958") == "+442079460958"
    assert messaging.normalize_phone("0044 20 7946 0958") == "+442079460958"
    assert messaging.normalize_phone("923001234567") == "+923001234567"
    assert messaging.normalize_phone("3001234567") == "+923001234567"
    assert messaging.normalize_phone("12") is None and messaging.normalize_phone("") is None


def test_template_keeps_unknown_placeholders():
    assert messaging.render("Hi {parent}, {oops}", parent="Ali") == "Hi Ali, {oops}"


def class_with_absentees(client):
    """Course, two students with guardian contacts (one also without), one running session."""
    client.post("/courses/new", data={"code": "CS-401", "name": "AI", "semester": 7, "section": "A"})
    for code, name, phone, email in (("S-1", "Ayesha Khan", "0300 1234567", "parent1@example.com"),
                                     ("S-2", "Bilal Ahmed", "0321-7654321", ""),
                                     ("S-3", "No Contact", "", "")):
        r = client.post("/students/new", data={"student_code": code, "name": name, "semester": 7, "section": "A",
                                               "department": "Computer Science", "consent": "on", "auto_enroll": "on",
                                               "guardian_name": "Mr " + name.split()[1], "guardian_phone": phone,
                                               "guardian_email": email}, follow_redirects=False)
        assert r.status_code == 303
    course_id = int(re.search(r'href="/courses/(\d+)"', client.get("/courses").text).group(1))
    r = client.post("/sessions/new", data={"course_id": course_id, "day": date.today().isoformat(),
                                           "start": datetime.now().strftime("%H:%M"), "duration": 60,
                                           "present_window": 10, "late_window": 20, "start_now": "1"},
                    follow_redirects=False)
    return int(r.headers["location"].split("/")[2])


def test_absence_messages_via_phone_gateway(client):
    login(client)
    sid = class_with_absentees(client)
    with database.SessionLocal() as db:
        s = db.query(Student).filter_by(student_code="S-1").one()
        assert s.guardian_phone == "0300 1234567" and db.query(Student).filter_by(student_code="S-3").one().guardian_phone is None
    client.post("/messages/settings", data={"parent_alerts": "absent", "parent_channel": "phone", "country_code": "92",
                                            "parent_email": "on",
                                            "parent_template": "Dear {parent}, {student} was {status} in {group} on {date}."})
    client.post(f"/sessions/{sid}/close")
    with database.SessionLocal() as db:
        msgs = db.query(OutboxMessage).order_by(OutboxMessage.id).all()
    phone = [m for m in msgs if m.channel == "phone"]
    assert sorted(m.recipient for m in phone) == ["+923001234567", "+923217654321"]
    assert all(m.status == "pending" for m in phone)
    assert any(m.body.startswith("Dear Mr Khan, Ayesha Khan was absent in CS-401 AI on") for m in phone)
    email = [m for m in msgs if m.channel == "email"]
    assert len(email) == 1 and email[0].recipient == "parent1@example.com"
    assert email[0].status == "failed" and "SMTP" in email[0].error  # no SMTP configured in tests

    # the Android app: not paired yet
    assert client.get("/api/gateway/messages").status_code == 401
    client.post("/messages/gateway-token")
    page = client.get("/messages").text
    assert "/messages/pair.svg" in page and "Ayesha Khan" in page
    assert client.get("/messages/pair.svg").headers["content-type"].startswith("image/svg")
    with database.SessionLocal() as db:
        from app.services import app_settings
        token = app_settings.get_setting(db, 1, "gateway_token")
    auth = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/gateway/ping", headers=auth).json()["ok"]
    assert client.get("/api/gateway/messages", headers={"Authorization": "Bearer 1.wrong"}).status_code == 401
    pulled = client.get("/api/gateway/messages", headers=auth).json()["messages"]
    assert len(pulled) == 2 and {"id", "to", "body"} <= set(pulled[0])
    assert client.get("/api/gateway/messages", headers=auth).json()["messages"] == []  # taken, not offered twice
    client.post(f"/api/gateway/messages/{pulled[0]['id']}", headers=auth, json={"ok": True})
    client.post(f"/api/gateway/messages/{pulled[1]['id']}", headers=auth, json={"ok": False, "error": "no credit"})
    with database.SessionLocal() as db:
        states = {m.id: (m.status, m.error) for m in db.query(OutboxMessage)}
    assert states[pulled[0]["id"]] == ("sent", None) and states[pulled[1]["id"]] == ("failed", "no credit")

    # pressing "Message parents" again never double-sends
    client.post(f"/sessions/{sid}/notify-parents")
    with database.SessionLocal() as db:
        assert db.query(OutboxMessage).count() == len(msgs)


def test_alerts_off_and_manual_button(client):
    login(client)
    sid = class_with_absentees(client)
    client.post(f"/sessions/{sid}/close")  # default: off
    with database.SessionLocal() as db:
        assert db.query(OutboxMessage).count() == 0
    client.post("/messages/settings", data={"parent_alerts": "off", "parent_channel": "manual", "country_code": "92",
                                            "parent_template": "{student} absent"})
    client.post(f"/sessions/{sid}/notify-parents")  # teacher presses the button: sent although automatic is off
    with database.SessionLocal() as db:
        m = db.query(OutboxMessage).filter_by(channel="manual").first()
        assert m.status == "manual-pending" and db.query(OutboxMessage).count() == 2
    page = client.get("/messages").text
    assert "https://wa.me/923001234567?text=Ayesha%20Khan%20absent" in page
    assert client.post(f"/messages/{m.id}/done").json()["ok"]
    with database.SessionLocal() as db:
        assert db.get(OutboxMessage, m.id).status == "manual"


def test_twilio_sms_and_failure(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "twilio_account_sid", "AC123")
    monkeypatch.setattr(settings, "twilio_auth_token", "secret")
    monkeypatch.setattr(settings, "twilio_sms_from", "+15550001111")
    sent = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"sid": "SM1"}'

    def fake_urlopen(req, timeout=0):
        body = dict(x.split("=", 1) for x in req.data.decode().split("&"))
        sent.append((req.full_url, body))
        if "7654321" in body["To"]:
            import io
            import urllib.error
            raise urllib.error.HTTPError(req.full_url, 400, "bad", {}, io.BytesIO(json.dumps({"message": "Invalid 'To' number"}).encode()))
        return Resp()

    monkeypatch.setattr(messaging.urllib.request, "urlopen", fake_urlopen)
    login(client)
    sid = class_with_absentees(client)
    client.post("/messages/settings", data={"parent_alerts": "absent", "parent_channel": "sms", "country_code": "92",
                                            "parent_template": "{student} was {status}"})
    client.post(f"/sessions/{sid}/close")
    assert sent and sent[0][0] == "https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json"
    assert sent[0][1]["From"] == "%2B15550001111"
    with database.SessionLocal() as db:
        sms = {m.recipient: m for m in db.query(OutboxMessage).filter_by(channel="sms")}
    assert sms["+923001234567"].status == "sent"
    assert sms["+923217654321"].status == "failed" and "Invalid 'To' number" in sms["+923217654321"].error


def test_general_settings_do_not_touch_message_settings(client):
    login(client)
    client.post("/messages/settings", data={"parent_alerts": "absent", "parent_channel": "phone", "country_code": "92",
                                            "parent_template": "x", "parent_email": "on"})
    client.post("/settings", data={"present_window_minutes": "12", "liveness_mode": "auto"})
    from app.services import app_settings
    with database.SessionLocal() as db:
        assert app_settings.get_setting(db, 1, "parent_email") is True
        assert app_settings.get_setting(db, 1, "parent_alerts") == "absent"
    assert "parent_template" not in client.get("/settings").text


def test_import_reads_parent_columns(db):
    from app.services.importer import COLUMN_ALIASES
    assert COLUMN_ALIASES["parentphone"] == "guardian_phone" and COLUMN_ALIASES["fathername"] == "guardian_name"
