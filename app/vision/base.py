"""Common types for the computer-vision pipeline."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np


@dataclass
class DetectedFace:
    bbox: tuple[int, int, int, int]  # x, y, w, h (pixels)
    score: float                     # detector confidence 0..1
    landmarks: np.ndarray            # (5, 2): right eye, left eye, nose tip, right mouth, left mouth
    raw: np.ndarray | None = None    # backend-specific detection row (YuNet: 15 floats)


class VisionBackend(Protocol):
    """Detection + alignment + embedding. Implementations: OpenCVBackend, FakeBackend."""

    name: str
    embedding_dim: int

    def detect(self, image: np.ndarray) -> list[DetectedFace]: ...

    def embed(self, image: np.ndarray, face: DetectedFace) -> np.ndarray:
        """Align the face and return an L2-normalised embedding vector."""
        ...


def l2_normalize(vec: np.ndarray) -> np.ndarray:
    vec = np.asarray(vec, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0 else vec


def decode_image(data: bytes | str) -> np.ndarray:
    """Decode JPEG/PNG bytes or a ``data:image/...;base64,`` URL into a BGR image."""
    if isinstance(data, str):
        if data.startswith("data:"):
            data = data.split(",", 1)[1]
        data = base64.b64decode(data)
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Could not decode image")
    return img


def crop(image: np.ndarray, bbox: tuple[int, int, int, int]) -> np.ndarray:
    x, y, w, h = bbox
    H, W = image.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    return image[y0:y1, x0:x1]
