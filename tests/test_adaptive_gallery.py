"""Self-learning gallery and recognition health."""
from datetime import timedelta

import numpy as np

from app import database
from app.models import FaceEmbedding, Student, now
from app.services import biokey, faces
from tests.conftest import login
from tests.test_multitenancy import build_org


def setup(client):
    login(client)
    sid, sess = build_org(client, "AD", 11)
    return sid, sess


def own_vector(db, sid):
    st = db.get(Student, sid)
    r = db.query(FaceEmbedding).filter_by(student_id=sid).first()
    return st, biokey.unprotect(db, st.org_id, r.embedding, r.key_version)


def near(v, angle, seed=5):
    """A unit vector with cosine `angle` to v (a 'changed appearance' of the same face)."""
    rng = np.random.default_rng(seed)
    o = rng.standard_normal(v.shape).astype(np.float32)
    o -= (o @ v) * v
    o /= np.linalg.norm(o)
    return (angle * v + np.sqrt(1 - angle ** 2) * o).astype(np.float32)


def test_learns_a_new_view_with_all_guards(client):
    sid, _ = setup(client)
    with database.SessionLocal() as db:
        st, v = own_vector(db, sid)
        assert faces.learn_from_sighting(db, st, v, 0.5) == "not confident enough"
        assert faces.learn_from_sighting(db, st, v, 0.99) == "nothing new"         # same view: nothing to learn
        new_view = near(v, 0.8)
        assert faces.learn_from_sighting(db, st, new_view, 0.8) is None             # learned
        assert db.query(FaceEmbedding).filter_by(student_id=sid, source="adaptive").count() == 1
        assert faces.learn_from_sighting(db, st, near(v, 0.75), 0.8) == "learned recently"
        # the learned view is recognised afterwards
        assert faces.load_gallery(db, st.org_id).match(new_view, 0.45).student_id == sid
        # cap: at most 3 learned templates, registration photos are kept
        for k in range(4):
            db.query(FaceEmbedding).filter_by(source="adaptive").update({"created_at": now() - timedelta(days=30)})
            db.commit()
            assert faces.learn_from_sighting(db, st, near(v, 0.7, seed=100 + k), 0.8) is None
        assert db.query(FaceEmbedding).filter_by(student_id=sid, source="adaptive").count() == 3
        assert db.query(FaceEmbedding).filter_by(student_id=sid, source="enrolled").count() == 3


def test_never_learns_a_face_that_resembles_someone_else(client):
    sid, _ = setup(client)
    r = client.post("/students/new", data={"student_code": "AD-002", "name": "Other", "semester": 7, "section": "A",
                                           "department": "Computer Science", "consent": "on"}, follow_redirects=False)
    other = int(r.headers["location"].split("/")[2])
    with database.SessionLocal() as db:
        st, v = own_vector(db, sid)
        o = db.get(Student, other)
        # pretend the camera wrongly confirmed 'Other' on AD-001's face
        assert faces.learn_from_sighting(db, o, near(v, 0.97), 0.95) in ("resembles someone else", "no registration")
        assert db.query(FaceEmbedding).filter_by(source="adaptive").count() == 0


def test_switch_and_health_page(client):
    sid, sess = setup(client)
    client.post(f"/sessions/{sess}/override", data={"student_id": sid, "status": "present", "note": ""})
    client.post("/settings", data={"present_window_minutes": "10"})              # switch unticked -> off
    with database.SessionLocal() as db:
        st, v = own_vector(db, sid)
        assert faces.learn_from_sighting(db, st, near(v, 0.8), 0.9) == "switched off"
        rows = faces.recognition_health(db, st.org_id)
    assert rows[0]["student"].id == sid and rows[0]["manual"] == 1
    page = client.get("/recognition-health").text
    assert "Recognition health" in page and "AD Student" in page
