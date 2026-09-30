"""Vision backends.

OpenCVBackend
    * Detection: **YuNet** (CNN face detector, ~230 KB ONNX, real-time on CPU,
      returns a box + 5 facial landmarks per face, handles many faces per frame).
    * Alignment: similarity transform of the 5 landmarks to a canonical 112x112 face.
    * Embedding: **SFace** (MobileFaceNet-style network trained with the SFace sigmoid-constrained
      hypersphere loss, an angular-margin loss of the ArcFace family) -> 128-D feature vector compared with cosine similarity.

FakeBackend
    Deterministic stand-in used by the unit tests so the full system can be tested
    without downloading models or having a camera.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from app.config import settings
from app.vision.base import DetectedFace, VisionBackend, l2_normalize

YUNET_FILE = "face_detection_yunet_2023mar.onnx"
SFACE_FILE = "face_recognition_sface_2021dec.onnx"
# Optional anti-spoofing CNN. Preferred: your own model trained with training/antispoof/
# (antispoof.onnx + antispoof.json describing its pre-processing). Fallback: the research
# model (MiniFASNet trained on CelebA-Spoof, non-commercial), downloaded only with
# scripts/download_models.py --include-research-antispoof.
CUSTOM_ANTISPOOF_FILE = "antispoof.onnx"
ANTISPOOF_FILE = "AntiSpoofing_print-replay_1.5_128.onnx"


@dataclass(frozen=True)
class AntiSpoofConfig:
    """How to feed a face to an anti-spoofing model (must match how it was trained)."""
    input_size: int = 128
    crop_scale: float = 1.5
    live_index: int = 0
    rgb: bool = True

    @classmethod
    def load(cls, sidecar: Path) -> "AntiSpoofConfig":
        if not sidecar.exists():
            return cls()
        data = json.loads(sidecar.read_text())
        return cls(input_size=int(data.get("input_size", 128)), crop_scale=float(data.get("crop_scale", 1.5)),
                   live_index=int(data.get("live_index", 0)), rgb=bool(data.get("rgb", True)))


def crop_face(image: np.ndarray, bbox, scale: float, size: int) -> np.ndarray:
    """Square crop of `scale` x the face box, centred on the face, zero padded, resized to size x size.

    Shared by the product and by training/antispoof/prepare.py so training and inference see
    exactly the same kind of crop.
    """
    x, y, w, h = bbox
    side = max(int(max(w, h) * scale), 1)
    x0, y0 = int(x + w / 2 - side / 2), int(y + h / 2 - side / 2)
    padded = cv2.copyMakeBorder(image, side, side, side, side, cv2.BORDER_CONSTANT, value=0)
    patch = padded[y0 + side:y0 + 2 * side, x0 + side:x0 + 2 * side]
    return cv2.resize(patch, (size, size), interpolation=cv2.INTER_LINEAR)


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
        self._antispoof, self.antispoof_config, self.antispoof_model = None, AntiSpoofConfig(), None
        for fname in (CUSTOM_ANTISPOOF_FILE, ANTISPOOF_FILE):
            spoof_path = models_dir / fname
            if spoof_path.exists():
                self._antispoof = cv2.dnn.readNetFromONNX(str(spoof_path))
                self.antispoof_config = AntiSpoofConfig.load(spoof_path.with_suffix(".json"))
                self.antispoof_model = fname
                break
        # cv2.dnn networks are not thread-safe; FastAPI may call us from several threads.
        self._lock = threading.Lock()

    # YuNet is trained for small/medium faces: faces wider than ~450 px (phone photos,
    # a student close to the webcam) get low scores and are missed at full resolution.
    # We therefore detect on a 640 px copy (large faces) and, for bigger frames, also at
    # full resolution up to 1920 px (small faces at the back of a classroom), then merge.
    COARSE_SIDE = 640
    FINE_MAX_SIDE = 1920

    def _detect_at(self, image: np.ndarray, scale: float) -> np.ndarray:
        img = image if scale == 1.0 else cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        h, w = img.shape[:2]
        with self._lock:
            self._detector.setInputSize((w, h))
            _, faces = self._detector.detect(img)
        if faces is None:
            return np.zeros((0, 15), np.float32)
        faces = faces.copy()
        faces[:, :14] /= scale  # boxes and landmarks back to original pixel coordinates
        return faces

    def detect(self, image: np.ndarray) -> list[DetectedFace]:
        long_side = max(image.shape[:2])
        rows = [self._detect_at(image, min(1.0, self.COARSE_SIDE / long_side))]
        if long_side > self.COARSE_SIDE:
            rows.append(self._detect_at(image, min(1.0, self.FINE_MAX_SIDE / long_side)))
        faces = np.concatenate(rows)
        if len(faces) == 0:
            return []
        keep = cv2.dnn.NMSBoxes(faces[:, :4].tolist(), faces[:, 14].tolist(), 0.0, 0.3)
        results: list[DetectedFace] = []
        for i in np.array(keep).reshape(-1):
            row = faces[i]
            x, y, fw, fh = (int(round(v)) for v in row[:4])
            if min(fw, fh) < self.min_face_size:
                continue
            results.append(DetectedFace(bbox=(x, y, fw, fh), score=float(row[14]),
                                        landmarks=row[4:14].reshape(5, 2).astype(np.float32), raw=row))
        results.sort(key=lambda f: f.bbox[0])
        return results

    def embed(self, image: np.ndarray, face: DetectedFace) -> np.ndarray:
        with self._lock:
            aligned = self._recognizer.alignCrop(image, face.raw)
            feat = self._recognizer.feature(aligned)
        return l2_normalize(feat)

    @property
    def has_antispoof(self) -> bool:
        return self._antispoof is not None

    def spoof_score(self, image: np.ndarray, face: DetectedFace) -> float | None:
        """Probability (0..1) that the face is a live person and not a print/screen.

        Pre-processing matches the model's training (see AntiSpoofConfig): square crop of
        `crop_scale` x the face box centred on the face (zero padded), RGB, resized, scaled to [0, 1].
        """
        if self._antispoof is None:
            return None
        cfg = self.antispoof_config
        patch = crop_face(image, face.bbox, cfg.crop_scale, cfg.input_size)
        blob = cv2.dnn.blobFromImage(patch, 1 / 255.0, (cfg.input_size, cfg.input_size), swapRB=cfg.rgb)
        with self._lock:
            self._antispoof.setInput(blob)
            logits = self._antispoof.forward()[0].astype(np.float64)
        prob = np.exp(logits - logits.max())
        return float(prob[cfg.live_index] / prob.sum())

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
