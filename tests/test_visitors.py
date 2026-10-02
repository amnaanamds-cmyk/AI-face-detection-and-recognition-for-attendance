"""Visitor passes: kiosk recognition for the day, then the face template deletes itself."""
from datetime import timedelta

from app import database
from app.models import Notification, Visitor, now
from app.services import attendance as att
from app.services import visitors
from tests.conftest import data_url, face_image, login, noisy
from tests.test_office_kiosk import kiosk_visit, office_signup, add_person


def new_pass(client, seed, **extra):
    body = {"name": "Mr Guest", "host": "Ms Principal", "purpose": "parent meeting", "consent": True,
            "images": [data_url(noisy(face_image(seed), k)) for k in range(3)], **extra}
    return client.post("/api/visitors", json=body)


def test_guest_recognised_at_kiosk_then_template_self_destructs(client, monkeypatch):
    office_signup(client, monkeypatch)
    add_person(client, "E-1", "Employee One", 1)
    client.post("/settings", data={"liveness_mode": "motion", "checkout_after_minutes": "30"})
    assert new_pass(client, 77, consent=False).status_code == 400            # consent required
    r = new_pass(client, 77)
    assert r.status_code == 200, r.text
    last = kiosk_visit(client, 77, 500)
    last = client.post("/api/kiosk/frame", json={"image": data_url(noisy(face_image(77), 600))}).json() if not last["events"] else last
    with database.SessionLocal() as db:
        v = db.query(Visitor).one()
        assert v.checked_in_at is not None and v.embedding is not None
        alert = db.query(Notification).filter_by(level="visitor").one()
        assert "Guest Mr Guest arrived for Ms Principal" in alert.message
        org_id = v.org_id
    page = client.get("/visitors").text
    assert "Mr Guest" in page and "until" in page
    # the pass expires: template deleted, visit log kept
    with database.SessionLocal() as db:
        res = visitors.purge(db, org_id, when=now() + timedelta(days=1))
        v = db.query(Visitor).one()
        assert res["templates"] == 1 and v.embedding is None and v.purged_at is not None and v.checked_out_at is not None
    att.live_trackers.pop(-org_id)
    after = kiosk_visit(client, 77, 700)
    assert all("guest" not in f["label"] for f in after["faces"])             # no longer recognised
    # the log itself goes after visitor_log_days
    with database.SessionLocal() as db:
        assert visitors.purge(db, org_id, when=now() + timedelta(days=31))["logs"] == 1


def test_members_cannot_get_a_visitor_pass_and_end_visit(client, monkeypatch):
    office_signup(client, monkeypatch)
    add_person(client, "E-2", "Employee Two", 2)
    r = new_pass(client, 2)
    assert r.status_code == 400 and "already registered as Employee Two" in r.json()["detail"]
    assert new_pass(client, 88).status_code == 200
    with database.SessionLocal() as db:
        vid = db.query(Visitor).one().id
    client.post(f"/visitors/{vid}/end")
    with database.SessionLocal() as db:
        assert db.get(Visitor, vid).embedding is None


def test_key_rotation_keeps_guests_recognisable(client, monkeypatch):
    import numpy as np
    from app.services import biokey
    office_signup(client, monkeypatch)
    assert new_pass(client, 91).status_code == 200
    with database.SessionLocal() as db:
        v = db.query(Visitor).one()
        ref = biokey.unprotect(db, v.org_id, v.embedding, v.key_version)
        biokey.rotate(db, v.org_id)
        db.refresh(v)
        assert v.key_version == 2 and visitors.match(db, v.org_id, ref).id == v.id
