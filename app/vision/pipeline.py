"""Real-time multi-face recognition pipeline.

frame -> detect all faces -> track faces across frames (IoU) -> per face:
    align + embed -> match against gallery -> vote over several frames
    liveness update (3-D motion + sharpness)
A track becomes *confirmed* when the same student won ``votes_required`` of the
last few frames; attendance is only marked for confirmed AND live tracks.
"""
from __future__ import annotations

import itertools
import time
from collections import Counter, deque
from dataclasses import dataclass, field

import numpy as np

from app.vision.base import DetectedFace, VisionBackend, crop
from app.vision.liveness import CHECKING, LIVE, LivenessChecker, LivenessState, roll_degrees
from app.vision.matcher import Gallery

_track_ids = itertools.count(1)


def iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


@dataclass
class Track:
    id: int
    bbox: tuple[int, int, int, int]
    first_seen: float
    last_seen: float
    votes: deque = field(default_factory=lambda: deque(maxlen=5))  # (student_id | None, similarity)
    liveness: LivenessState = field(default_factory=LivenessState)
    marked: bool = False  # attendance already handled for this track
    outcome: str = ""     # last attendance message shown for this track
    final_state: str = ""  # marked | checked_out | duplicate | rejected (after the attendance decision)
    logged: set = field(default_factory=set)  # audit events already written for this track
    embedding: np.ndarray | None = None      # running mean embedding of this face

    def consensus(self, required: int) -> tuple[int | None, float]:
        ids = [sid for sid, _ in self.votes if sid is not None]
        if not ids:
            return None, 0.0
        sid, count = Counter(ids).most_common(1)[0]
        if count < required:
            return None, 0.0
        sims = [s for v, s in self.votes if v == sid]
        return sid, float(np.mean(sims))


class FaceTracker:
    """Greedy IoU tracker - enough for a mostly static classroom camera.

    Box overlap alone cannot tell two people apart when one student leaves and another
    sits in the same place, so the pipeline also splits a track when the face embedding
    changes to a different person (see ``split``).
    """

    def __init__(self, iou_threshold: float = 0.3, max_age: float = 3.0):
        self.iou_threshold = iou_threshold
        self.max_age = max_age
        self.tracks: dict[int, Track] = {}

    def update(self, faces: list[DetectedFace], t: float) -> list[tuple[Track, DetectedFace]]:
        self.tracks = {k: tr for k, tr in self.tracks.items() if t - tr.last_seen <= self.max_age}
        pairs = sorted(
            ((iou(tr.bbox, f.bbox), tid, i) for tid, tr in self.tracks.items() for i, f in enumerate(faces)),
            reverse=True,
        )
        used_t, used_f, assigned = set(), set(), {}
        for score, tid, i in pairs:
            if score < self.iou_threshold or tid in used_t or i in used_f:
                continue
            used_t.add(tid)
            used_f.add(i)
            assigned[i] = self.tracks[tid]
        out = []
        for i, f in enumerate(faces):
            tr = assigned.get(i)
            if tr is None:
                tr = Track(id=next(_track_ids), bbox=f.bbox, first_seen=t, last_seen=t)
                self.tracks[tr.id] = tr
            tr.bbox, tr.last_seen = f.bbox, t
            out.append((tr, f))
        return out

    def split(self, track: "Track", t: float) -> "Track":
        """Replace ``track`` by a fresh one (a different person now occupies this position)."""
        self.tracks.pop(track.id, None)
        new = Track(id=next(_track_ids), bbox=track.bbox, first_seen=t, last_seen=t)
        self.tracks[new.id] = new
        return new


# Below this cosine similarity between consecutive embeddings of one track, the face is
# treated as a different person (same person across frames: typically > 0.6).
SAME_TRACK_MIN_SIMILARITY = 0.3


@dataclass
class FaceResult:
    track_id: int
    bbox: tuple[int, int, int, int]
    detection_score: float
    candidate_id: int | None     # best match in this frame
    similarity: float
    student_id: int | None       # confirmed identity (after voting)
    confirmed_similarity: float
    liveness: str                # checking | live | spoof | disabled
    liveness_score: float
    track: Track = field(repr=False)


class RecognitionPipeline:
    def __init__(
        self,
        backend: VisionBackend,
        liveness: LivenessChecker,
        threshold: float,
        margin: float,
        votes_required: int,
    ):
        self.backend = backend
        self.liveness = liveness
        self.threshold = threshold
        self.margin = margin
        self.votes_required = votes_required

    def process(
        self,
        frame: np.ndarray,
        gallery: Gallery,
        tracker: FaceTracker,
        liveness_required: bool = True,
        t: float | None = None,
    ) -> list[FaceResult]:
        t = time.monotonic() if t is None else t
        faces = self.backend.detect(frame)
        results = []
        for track, face in tracker.update(faces, t):
            emb = self.backend.embed(frame, face)
            if track.embedding is not None and float(emb @ track.embedding) < SAME_TRACK_MIN_SIMILARITY:
                track = tracker.split(track, t)
            track.embedding = emb if track.embedding is None else _update_mean(track.embedding, emb)
            m = gallery.match(emb, self.threshold, self.margin)
            track.votes.append((m.student_id, m.similarity))
            sid, conf_sim = track.consensus(self.votes_required)

            if liveness_required:
                lm_fn = getattr(self.backend, "liveness_landmarks", None)
                landmarks = lm_fn(frame, face) if lm_fn and "motion" in self.liveness.mode else face.landmarks
                spoof_fn = getattr(self.backend, "spoof_score", None)
                cnn_live = spoof_fn(frame, face) if spoof_fn and "cnn" in self.liveness.mode else None
                self.liveness.update(track.liveness, t, landmarks, crop(frame, face.bbox),
                                     roll=roll_degrees(face.landmarks), cnn_live=cnn_live)
                live_state, live_score = track.liveness.decision, track.liveness.score
            else:
                live_state, live_score = "disabled", 1.0

            results.append(
                FaceResult(
                    track_id=track.id,
                    bbox=face.bbox,
                    detection_score=face.score,
                    candidate_id=m.student_id,
                    similarity=m.similarity,
                    student_id=sid,
                    confirmed_similarity=conf_sim,
                    liveness=live_state,
                    liveness_score=live_score,
                    track=track,
                )
            )
        return results


def _update_mean(mean: np.ndarray, new: np.ndarray, alpha: float = 0.3) -> np.ndarray:
    v = (1 - alpha) * mean + alpha * new
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else new


def is_accepted(result: FaceResult) -> bool:
    return result.student_id is not None and result.liveness in (LIVE, "disabled")


__all__ = ["CHECKING", "FaceResult", "FaceTracker", "RecognitionPipeline", "Track", "is_accepted", "iou"]
