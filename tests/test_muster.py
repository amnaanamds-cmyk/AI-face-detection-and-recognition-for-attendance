"""Emergency roll call: on-site snapshot, face ticks at the assembly point, missing list, contacts, report."""
from app import database
from app.models import Course, MusterEntry, OutboxMessage, Student, Visitor
from app.services import attendance as att
from tests.conftest import data_url, face_image, noisy
from tests.test_office_kiosk import add_person, kiosk_visit, office_signup
from tests.test_visitors import new_pass


def check_in(client, seed, base, org_id=None):
    kiosk_visit(client, seed, base)
    with database.SessionLocal() as db:
        org_id = db.query(Course).first().org_id
    att.live_trackers.pop(-org_id, None)


def test_roll_call(client, monkeypatch):
    office_signup(client, monkeypatch)
    client.post("/settings", data={"liveness_mode": "motion", "checkout_after_minutes": "30"})
    a = add_person(client, "E-1", "Aamir", 31)
    b = add_person(client, "E-2", "Bushra", 32)
    add_person(client, "E-3", "Chand", 33)                      # never came in today
    client.post(f"/students/{b}/edit", data={"student_code": "E-2", "name": "Bushra", "department": "Sales",
                                             "semester": 1, "section": "A", "consent": "on", "is_active": "on",
                                             "guardian_phone": "0300 1112223"})
    check_in(client, 31, 100)
    check_in(client, 32, 200)
    assert new_pass(client, 34).status_code == 200
    with database.SessionLocal() as db:
        v = db.query(Visitor).one()
        v.checked_in_at = v.created_at                          # the guest is in the building
        db.commit()
    assert "On site now: <strong>2</strong>" in client.get("/muster").text
    client.post("/muster/start", data={"note": "fire alarm"})
    with database.SessionLocal() as db:
        names = sorted(e.name for e in db.query(MusterEntry))
        eid = db.query(MusterEntry).first().event_id
    assert names == ["Aamir", "Bushra", "Mr Guest"]            # Chand never checked in, so not expected
    for k in range(5):                                          # Aamir walks past the assembly-point camera
        r = client.post(f"/api/muster/{eid}/frame", json={"image": data_url(noisy(face_image(31), 900 + k))})
    s = r.json()
    assert s["safe"] == 1 and {m["name"] for m in s["missing"]} == {"Bushra", "Mr Guest"}
    guest = next(m for m in s["missing"] if m["name"] == "Mr Guest")
    s = client.post(f"/api/muster/{eid}/entries/{guest['id']}/safe").json()
    assert [m["name"] for m in s["missing"]] == ["Bushra"]
    assert {m["name"]: m["how"] for m in s["safe_list"]} == {"Aamir": "face", "Mr Guest": "manual"}
    client.post(f"/muster/{eid}/notify")
    with database.SessionLocal() as db:
        msg = db.query(OutboxMessage).one()
        assert msg.recipient == "+923001112223" and "Bushra has not been accounted for" in msg.body
    client.post(f"/muster/{eid}/end")
    report = client.get(f"/muster/{eid}").text
    assert "Roll call report" in report and "fire alarm" in report
    assert client.post(f"/api/muster/{eid}/frame", json={"image": data_url(face_image(31))}).status_code == 409
