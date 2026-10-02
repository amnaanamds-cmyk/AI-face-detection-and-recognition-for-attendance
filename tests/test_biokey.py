"""Cancelable biometrics: protected templates, same accuracy, rotation without re-enrollment, unlinkable orgs."""
import numpy as np

from app import database
from app.models import FaceEmbedding, Student
from app.security import decrypt_embedding
from app.services import biokey, faces
from app.tenancy import create_org
from tests.conftest import data_url, face_image, login, noisy
from tests.test_multitenancy import build_org


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def test_rotation_matrix_keeps_similarity(db):
    org = create_org(db, "K", "school")
    _, q = biokey.current(db, org.id)
    rng = np.random.default_rng(0)
    a, b = unit(rng.standard_normal(128)), unit(rng.standard_normal(128))
    assert np.allclose(q @ q.T, np.eye(128), atol=1e-5)
    assert abs(float((q @ a) @ (q @ b)) - float(a @ b)) < 1e-5     # recognition accuracy unchanged
    assert float((q @ a) @ a) < 0.5                                 # but stored numbers look nothing like the face


def test_stored_templates_are_protected_and_rotation_keeps_recognition(client):
    login(client)
    sid, sess = build_org(client, "BK", 9)
    with database.SessionLocal() as db:
        st = db.get(Student, sid)
        rows = db.query(FaceEmbedding).filter_by(student_id=sid).all()
        assert rows and all(r.key_version == 1 for r in rows)
        before = [decrypt_embedding(r.embedding) for r in rows]
        gallery = faces.load_gallery(db, st.org_id)
        probe = gallery.matrix[0]
        assert gallery.match(probe, 0.45).student_id == sid
        n = biokey.rotate(db, st.org_id)
        assert n == len(rows)
        rows = db.query(FaceEmbedding).filter_by(student_id=sid).all()
        after = [decrypt_embedding(r.embedding) for r in rows]
        assert all(r.key_version == 2 for r in rows)
        assert max(float(a @ b) for a, b in zip(before, after)) < 0.5     # old (leaked) copies are now useless
        assert faces.load_gallery(db, st.org_id).match(probe, 0.45).student_id == sid   # nobody re-registers
    page = client.get("/integrity").text
    assert "Biometric key" in page and "version 2" in page
    client.post("/integrity/rotate-key")
    with database.SessionLocal() as db:
        assert db.query(FaceEmbedding).first().key_version == 3


def test_same_face_in_two_organizations_is_unlinkable(db):
    a, b = create_org(db, "A", "school"), create_org(db, "B", "school")
    v = unit(np.random.default_rng(1).standard_normal(128))
    ta = decrypt_embedding(biokey.protect(db, a.id, v)[0])
    tb = decrypt_embedding(biokey.protect(db, b.id, v)[0])
    assert float(ta @ tb) < 0.5


def test_legacy_templates_get_protected(db):
    from app.security import encrypt_embedding
    org = create_org(db, "L", "school")
    st = Student(org_id=org.id, student_code="L1", name="L", consent_given=True)
    db.add(st)
    db.flush()
    v = unit(np.random.default_rng(2).standard_normal(128))
    db.add(FaceEmbedding(student_id=st.id, embedding=encrypt_embedding(v), model_name="fake", key_version=0))
    db.commit()
    assert biokey.protect_legacy(db, org.id) == 1
    row = db.query(FaceEmbedding).one()
    assert row.key_version == 1 and np.allclose(biokey.unprotect(db, org.id, row.embedding, 1), v, atol=1e-5)
