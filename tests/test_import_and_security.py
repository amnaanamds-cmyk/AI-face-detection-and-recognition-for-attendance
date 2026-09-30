import io
import zipfile

import cv2
import numpy as np
from openpyxl import Workbook

from app import database
from app.models import Enrollment, FaceEmbedding, Student
from app.vision.liveness import CHECKING, LIVE, SPOOF, LivenessChecker, LivenessState
from tests.conftest import face_image, login, noisy
from tests.test_liveness import FACE_3D, project, sharp


def _jpg(img):
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tobytes()


def test_import_excel_class_list_and_zip_faces(client):
    login(client)
    client.post("/courses/new", data={"code": "CS-401", "name": "AI", "semester": 7, "section": "A"})
    wb = Workbook()
    ws = wb.active
    ws.append(["Reg No", "Student Name", "Roll #", "Sem", "Sec", "Program", "Consent", "Hobby"])
    ws.append(["BSCS-2023-001", "Amina Bibi", "1", 7, "A", "Computer Science", "yes", "x"])
    ws.append(["BSCS-2023-002", "Ali Khan", "2", 7, "A", "Computer Science", "no", ""])
    ws.append(["", "No ID", "", 7, "A", "", "", ""])
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post("/students/import", files={"file": ("list.xlsx", buf.getvalue())}, data={"auto_enroll": "on"})
    assert "2 created" in r.text and "2 course enrolment" in r.text
    assert "ignored columns: Hobby" in r.text and "row 4: missing Student ID" in r.text

    # re-import updates instead of duplicating
    csv_text = "Student ID;Name;Email\nBSCS-2023-001;Amina Bibi;amina@uni.edu\n"
    r = client.post("/students/import", files={"file": ("list.csv", csv_text.encode())})
    assert "0 created, 1 updated" in r.text

    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        for k in range(3):
            zf.writestr(f"BSCS-2023-001/{k}.jpg", _jpg(noisy(face_image(1), k)))
        zf.writestr("BSCS-2023-002_1.jpg", _jpg(face_image(2)))          # no consent
        zf.writestr("UNKNOWN-9/1.jpg", _jpg(face_image(3)))              # not in class list
        zf.writestr("__MACOSX/BSCS-2023-001/._0.jpg", b"junk")
    r = client.post("/students/import/faces", files={"file": ("faces.zip", z.getvalue())})
    assert "3 template(s) registered for 1 student(s)" in r.text
    assert "BSCS-2023-002: biometric consent not recorded" in r.text and "UNKNOWN-9: no student" in r.text
    with database.SessionLocal() as db:
        amina = db.query(Student).filter_by(student_code="BSCS-2023-001").one()
        assert amina.email == "amina@uni.edu" and amina.consent_given and amina.semester == 7
        assert db.query(Enrollment).count() == 2
        assert db.query(FaceEmbedding).count() == 3


def test_same_face_cannot_be_enrolled_for_two_students(client):
    login(client)
    for code in ("S1", "S2"):
        client.post("/students/new", data={"student_code": code, "name": code, "consent": "on"})
    img = [__import__("tests.conftest", fromlist=["data_url"]).data_url(noisy(face_image(5), k)) for k in range(3)]
    assert client.post("/api/students/1/faces", json={"images": img}).json()["accepted"] == 3
    r = client.post("/api/students/2/faces", json={"images": img}).json()
    assert r["accepted"] == 0 and "already registered as S1" in r["rejected"][0]


def test_dominant_face_used_when_background_faces_present(db):
    from app.services import faces
    from app.vision.base import DetectedFace

    class TwoFaces:
        name, embedding_dim = "two", 3

        def detect(self, image):
            return [DetectedFace((0, 0, 40, 40), 0.9, np.zeros((5, 2))), DetectedFace((50, 0, 100, 100), 0.9, np.zeros((5, 2)))]

        def embed(self, image, face):
            return np.array([1.0, 0, 0], np.float32)

    from app.vision import backends
    backends.set_backend(TwoFaces())
    from app.tenancy import create_org
    org = create_org(db, "Org")
    st = Student(student_code="X", name="X", consent_given=True, org_id=org.id)
    db.add(st)
    db.commit()
    rep = faces.enroll_images(db, st, [np.zeros((100, 200, 3), np.uint8)])
    assert rep.accepted == 1


def test_login_lockout(client):
    for _ in range(5):
        assert client.post("/login", data={"username": "admin", "password": "wrong"}).status_code == 401
    r = client.post("/login", data={"username": "admin", "password": "admin123"})
    assert r.status_code == 429  # even the right password is refused during lockout


def test_settings_liveness_mode_validation(client):
    login(client)
    client.post("/settings", data={"liveness_mode": "cnn+motion", "match_threshold": "0.5"})
    assert 'value="cnn+motion" selected' in client.get("/settings").text
    r = client.post("/settings", data={"liveness_mode": "magic"}, follow_redirects=True)
    assert "must be one of" in r.text


def test_cnn_mode_passive_accept_and_reject():
    chk = LivenessChecker(mode="cnn")
    st = LivenessState()
    for i in range(5):  # 5 frames: CNN votes + proof that the video is not a frozen still image
        chk.update(st, i * 0.5, project(FACE_3D), sharp(), cnn_live=0.97)
    assert st.decision == LIVE  # no head movement needed
    st = LivenessState()
    for i in range(3):
        chk.update(st, i * 0.5, project(FACE_3D, yaw_deg=(-20) ** i % 40), sharp(), cnn_live=0.02)
    assert st.decision == SPOOF and "anti-spoofing" in st.reason


def test_cnn_plus_motion_requires_both():
    chk = LivenessChecker(mode="cnn+motion", timeout_seconds=100)
    st = LivenessState()
    for i in range(8):  # CNN says live, but the face never turns
        chk.update(st, i * 0.5, project(FACE_3D), sharp(), cnn_live=0.95)
    assert st.decision == CHECKING
    for i, yaw in enumerate([0, -20, -20, -20, 0, 20, 20, 20]):  # turn left, hold ~1 s, turn right, hold
        chk.update(st, 5 + i * 0.5, project(FACE_3D, yaw_deg=yaw), sharp(), cnn_live=0.95)
    assert st.decision == LIVE


def test_cnn_mode_without_model_falls_back_to_motion():
    chk = LivenessChecker(mode="cnn", timeout_seconds=100)
    st = LivenessState()
    for i, yaw in enumerate([0, -20, 20, -20, 20, -20, 20]):
        chk.update(st, i * 0.5, project(FACE_3D, yaw_deg=yaw), sharp(), cnn_live=None)
    assert st.decision == LIVE
