"""Presentation-attack-detection metrics (ISO/IEC 30107-3). No torch needed.

APCER  attack presentations wrongly accepted as live   (security: lower = harder to fool)
BPCER  live people wrongly rejected as attacks          (convenience: lower = fewer retries)
ACER   (APCER + BPCER) / 2
EER    error rate where APCER == BPCER (threshold-independent summary)
"""
from __future__ import annotations

import numpy as np


def pad_metrics(live_prob: np.ndarray, is_live: np.ndarray, threshold: float) -> dict[str, float]:
    live_prob, is_live = np.asarray(live_prob, float), np.asarray(is_live, bool)
    attacks, lives = live_prob[~is_live], live_prob[is_live]
    apcer = float((attacks >= threshold).mean()) if len(attacks) else 0.0
    bpcer = float((lives < threshold).mean()) if len(lives) else 0.0
    eer, eer_t = 1.0, 0.5
    if len(attacks) and len(lives):
        best = None
        for t in np.unique(np.concatenate([live_prob, [0.0, 1.0]])):
            a, b = (attacks >= t).mean(), (lives < t).mean()
            if best is None or abs(a - b) < best:
                best, eer, eer_t = abs(a - b), (a + b) / 2, float(t)
    return {"apcer": apcer, "bpcer": bpcer, "acer": (apcer + bpcer) / 2, "eer": float(eer), "eer_threshold": eer_t}
