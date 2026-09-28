"""Vision backends.

OpenCVBackend
    * Detection: **YuNet** (CNN face detector, ~230 KB ONNX, real-time on CPU,
      returns a box + 5 facial landmarks per face, handles many faces per frame).
    * Alignment: similarity transform of the 5 landmarks to a canonical 112x112 face.
    * Embedding: **SFace** (MobileFaceNet-style network trained with an ArcFace-like
      margin loss) -> 128-D feature vector compared with cosine similarity.

FakeBackend
    Deterministic stand-in used by the unit tests so the full system can be tested
    without downloading models or having a camera.
"""
from __future__ import annotations

import threading
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from app.config import settings
from app.vision.base import DetectedFace, VisionBackend, l2_normalize

YUNET_FILE = "face_detection_yunet_2023mar.onnx"
SFACE_FILE = "face_recognition_sface_2021dec.onnx"


class ModelsMissingError(RuntimeError):
    pass


class OpenCVBackend:
    name = "sface-2021dec"
    embedding_dim = 128

    def __init__(self, models_dir: Path, score_threshold: float = 0.85, min_face_size: int = 40):
        det_path = models_dir / YUNET_FILE
        rec_path = models_dir / SFACE_FILE
        missing = [p.name for p in (det_path, rec_path) if not p.exists()]
        if missing:
            raise ModelsMissingError(
                f"Missing model files {missing} in {models_dir}. Run: python scripts/download_models.py"
            )
        self.min_face_size = min_face_size
        self._detector = cv2.FaceDetectorYN.create(str(det_path), "", (320, 320), score_threshold, 0.3, 5000)
        self._recognizer = cv2.FaceRecognizerSF.create(str(rec_path), "")
        # cv2.dnn networks are not thread-safe; FastAPI may call us from several threads.
        self._lock = threading.Lock()

    def detect(self, image: np.ndarray) -> list[DetectedFace]:
        h, w = image.shape[:2]
        with self._lock:
            self._detector.setInputSize((w, h))
            _, faces = self._detector.detect(image)
        results: list[DetectedFace] = []
        if faces is None:
            return results
        for row in faces:
            x, y, fw, fh = (int(round(v)) for v in row[:4])
            if min(fw, fh) < self.min_face_size:
                continue
            results.append(
                DetectedFace(
                    bbox=(x, y, fw, fh),
                    score=float(row[14]),
                    landmarks=row[4:14].reshape(5, 2).astype(np.float32),
                    raw=row,
                )
            )
        return results

    def embed(self, image: np.ndarray, face: DetectedFace) -> np.ndarray:
        with self._lock:
            aligned = self._recognizer.alignCrop(image, face.raw)
            feat = self._recognizer.feature(aligned)
        return l2_normalize(feat)

    def liveness_landmarks(self, image: np.ndarray, face: DetectedFace, size: int = 256, eye_dist: float = 70.0) -> np.ndarray:
        """Landmarks re-detected on an eye-aligned, scale-normalised crop.

        YuNet's landmark predictions are slightly biased by in-plane rotation and scale,
        which would make a photo that is merely rotated/moved look "3-D". Warping every
        face to the same canonical pose first removes that bias (measured: spread for a
        moved photo drops from ~0.2-0.3 to ~0.07, see docs/EVALUATION.md).
        """
        lm = face.landmarks
        e1, e2 = lm[0], lm[1]
        centre, v = (e1 + e2) / 2.0, e2 - e1
        dist = float(np.linalg.norm(v))
        if dist < 1:
            return lm
        angle = float(np.degrees(np.arctan2(v[1], v[0])))
        m = cv2.getRotationMatrix2D((float(centre[0]), float(centre[1])), angle, eye_dist / dist)
        m[:, 2] += np.array([size / 2.0, size * 0.4]) - centre
        canon = cv2.warpAffine(image, m, (size, size), borderMode=cv2.BORDER_REPLICATE)
        with self._lock:
            self._detector.setInputSize((size, size))
            _, faces = self._detector.detect(canon)
        if faces is None:
            return lm
        best = max(faces, key=lambda r: r[2] * r[3])
        return best[4:14].reshape(5, 2).astype(np.float32)


class FakeBackend:
    """Treats the whole (non-blank) image as one face.

    Embedding = normalised 16x16 grayscale thumbnail, so identical/similar synthetic
    images match and different ones do not. The "nose" landmark follows the image's
    brightness centroid, which lets tests simulate a moving (live) vs. static face.
    """

    name = "fake"
    embedding_dim = 256

    def detect(self, image: np.ndarray) -> list[DetectedFace]:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        if float(gray.std()) < 2.0:  # blank frame -> no face
            return []
        h, w = gray.shape
        m = cv2.moments(gray.astype(np.float32))
        cx = m["m10"] / m["m00"] if m["m00"] else w / 2
        cy = m["m01"] / m["m00"] if m["m00"] else h / 2
        landmarks = np.array(
            [[0.35 * w, 0.4 * h], [0.65 * w, 0.4 * h], [cx, cy], [0.4 * w, 0.75 * h], [0.6 * w, 0.75 * h]],
            dtype=np.float32,
        )
        return [DetectedFace(bbox=(0, 0, w, h), score=0.99, landmarks=landmarks)]

    def embed(self, image: np.ndarray, face: DetectedFace) -> np.ndarray:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        thumb = cv2.resize(gray, (16, 16), interpolation=cv2.INTER_AREA).astype(np.float32)
        thumb -= thumb.mean()
        return l2_normalize(thumb)


_override: VisionBackend | None = None


def set_backend(backend: VisionBackend | None) -> None:
    """Inject a backend (tests)."""
    global _override
    _override = backend
    _default_backend.cache_clear()


@lru_cache(maxsize=1)
def _default_backend() -> VisionBackend:
    if settings.vision_backend == "fake":
        return FakeBackend()
    return OpenCVBackend(settings.models_dir, settings.detection_score_threshold, settings.min_face_size)


def get_backend() -> VisionBackend:
    return _override or _default_backend()
