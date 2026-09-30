"""Turn raw photos/videos into face crops for anti-spoofing training.

Input layout (one sub-folder per person or recording session; use the same folder name for the
same person in every class - the train/val split is made by that name, so a person is never in both):

    raw/
      live/    <session>/  *.jpg | *.png | *.mp4 ...   real people in front of the camera
      print/   <session>/  ...                          printed photos held up to the camera
      replay/  <session>/  ...                          photos/videos shown on a phone, tablet or monitor

Any other class folder (e.g. mask/) is used as an extra attack class. Faces are found with the
same YuNet detector the product uses and cropped exactly like the product crops them at
check-in time (app.vision.backends.crop_face), so what the model learns matches what it sees.

    python training/antispoof/prepare.py --raw raw --out data --size 128 --scale 1.5
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.vision.backends import OpenCVBackend, crop_face  # noqa: E402

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
VIDEO_EXT = {".mp4", ".avi", ".mov", ".mkv", ".webm"}


def frames(path: Path, every: int):
    if path.suffix.lower() in IMAGE_EXT:
        img = cv2.imread(str(path))
        if img is not None:
            yield path.stem, img
        return
    cap = cv2.VideoCapture(str(path))
    i = 0
    while True:
        ok, img = cap.read()
        if not ok:
            break
        if i % every == 0:
            yield f"{path.stem}_f{i:05d}", img
        i += 1
    cap.release()


def split_of(session: str, val_fraction: float) -> str:
    """Deterministic split by recording session (hash), so reruns give the same split."""
    h = int(hashlib.sha1(session.encode()).hexdigest(), 16) % 1000
    return "val" if h < val_fraction * 1000 else "train"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--size", type=int, default=128, help="network input size (default 128)")
    ap.add_argument("--scale", type=float, default=1.5, help="crop size relative to the face box (default 1.5)")
    ap.add_argument("--val-fraction", type=float, default=0.2)
    ap.add_argument("--every", type=int, default=5, help="use every N-th video frame")
    ap.add_argument("--min-face", type=int, default=60, help="skip faces smaller than this (pixels)")
    ap.add_argument("--models", type=Path, default=ROOT / "models")
    args = ap.parse_args()

    backend = OpenCVBackend(args.models, min_face_size=args.min_face)
    classes = sorted(d.name for d in args.raw.iterdir() if d.is_dir())
    if "live" not in classes:
        sys.exit("raw/ must contain a 'live' folder")
    counts: dict[tuple[str, str], int] = {}
    skipped = 0
    for cls in classes:
        for f in sorted((args.raw / cls).rglob("*")):
            if f.suffix.lower() not in IMAGE_EXT | VIDEO_EXT:
                continue
            rel = f.relative_to(args.raw / cls)
            # split by session/person folder name (shared across classes), so one person's live and
            # attack recordings are never divided between train and val
            session = rel.parts[0] if len(rel.parts) > 1 else f"{cls}/{f.stem}"
            split = split_of(session, args.val_fraction)
            dest = args.out / split / cls
            dest.mkdir(parents=True, exist_ok=True)
            for name, img in frames(f, args.every):
                faces = backend.detect(img)
                if not faces:
                    skipped += 1
                    continue
                face = max(faces, key=lambda d: d.bbox[2] * d.bbox[3])
                crop = crop_face(img, face.bbox, args.scale, args.size)
                tag = "_".join(rel.parts[:-1] + (name,))
                cv2.imwrite(str(dest / f"{tag}.png"), crop)
                counts[(split, cls)] = counts.get((split, cls), 0) + 1
    for (split, cls), n in sorted(counts.items()):
        print(f"{split:5s} {cls:10s} {n:6d}")
    print(f"frames without a usable face: {skipped}")
    (args.out / "prepare.json").write_text(json.dumps({"input_size": args.size, "crop_scale": args.scale}))


if __name__ == "__main__":
    main()
