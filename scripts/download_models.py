"""Download the pre-trained face models from the OpenCV model zoo.

    python scripts/download_models.py [--dir models]

* YuNet  (face detection, ~0.2 MB)   - https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet
* SFace  (face recognition, ~37 MB)  - https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MODELS = {
    "face_detection_yunet_2023mar.onnx": (
        "models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    ),
    "face_recognition_sface_2021dec.onnx": (
        "models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    ),
}
# The zoo stores models with Git LFS; media.githubusercontent.com serves the real file.
MIRRORS = [
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/{path}",
    "https://github.com/opencv/opencv_zoo/raw/main/{path}",
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=str(ROOT / "models"))
    args = ap.parse_args()
    out_dir = Path(args.dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ok = True
    for name, (path, digest) in MODELS.items():
        target = out_dir / name
        if target.exists() and sha256(target) == digest:
            print(f"[ok]   {name} already present")
            continue
        for mirror in MIRRORS:
            url = mirror.format(path=path)
            try:
                print(f"[get]  {url}")
                urllib.request.urlretrieve(url, target)
            except OSError as exc:
                print(f"       failed: {exc}")
                continue
            if sha256(target) == digest:
                print(f"[ok]   {name} ({target.stat().st_size / 1e6:.1f} MB)")
                break
            print("       checksum mismatch (Git LFS pointer?), trying next mirror")
        else:
            ok = False
            target.unlink(missing_ok=True)
            print(f"[fail] could not download {name}; download it manually into {out_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
