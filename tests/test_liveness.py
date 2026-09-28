import numpy as np
import pytest

from app.vision.liveness import (CHECKING, LIVE, SPOOF, LivenessChecker, LivenessState, affine_nose_coordinates,
                                 roll_degrees)

# A rough 3-D face model (x right, y down, z towards camera), units ~ cm.
FACE_3D = np.array([
    [-3.2, -1.0, 0.0],   # eye 1
    [3.2, -1.0, 0.0],    # eye 2
    [0.0, 1.5, 2.0],     # nose tip sticks out of the face plane
    [-2.3, 4.0, 0.3],    # mouth corner 1
    [2.3, 4.0, 0.3],     # mouth corner 2
])


def project(points, yaw_deg=0.0, pitch_deg=0.0, f=800.0, dist=60.0, shift=(320, 240)):
    y, p = np.radians(yaw_deg), np.radians(pitch_deg)
    ry = np.array([[np.cos(y), 0, np.sin(y)], [0, 1, 0], [-np.sin(y), 0, np.cos(y)]])
    rx = np.array([[1, 0, 0], [0, np.cos(p), -np.sin(p)], [0, np.sin(p), np.cos(p)]])
    pts = points @ (rx @ ry).T
    z = dist - pts[:, 2]
    return np.stack([f * pts[:, 0] / z + shift[0], f * pts[:, 1] / z + shift[1]], axis=1)


def flat_photo_landmarks():
    """Landmarks of a printed photo = the frontal projection, then flattened onto a plane."""
    return project(FACE_3D)


def random_affine(pts, rng):
    a = rng.uniform(-0.4, 0.4, (2, 2)) + np.eye(2)
    return pts @ a.T + rng.uniform(-100, 100, 2)


def test_affine_coordinates_invariant_for_flat_photo():
    rng = np.random.RandomState(0)
    base = affine_nose_coordinates(flat_photo_landmarks())
    for _ in range(20):
        moved = affine_nose_coordinates(random_affine(flat_photo_landmarks(), rng))
        assert np.allclose(base, moved, atol=1e-6)


def test_affine_coordinates_change_for_real_head_rotation():
    front = np.array(affine_nose_coordinates(project(FACE_3D, yaw_deg=0)))
    turned = np.array(affine_nose_coordinates(project(FACE_3D, yaw_deg=15)))
    nod = np.array(affine_nose_coordinates(project(FACE_3D, pitch_deg=12)))
    assert np.max(np.abs(turned - front)) > 0.06
    assert np.max(np.abs(nod - front)) > 0.06


SHARP = np.random.RandomState(1).randint(0, 255, (112, 112, 3)).astype(np.uint8)
BLURRY = np.full((112, 112, 3), 128, np.uint8)


def test_live_person_accepted():
    chk = LivenessChecker()  # defaults: 6 frames, spread >= 0.12
    st = LivenessState()
    for i, yaw in enumerate([0, -10, -20, -10, 0, 10, 20, 10]):  # "turn your head left and right"
        chk.update(st, i * 0.5, project(FACE_3D, yaw_deg=yaw), SHARP)
    assert st.decision == LIVE
    assert st.score == 1.0


def test_small_head_motion_is_not_enough():
    chk = LivenessChecker(timeout_seconds=100)
    st = LivenessState()
    for i, yaw in enumerate([0, 3, 5, 3, 0, -3, -5, -3]):
        chk.update(st, i * 0.5, project(FACE_3D, yaw_deg=yaw), SHARP)
    assert st.decision == CHECKING and st.score < 1.0


def test_single_frame_jitter_is_filtered():
    chk = LivenessChecker(timeout_seconds=100)
    st = LivenessState()
    for i in range(10):  # one outlier landmark frame must not be mistaken for a head turn
        chk.update(st, i * 0.5, project(FACE_3D, yaw_deg=30 if i == 4 else 0), SHARP)
    assert st.decision == CHECKING


def test_moving_photo_rejected_after_timeout():
    rng = np.random.RandomState(2)
    chk = LivenessChecker(min_frames=4, motion_threshold=0.06, timeout_seconds=5)
    st = LivenessState()
    for i in range(8):  # photo waved around: big 2-D motion, no 3-D change
        chk.update(st, i * 1.0, random_affine(flat_photo_landmarks(), rng), SHARP)
        if i < 5:
            assert st.decision == CHECKING
    assert st.decision == SPOOF


def test_blurry_replay_rejected():
    chk = LivenessChecker(min_frames=3, min_sharpness=15)
    st = LivenessState()
    for i, yaw in enumerate([0, 10, 20]):
        chk.update(st, i, project(FACE_3D, yaw_deg=yaw), BLURRY)
    assert st.decision == SPOOF


def test_decision_is_sticky():
    chk = LivenessChecker(min_frames=2, motion_threshold=0.06)
    st = LivenessState()
    chk.update(st, 0, project(FACE_3D, yaw_deg=0), SHARP)
    chk.update(st, 1, project(FACE_3D, yaw_deg=20), SHARP)
    assert st.decision == LIVE
    chk.update(st, 100, flat_photo_landmarks(), BLURRY)
    assert st.decision == LIVE


def test_strongly_rolled_frames_are_ignored():
    chk = LivenessChecker(timeout_seconds=100)
    st = LivenessState()
    for i, yaw in enumerate([0, -20, 20, -20, 20, -20, 20, 0]):
        chk.update(st, i, project(FACE_3D, yaw_deg=yaw), SHARP, roll=40.0)
    assert st.samples == [] and st.decision == CHECKING


def test_roll_degrees():
    lm = project(FACE_3D)
    assert abs(roll_degrees(lm)) < 1e-6
    theta = np.radians(25)
    rot = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    assert roll_degrees(lm @ rot.T) == pytest.approx(25, abs=1e-6)
    assert roll_degrees(lm[[1, 0, 2, 4, 3]]) == pytest.approx(0, abs=1e-6)  # eye order does not matter
