"""Evaluate liveness (anti-spoofing) on recorded clips.

Layout - each clip is a video file or a folder of frames:

    liveness_data/
      real/   amina_1.mp4  ali_1.mp4  ...          (live students turning their head)
      spoof/  photo_print_1.mp4  phone_replay_1/   (printed photos, phone/laptop screens)

Metrics (ISO/IEC 30107-3 terminology):
  APCER  = attack presentations wrongly accepted as live
  BPCER  = bona-fide (real) presentations wrongly rejected
  ACER   = (APCER + BPCER) / 2
plus the average time until a real person is accepted.

    python scripts/evaluate_liveness.py --data liveness_data --fps 2
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.vision.backends import OpenCVBackend  # noqa: E402
from app.vision.base import crop  # noqa: E402
from app.vision.liveness import LIVE, LivenessChecker, LivenessState, roll_degrees  # noqa: E402

VIDEO_EXT = {".mp4", ".avi", ".mov", ".mkv", ".webm"}
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp"}


def frames(clip: Path, fps: float):
    """Yield (t, frame) sampled at ~fps, like the browser does in live mode."""
    if clip.is_dir():
        for i, p in enumerate(sorted(p for p in clip.iterdir() if p.suffix.lower() in IMG_EXT)):
            yield i / fps, cv2.imread(str(p))
        return
    cap = cv2.VideoCapture(str(clip))
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(round(src_fps / fps)))
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i % step == 0:
            yield i / src_fps, frame
        i += 1
    cap.release()


def run_clip(backend, checker: LivenessChecker, clip: Path, fps: float) -> tuple[str, float | None, float]:
    state = LivenessState()
    decided_at = None
    for t, frame in frames(clip, fps):
        faces = backend.detect(frame)
        if not faces:
            continue
        face = max(faces, key=lambda f: f.bbox[2] * f.bbox[3])
        checker.update(state, t, backend.liveness_landmarks(frame, face), crop(frame, face.bbox),
                       roll=roll_degrees(face.landmarks))
        if state.decision != "checking" and decided_at is None:
            decided_at = t
            break
    return state.decision, decided_at, state.score


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, type=Path)
    ap.add_argument("--fps", type=float, default=2.0)
    ap.add_argument("--threshold", type=float, default=settings.liveness_motion_threshold)
    ap.add_argument("--out", type=Path, default=Path("results/liveness.json"))
    args = ap.parse_args()

    backend = OpenCVBackend(settings.models_dir, settings.detection_score_threshold, settings.min_face_size)
    checker = LivenessChecker(settings.liveness_min_frames, args.threshold, settings.liveness_timeout_seconds,
                              settings.liveness_min_sharpness)
    rows = []
    for kind in ("real", "spoof"):
        d = args.data / kind
        if not d.exists():
            continue
        for clip in sorted(d.iterdir()):
            if not (clip.is_dir() or clip.suffix.lower() in VIDEO_EXT):
                continue
            decision, t, score = run_clip(backend, checker, clip, args.fps)
            rows.append({"clip": str(clip), "kind": kind, "decision": decision, "decided_at_s": t, "score": score})
            print(f"{kind:5s} {decision:8s} t={t if t is not None else '-':>5} score={score:.2f}  {clip.name}")

    real = [r for r in rows if r["kind"] == "real"]
    spoof = [r for r in rows if r["kind"] == "spoof"]
    apcer = sum(r["decision"] == LIVE for r in spoof) / len(spoof) if spoof else None
    bpcer = sum(r["decision"] != LIVE for r in real) / len(real) if real else None
    times = [r["decided_at_s"] for r in real if r["decision"] == LIVE and r["decided_at_s"] is not None]
    summary = {"APCER": apcer, "BPCER": bpcer,
               "ACER": (apcer + bpcer) / 2 if apcer is not None and bpcer is not None else None,
               "mean_time_to_accept_s": float(np.mean(times)) if times else None,
               "threshold": args.threshold, "clips": rows}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k != "clips"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
