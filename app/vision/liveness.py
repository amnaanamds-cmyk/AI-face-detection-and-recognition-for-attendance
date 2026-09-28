"""Liveness (presentation-attack) detection.

Two complementary cues are combined:

1. **3-D structure from motion (primary cue).**
   A printed photo or a phone screen is a *plane*. When a plane moves, tilts or
   rotates in front of a camera, its image changes (approximately) by an *affine*
   transform. Affine transforms preserve the coordinates of one point expressed in
   the frame spanned by three other points. We therefore express the nose tip in
   the frame (left eye, right eye, mouth centre):

       nose = eye_1 + a * (eye_2 - eye_1) + b * (mouth - eye_1)

   For a flat photo, (a, b) stays constant no matter how the photo is moved.
   For a real head, the nose sticks out of the eye/mouth plane, so any natural
   head rotation (yaw/pitch) changes (a, b). A face track is accepted as *live*
   once the spread of (a, b) over time exceeds a threshold; if a face stays
   perfectly "flat" for the whole observation window it is flagged as a spoof.

   Robustness measures (motivated by the measurements in docs/EVALUATION.md):
   landmarks are re-detected on an eye-aligned canonical crop (backend
   ``liveness_landmarks``), a 3-frame running median removes jitter, the spread is
   the 10th-90th percentile range, and strongly rolled frames are ignored.

2. **Image sharpness (secondary cue).**
   Re-captured images (screen replays, printed photos) are often blurred. The
   variance of the Laplacian of the face crop is computed; very low values fail.

Limitations (discussed in docs/EVALUATION.md): a 3-D mask or a *video* replay of
the real student moving their head can defeat cue 1. A deep-learning anti-spoofing
model can be plugged in by implementing ``LivenessChecker`` differently.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

LIVE = "live"
SPOOF = "spoof"
CHECKING = "checking"


def affine_nose_coordinates(landmarks: np.ndarray) -> tuple[float, float]:
    """Return (a, b): nose position in the affine frame (eye1, eye2, mouth centre)."""
    lm = np.asarray(landmarks, dtype=np.float64).reshape(5, 2)
    e1, e2, nose = lm[0], lm[1], lm[2]
    mouth = (lm[3] + lm[4]) / 2.0
    basis = np.column_stack([e2 - e1, mouth - e1])  # 2x2
    if abs(np.linalg.det(basis)) < 1e-6:
        return 0.0, 0.0
    a, b = np.linalg.solve(basis, nose - e1)
    return float(a), float(b)


def roll_degrees(landmarks: np.ndarray) -> float:
    """In-plane rotation of the face (angle of the eye line)."""
    lm = np.asarray(landmarks, dtype=np.float64).reshape(5, 2)
    v = lm[1] - lm[0]
    ang = float(np.degrees(np.arctan2(v[1], v[0])))
    # eye order may be either way round; fold to [-90, 90]
    if ang > 90:
        ang -= 180
    elif ang < -90:
        ang += 180
    return ang


def sharpness(face_crop: np.ndarray) -> float:
    if face_crop.size == 0:
        return 0.0
    gray = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY) if face_crop.ndim == 3 else face_crop
    gray = cv2.resize(gray, (112, 112))
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


@dataclass
class LivenessState:
    samples: list[tuple[float, float, float]] = field(default_factory=list)  # (t, a, b)
    sharpness: list[float] = field(default_factory=list)
    cnn: list[float] = field(default_factory=list)  # anti-spoofing CNN P(live) per frame
    first_seen: float | None = None
    decision: str = CHECKING
    score: float = 0.0
    reason: str = ""


def smoothed(values: np.ndarray) -> np.ndarray:
    """Running median over 3 consecutive frames - removes single-frame landmark jitter."""
    if len(values) < 3:
        return values
    padded = np.concatenate([values[:1], values, values[-1:]])
    return np.median(np.stack([padded[:-2], padded[1:-1], padded[2:]]), axis=0)


def motion_spread(coords: np.ndarray) -> float:
    """Robust spread (10th-90th percentile range) of the smoothed (a, b) track."""
    if len(coords) < 2:
        return 0.0
    sm = smoothed(coords)
    return float(np.max(np.percentile(sm, 90, axis=0) - np.percentile(sm, 10, axis=0)))


MODES = ("cnn", "motion", "cnn+motion")


@dataclass
class LivenessChecker:
    """Combines the cues according to ``mode``:

    * ``cnn``        - passive: anti-spoofing CNN only (no head movement needed)
    * ``motion``     - active: 3-D motion test (student turns head)
    * ``cnn+motion`` - strictest: both must pass
    If the CNN model is not installed, ``cnn`` falls back to ``motion``.
    """

    min_frames: int = 6
    motion_threshold: float = 0.12   # ~20 degrees of head turn (yaw) on an average face
    timeout_seconds: float = 12.0
    min_sharpness: float = 15.0
    # Landmark error grows when a face is strongly rotated in-plane (typical of a photo
    # being waved around, rare for a seated student) - such frames are not used.
    max_roll_degrees: float = 15.0
    mode: str = "motion"
    motion_window: int = 16          # most recent frames used for the motion test (~8 s at 2 fps)
    cnn_threshold: float = 0.7       # median P(live) needed to accept
    cnn_reject: float = 0.3          # median P(live) below this = spoof
    cnn_min_frames: int = 3

    def update(self, state: LivenessState, t: float, landmarks: np.ndarray, face_crop: np.ndarray,
               roll: float | None = None, cnn_live: float | None = None) -> LivenessState:
        if state.decision != CHECKING:
            return state  # decisions are sticky for the life of the track
        if state.first_seen is None:
            state.first_seen = t
        mode = self.mode
        if "cnn" in mode and cnn_live is None and not state.cnn:
            mode = "motion"  # CNN not available -> geometric cue only
        use_cnn, use_motion = "cnn" in mode, "motion" in mode

        if cnn_live is not None:
            state.cnn.append(float(cnn_live))
        rolled = roll is not None and abs(roll) > self.max_roll_degrees
        if not rolled:
            a, b = affine_nose_coordinates(landmarks)
            state.samples.append((t, a, b))
            state.sharpness.append(sharpness(face_crop))

        # sliding window: a student who sat still before turning their head is not penalised
        recent = state.samples[-self.motion_window:]
        motion = motion_spread(np.array([(s[1], s[2]) for s in recent])) if recent else 0.0
        motion_ok = len(state.samples) >= self.min_frames and motion >= self.motion_threshold
        cnn_med = float(np.median(state.cnn)) if state.cnn else 0.0
        cnn_ready = len(state.cnn) >= self.cnn_min_frames
        elapsed = t - state.first_seen

        parts = []
        if use_cnn:
            parts.append(min(1.0, cnn_med / self.cnn_threshold) if self.cnn_threshold > 0 else 1.0)
        if use_motion:
            parts.append(min(1.0, motion / self.motion_threshold) if self.motion_threshold > 0 else 1.0)
        state.score = min(parts)

        if len(state.sharpness) >= self.min_frames and float(np.median(state.sharpness)) < self.min_sharpness:
            state.decision, state.reason = SPOOF, "image too blurred (screen/print re-capture?)"
        elif use_cnn and cnn_ready and cnn_med < self.cnn_reject:
            state.decision, state.reason = SPOOF, f"anti-spoofing model: P(live)={cnn_med:.2f}"
        elif (not use_cnn or (cnn_ready and cnn_med >= self.cnn_threshold)) and (not use_motion or motion_ok):
            state.decision, state.reason = LIVE, ""
        elif elapsed >= self.timeout_seconds and (len(state.samples) >= self.min_frames or cnn_ready):
            state.decision = SPOOF
            state.reason = "no 3-D head movement" if use_motion and not motion_ok else "liveness not confirmed"
        return state
