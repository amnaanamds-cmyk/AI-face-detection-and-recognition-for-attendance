import numpy as np

from app.vision.base import DetectedFace, l2_normalize
from app.vision.liveness import LivenessChecker
from app.vision.matcher import Gallery
from app.vision.pipeline import FaceTracker, RecognitionPipeline, is_accepted, iou
from tests.test_liveness import FACE_3D, project


def unit(*v):
    return l2_normalize(np.array(v, dtype=np.float32))


def test_gallery_match_unknown_and_ambiguous():
    g = Gallery(np.stack([unit(1, 0, 0), unit(0.95, 0.3, 0), unit(0, 1, 0)]), [1, 1, 2])
    assert g.match(unit(1, 0.1, 0), threshold=0.5).student_id == 1
    assert g.match(unit(0, 0, 1), threshold=0.5).student_id is None          # unknown person
    r = Gallery(np.stack([unit(1, 0, 0), unit(0, 1, 0)]), [1, 2]).match(unit(1, 1.05, 0), threshold=0.5, margin=0.1)
    assert r.student_id is None and r.reason == "ambiguous"
    assert Gallery().match(unit(1, 0, 0), 0.5).student_id is None            # empty gallery


def test_iou():
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert iou((0, 0, 10, 10), (20, 20, 5, 5)) == 0.0
    assert 0.3 < iou((0, 0, 10, 10), (3, 0, 10, 10)) < 0.6


def test_tracker_keeps_identity_and_expires():
    tr = FaceTracker(max_age=2)
    f = lambda x: DetectedFace((x, 10, 50, 50), 0.9, np.zeros((5, 2)))
    a = tr.update([f(0), f(200)], 0.0)
    b = tr.update([f(5), f(205)], 0.5)
    assert [t.id for t, _ in a] == [t.id for t, _ in b]
    c = tr.update([f(5)], 5.0)  # everything older than max_age is dropped
    assert c[0][0].id not in {t.id for t, _ in a}


class ScriptedBackend:
    """Returns pre-scripted faces so the full pipeline can be exercised deterministically."""
    name, embedding_dim = "scripted", 3

    def __init__(self):
        self.frames = []

    def detect(self, image):
        return [f for f, _ in self.frames.pop(0)] if self.frames else []

    def embed(self, image, face):
        return face.raw


def scripted_face(x, emb, yaw):
    lm = project(FACE_3D, yaw_deg=yaw, shift=(x + 50, 60))
    return DetectedFace((x, 10, 100, 100), 0.95, lm, raw=unit(*emb))


def test_pipeline_votes_liveness_multi_face():
    backend = ScriptedBackend()
    gallery = Gallery(np.stack([unit(1, 0, 0), unit(0, 1, 0)]), [10, 20])
    pipe = RecognitionPipeline(backend, LivenessChecker(min_frames=3, motion_threshold=0.06, timeout_seconds=4,
                                                        min_sharpness=0), threshold=0.5, margin=0.05, votes_required=3)
    tracker = FaceTracker()
    frame = np.random.RandomState(0).randint(0, 255, (300, 500, 3)).astype(np.uint8)
    yaws = [0, 20, -20, 20, -20, 0, 0, 0]
    results = []
    for i, yaw in enumerate(yaws):
        backend.frames.append([
            (scripted_face(0, (1, 0.05, 0), yaw), None),     # live student 10, turning head
            (scripted_face(250, (0, 1, 0), 0), None),        # student 20 shown on a static photo
            (scripted_face(400, (0, 0, 1), yaw), None),  # stranger (live, but not registered)
        ])
        results.append(pipe.process(frame, gallery, tracker, liveness_required=True, t=i * 1.0))

    first = results[0]
    assert all(r.student_id is None for r in first)                # needs 3 agreeing votes first
    third = {r.bbox[0]: r for r in results[2]}
    assert third[0].student_id == 10 and is_accepted(third[0])     # live + confirmed
    assert third[250].student_id == 20 and not is_accepted(third[250])
    last = {r.bbox[0]: r for r in results[-1]}
    assert last[250].liveness == "spoof"                           # photo never shows 3-D motion
    assert last[400].student_id is None                            # unknown never gets an identity


def test_new_person_in_same_seat_gets_new_track():
    """Student A leaves, student B sits in the same place a second later: B must get
    their own track (own votes, own liveness, own attendance) instead of inheriting A's."""
    backend = ScriptedBackend()
    gallery = Gallery(np.stack([unit(1, 0, 0), unit(0, 1, 0)]), [10, 20])
    pipe = RecognitionPipeline(backend, LivenessChecker(), threshold=0.5, margin=0.05, votes_required=3)
    tracker = FaceTracker()
    frame = np.zeros((300, 500, 3), np.uint8)
    out = []
    for i in range(8):
        emb = (1, 0.05, 0) if i < 4 else (0.05, 1, 0)  # same box, different person from frame 4
        backend.frames.append([(scripted_face(0, emb, 0), None)])
        out.append(pipe.process(frame, gallery, tracker, liveness_required=False, t=i * 0.5)[0])
    assert out[3].student_id == 10
    assert out[4].track_id != out[3].track_id and out[4].student_id is None  # fresh track, no inherited votes
    assert out[-1].student_id == 20
