"""Simulate printed-photo presentation attacks against the liveness checker.

A face photo is "held in front of the camera" and moved along a smooth random
trajectory (in-plane rotation, scaling, translation and out-of-plane tilt of the
paper, plus sensor noise). Every simulated attack is fed frame by frame through the
same detector + LivenessChecker that the live system uses, and we count how many
attacks are (wrongly) accepted as live.

    python scripts/simulate_photo_attack.py photo1.jpg photo2.jpg --trials 20

This complements - it does not replace - testing with real printed photos, phone
screens and video replays (scripts/evaluate_liveness.py).
"""
from __future__ import annotations

import argparse
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

LEVELS = {  # max in-plane rotation (deg), max corner displacement for tilt (px)
    "gentle": dict(rot=10, tilt=20),
    "moderate": dict(rot=20, tilt=40),
    "aggressive": dict(rot=35, tilt=60),
}


def attack_frames(photo, rng, n, rot, tilt, fps, scale=(0.75, 1.15), shift=50, canvas=(640, 480)):
    h, w = photo.shape[:2]
    phase = rng.uniform(0, 2 * np.pi, 6)
    freq = rng.uniform(0.2, 0.7, 6) / fps  # 0.2-0.7 Hz hand motion
    src = np.float32([[0, 0], [canvas[0], 0], [canvas[0], canvas[1]], [0, canvas[1]]])
    for i in range(n):
        s = np.sin(2 * np.pi * freq * i + phase)
        m = cv2.getRotationMatrix2D((w / 2, h / 2), rot * s[0], 0.8 * np.interp(s[1], [-1, 1], scale))
        m[:, 2] += [(canvas[0] - w) / 2 + shift * s[2], (canvas[1] - h) / 2 + shift * s[3]]
        img = cv2.warpAffine(photo, m, canvas, borderValue=(90, 90, 90))
        d = 0.5 * tilt * np.float32([[s[4], s[5]], [-s[4], -s[5]], [s[4], -s[5]], [-s[4], s[5]]])
        img = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, src + d), canvas)
        yield i / fps, np.clip(img + rng.normal(0, 3, img.shape), 0, 255).astype(np.uint8)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("photos", nargs="+", type=Path)
    ap.add_argument("--trials", type=int, default=20)
    ap.add_argument("--fps", type=float, default=2.0)
    ap.add_argument("--threshold", type=float, default=settings.liveness_motion_threshold)
    args = ap.parse_args()

    backend = OpenCVBackend(settings.models_dir, settings.detection_score_threshold, settings.min_face_size)
    n_frames = int(settings.liveness_timeout_seconds * args.fps) + 2
    print(f"threshold={args.threshold}  frames/attack={n_frames}  fps={args.fps}")
    for level, cfg in LEVELS.items():
        accepted = total = 0
        for photo_path in args.photos:
            photo = cv2.imread(str(photo_path))
            if photo is None:
                print("cannot read", photo_path)
                continue
            for trial in range(args.trials):
                rng = np.random.RandomState(trial)
                checker = LivenessChecker(settings.liveness_min_frames, args.threshold,
                                          settings.liveness_timeout_seconds, settings.liveness_min_sharpness)
                state = LivenessState()
                for t, frame in attack_frames(photo, rng, n_frames, fps=args.fps, **cfg):
                    faces = backend.detect(frame)
                    if not faces:
                        continue
                    face = max(faces, key=lambda f: f.bbox[2] * f.bbox[3])
                    checker.update(state, t, backend.liveness_landmarks(frame, face), crop(frame, face.bbox),
                                   roll=roll_degrees(face.landmarks))
                    if state.decision != "checking":
                        break
                total += 1
                accepted += state.decision == LIVE
        print(f"{level:10s} rot<={cfg['rot']:>2} deg tilt<={cfg['tilt']:>2}px : accepted as live "
              f"{accepted}/{total} = {100 * accepted / max(total, 1):.1f}%  (APCER)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
