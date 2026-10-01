"""Proxy watch: same face twice in a frame, same person in two rooms at once, manual overrides."""
from datetime import datetime

import numpy as np

from app import database
from app.models import ClassSession, Course, Enrollment, Notification, SessionState, Student
from app.services import attendance as att
from app.services.proxy import manual_overrides
from app.tenancy import create_org
from app.vision.liveness import LivenessChecker
from app.vision.matcher import Gallery
from app.vision.pipeline import FaceTracker, RecognitionPipeline
from tests.conftest import login
from tests.test_matcher_pipeline import ScriptedBackend, scripted_face, unit
from tests.test_multitenancy import build_org


def test_same_person_twice_in_one_frame_is_not_accepted():
    backend = ScriptedBackend()
    gallery = Gallery(np.stack([unit(1, 0, 0), unit(0, 1, 0)]), [10, 20])
    pipe = RecognitionPipeline(backend, LivenessChecker(min_frames=3, motion_threshold=0.06, timeout_seconds=4,
                                                        min_sharpness=0), threshold=0.5, margin=0.05, votes_required=3)
    tracker = FaceTracker()
    camera = np.random.RandomState(1)
    for i, yaw in enumerate([0, 20, -20, 20, 0]):
        backend.frames.append([
            (scripted_face(0, (1, 0.02, 0), yaw), None),     # student 10 in person
            (scripted_face(250, (1, -0.02, 0), yaw), None),  # ... and a photo / look-alike of student 10
            (scripted_face(400, (0, 1, 0), yaw), None),      # student 20, alone
        ])
        results = pipe.process(camera.randint(0, 255, (300, 500, 3)).astype(np.uint8), gallery, tracker, True, t=i)
    by_x = {r.bbox[0]: r for r in results}
    assert by_x[0].clash == 10 and by_x[0].student_id is None
    assert by_x[250].clash == 10 and by_x[250].student_id is None
    assert by_x[400].clash is None and by_x[400].student_id == 20   # innocent bystander unaffected


def _two_overlapping_classes(db):
    org = create_org(db, "Proxy U", "school")
    a = Course(org_id=org.id, code="CS-1", name="A")
    b = Course(org_id=org.id, code="CS-2", name="B")
    st = Student(org_id=org.id, student_code="P-1", name="Pervez", consent_given=True)
    db.add_all([a, b, st])
    db.flush()
    db.add_all([Enrollment(student_id=st.id, course_id=a.id), Enrollment(student_id=st.id, course_id=b.id)])
    day = datetime(2026, 10, 1, 9, 0)
    s1 = ClassSession(course_id=a.id, date=day.date(), start_time=day, duration_minutes=60, state=SessionState.active)
    s2 = ClassSession(course_id=b.id, date=day.date(), start_time=day.replace(minute=30), duration_minutes=60,
                      state=SessionState.active)
    db.add_all([s1, s2])
    db.commit()
    return st, s1, s2, day


def test_recognised_in_two_rooms_at_once(db):
    st, s1, s2, day = _two_overlapping_classes(db)
    att.mark_attendance(db, s1, st.id, when=day.replace(minute=35), similarity=0.8)
    assert db.query(Notification).filter_by(level="proxy").count() == 0
    att.mark_attendance(db, s2, st.id, when=day.replace(minute=40), similarity=0.8)
    alerts = db.query(Notification).filter_by(level="proxy").all()
    assert len(alerts) == 1 and "CS-1 at 09:35 and in CS-2 at 09:40" in alerts[0].message


def test_back_to_back_classes_are_fine(db):
    st, s1, s2, day = _two_overlapping_classes(db)
    s2.start_time = day.replace(hour=10)   # B starts when A ends
    db.commit()
    att.mark_attendance(db, s1, st.id, when=day.replace(minute=5))
    att.mark_attendance(db, s2, st.id, when=day.replace(hour=10, minute=2))
    assert db.query(Notification).filter_by(level="proxy").count() == 0


def test_manual_overrides_page(client):
    login(client)
    sid, sess = build_org(client, "MO", 5)
    client.post(f"/sessions/{sess}/override", data={"student_id": sid, "status": "present", "note": "said he was here"})
    with database.SessionLocal() as db:
        rows = manual_overrides(db, 1)
    assert rows and rows[0]["actor"] == "admin" and rows[0]["manual"] == 1
    page = client.get("/proxy").text
    assert "Proxy watch" in page and "admin" in page
