"""Test an anti-spoofing model exactly the way the product runs it.

Each photo/video frame in a raw-layout folder (see prepare.py) goes through the product's own
pipeline: YuNet detection -> crop -> ONNX model via OpenCV -> live probability. Use recordings
that were NOT used for training (ideally a different room, camera and set of people).

    python training/antispoof/evaluate.py --raw test_raw --models models
    python training/antispoof/evaluate.py --raw test_raw --models runs/v1 --threshold 0.6
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.vision.backends import SFACE_FILE, YUNET_FILE, OpenCVBackend  # noqa: E402
from metrics import pad_metrics  # noqa: E402
from prepare import IMAGE_EXT, VIDEO_EXT, frames  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--models", type=Path, default=ROOT / "models",
                    help="folder with antispoof.onnx + antispoof.json (detector models are taken from --detector if absent)")
    ap.add_argument("--detector", type=Path, default=ROOT / "models", help="folder with the YuNet/SFace models")
    ap.add_argument("--threshold", type=float, default=0.7)
    ap.add_argument("--every", type=int, default=5)
    args = ap.parse_args()

    models = args.models
    if not (models / YUNET_FILE).exists():  # a training run folder: borrow the detector from models/
        import shutil
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        for f in (YUNET_FILE, SFACE_FILE):
            shutil.copy(args.detector / f, tmp / f)
        for f in ("antispoof.onnx", "antispoof.json"):
            if (models / f).exists():
                shutil.copy(models / f, tmp / f)
        models = tmp
    backend = OpenCVBackend(models)
    if not backend.has_antispoof:
        sys.exit(f"no anti-spoofing model in {args.models}")
    print(f"model {backend.antispoof_model}  config {backend.antispoof_config}")
    scores, live, per_class = [], [], {}
    for cls_dir in sorted(d for d in args.raw.iterdir() if d.is_dir()):
        for f in sorted(cls_dir.rglob("*")):
            if f.suffix.lower() not in IMAGE_EXT | VIDEO_EXT:
                continue
            for _, img in frames(f, args.every):
                faces = backend.detect(img)
                if not faces:
                    continue
                face = max(faces, key=lambda d: d.bbox[2] * d.bbox[3])
                s = backend.spoof_score(img, face)
                scores.append(s)
                live.append(cls_dir.name == "live")
                per_class.setdefault(cls_dir.name, []).append(s)
    if not scores:
        sys.exit("no faces found")
    for cls, s in per_class.items():
        s = np.array(s)
        ok = (s >= args.threshold) if cls == "live" else (s < args.threshold)
        print(f"{cls:10s} n={len(s):5d}  mean live-prob {s.mean():.3f}  correctly classified {ok.mean():.1%}")
    m = pad_metrics(np.array(scores), np.array(live), args.threshold)
    print(json.dumps(m, indent=2))


if __name__ == "__main__":
    main()
