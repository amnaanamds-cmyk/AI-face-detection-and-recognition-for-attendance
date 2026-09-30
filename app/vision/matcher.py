"""Similarity matching of a query embedding against the registered gallery."""
from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np


@dataclass
class MatchResult:
    student_id: int | None   # None -> unknown person
    similarity: float        # cosine similarity of the best match
    second_best: float       # best similarity among *other* students
    reason: str = ""         # why the match was rejected (if it was)


class Gallery:
    """In-memory matrix of all registered embeddings (one row per template)."""

    def __init__(self, embeddings: np.ndarray | None = None, labels: list[int] | None = None):
        if embeddings is None or len(embeddings) == 0:
            self.matrix = np.zeros((0, 0), dtype=np.float32)
            self.labels = np.zeros((0,), dtype=np.int64)
        else:
            m = np.asarray(embeddings, dtype=np.float32)
            m /= np.linalg.norm(m, axis=1, keepdims=True).clip(min=1e-12)
            self.matrix = m
            self.labels = np.asarray(labels, dtype=np.int64)

    def __len__(self) -> int:
        return len(self.labels)

    def per_student_scores(self, query: np.ndarray) -> dict[int, float]:
        if len(self) == 0 or query.shape[-1] != self.matrix.shape[1]:
            return {}
        sims = self.matrix @ query.astype(np.float32)
        scores: dict[int, float] = {}
        for label, s in zip(self.labels.tolist(), sims.tolist()):
            if s > scores.get(label, -2.0):
                scores[label] = s
        return scores

    def match(self, query: np.ndarray, threshold: float, margin: float = 0.0) -> MatchResult:
        scores = self.per_student_scores(query)
        if not scores:
            return MatchResult(None, 0.0, 0.0, "empty gallery")
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        best_id, best = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else -1.0
        if best < threshold:
            return MatchResult(None, best, second, "below threshold")
        if len(ranked) > 1 and best - second < margin:
            return MatchResult(None, best, second, "ambiguous")
        return MatchResult(best_id, best, second)


class GalleryCache:
    """Caches one gallery per organization; invalidated whenever faces change."""

    def __init__(self):
        self._galleries: dict[int, Gallery] = {}
        self._lock = threading.Lock()

    def invalidate(self, org_id: int | None = None) -> None:
        with self._lock:
            if org_id is None:
                self._galleries.clear()
            else:
                self._galleries.pop(org_id, None)

    def get(self, org_id: int, loader) -> Gallery:
        with self._lock:
            if org_id not in self._galleries:
                self._galleries[org_id] = loader()
            return self._galleries[org_id]


gallery_cache = GalleryCache()
