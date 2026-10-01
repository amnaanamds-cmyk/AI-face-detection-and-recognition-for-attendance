"""Two-way parent SMS: STATUS / REPORT / LEAVE answered automatically through the school phone."""
from app import database
from app.models import Attendance, InboxMessage, OutboxMessage
from app.services import app_settings, messaging
from app.services.sms_commands import MAX_REPLIES_PER_DAY, parse
from tests.conftest import login
from tests.test_parent_messages import class_with_absentees


def test_parse_commands():
    assert parse("status")[0] == "status" and parse("  HAAZRI ")[0] == "status" and parse("حاضری")[0] == "status"
    assert parse("Leave fever since morning") == ("leave", "fever since morning")
    assert parse("chutti")[0] == "leave" and parse("Report")[0] == "report" and parse("hello")[0] == "unknown"


def paired(client):
    login(client)
    sid = class_with_absentees(client)
    client.post("/messages/gateway-token")
    with database.SessionLocal() as db:
        token = app_settings.get_setting(db, 1, "gateway_token")
    return sid, {"Authorization": f"Bearer {token}"}


def sms(client, auth, sender, body):
    r = client.post("/api/gateway/incoming", headers=auth, json={"sender": sender, "body": body})
    assert r.status_code == 200, r.text
    return r.json()


def test_parent_asks_status_and_gets_reply_via_the_phone(client, monkeypatch):
    monkeypatch.setattr(messaging, "RUN_IN_THREAD", False)
    sess, auth = paired(client)
    client.post(f"/sessions/{sess}/close")
    r = sms(client, auth, "+92 300 1234567", "status")                 # Ayesha's father, other number format
    assert r["command"] == "status"
    assert "Ayesha Khan today: CS-401 absent." in r["reply"] and "this month: 0%" in r["reply"]
    assert "Bilal" not in r["reply"]                                     # only his own child
    pulled = client.get("/api/gateway/messages", headers=auth).json()["messages"]
    assert any(m["to"] == "+923001234567" and m["body"] == r["reply"] for m in pulled)
    assert "+923001234567" in client.get("/messages").text


def test_unknown_number_learns_nothing(client):
    sess, auth = paired(client)
    r = sms(client, auth, "03459999999", "STATUS")
    assert "not registered" in r["reply"] and "Ayesha" not in r["reply"]


def test_leave_request_and_approval(client):
    sess, auth = paired(client)
    client.post(f"/sessions/{sess}/close")                              # Bilal marked absent
    r = sms(client, auth, "03217654321", "CHUTTI bukhar hai")
    assert r["state"] == "leave-pending" and "leave request for Bilal Ahmed" in r["reply"]
    with database.SessionLocal() as db:
        mid = db.query(InboxMessage).filter_by(state="leave-pending").one().id
    assert "Approve leave" in client.get("/messages").text
    client.post(f"/messages/inbox/{mid}/approve")
    with database.SessionLocal() as db:
        rec = db.query(Attendance).join(Attendance.student).filter_by(student_code="S-2").one()
        assert rec.status.value == "leave" and "bukhar" in rec.note
        assert db.get(InboxMessage, mid).state == "leave-approved"
        assert db.query(OutboxMessage).filter(OutboxMessage.body.like("%was approved%")).count() == 1


def test_replies_are_rate_limited_and_can_be_switched_off(client):
    sess, auth = paired(client)
    for _ in range(MAX_REPLIES_PER_DAY + 2):
        last = sms(client, auth, "03001234567", "help")
    assert last["state"] == "rate-limited"
    with database.SessionLocal() as db:
        assert db.query(OutboxMessage).filter_by(recipient="+923001234567").count() == MAX_REPLIES_PER_DAY
    client.post("/messages/settings", data={"parent_alerts": "off", "parent_channel": "phone", "country_code": "92",
                                            "parent_template": "x"})       # parent_replies unticked
    assert sms(client, auth, "03001234567", "status")["state"] == "ignored"
