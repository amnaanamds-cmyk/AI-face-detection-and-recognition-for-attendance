"""Download the pre-trained face models from the OpenCV model zoo.

    python scripts/download_models.py [--dir models]

* YuNet  (face detection, ~0.2 MB)   - https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet
* SFace  (face recognition, ~37 MB)  - https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface
* Research anti-spoofing CNN (~1.9 MB, only with --include-research-antispoof)
  https://github.com/hairymax/Face-AntiSpoofing - trained on CelebA-Spoof, which is licensed for
  NON-COMMERCIAL RESEARCH ONLY and the repository has no license: fine for a university project,
  NOT for a product you sell. For a commercial deployment train your own model
  (training/antispoof/README.md) or license one; without a CNN, liveness uses the head-movement test.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# name -> (list of URLs, sha256, optional git fallback (repo, revision, path))
ZOO = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
ZOO_RAW = "https://github.com/opencv/opencv_zoo/raw/main/models/"
ASP_REV = "eed4e278d80e60c2c63ea8e480886266e566ec63"
ASP_PATH = "saved_models/AntiSpoofing_print-replay_1.5_128.onnx"
MODELS = {
    # YuNet face detector (MIT) and SFace recogniser (Apache-2.0) from the OpenCV model zoo (Git LFS)
    "face_detection_yunet_2023mar.onnx": (
        [ZOO + "face_detection_yunet/face_detection_yunet_2023mar.onnx",
         ZOO_RAW + "face_detection_yunet/face_detection_yunet_2023mar.onnx"],
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4", None,
    ),
    "face_recognition_sface_2021dec.onnx": (
        [ZOO + "face_recognition_sface/face_recognition_sface_2021dec.onnx",
         ZOO_RAW + "face_recognition_sface/face_recognition_sface_2021dec.onnx"],
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79", None,
    ),
    # Anti-spoofing CNN (MiniFASNet architecture from Silent-Face-Anti-Spoofing, trained on
    # CelebA-Spoof: live / print / replay) - https://github.com/hairymax/Face-AntiSpoofing
    "AntiSpoofing_print-replay_1.5_128.onnx": (
        [f"https://github.com/hairymax/Face-AntiSpoofing/raw/{ASP_REV}/{ASP_PATH}",
         f"https://raw.githubusercontent.com/hairymax/Face-AntiSpoofing/{ASP_REV}/{ASP_PATH}"],
        "7ef69fafebdb333619c6dcdb54ba9b493ccaff5cd4ceba8f66de20a1c6692433",
        ("https://github.com/hairymax/Face-AntiSpoofing", ASP_REV, ASP_PATH),
    ),
}


RESEARCH_ONLY = {"AntiSpoofing_print-replay_1.5_128.onnx"}


def git_fetch(repo: str, rev: str, path: str, target: Path) -> bool:
    """Fallback for networks that block raw file downloads but allow git."""
    import shutil
    import subprocess
    import tempfile

    if not shutil.which("git"):
        return False
    with tempfile.TemporaryDirectory() as tmp:
        try:
            subprocess.run(["git", "clone", "-q", "--filter=blob:none", "--no-checkout", repo, tmp], check=True)
            subprocess.run(["git", "-C", tmp, "checkout", "-q", rev, "--", path], check=True)
        except (subprocess.CalledProcessError, OSError) as exc:
            print(f"       git fallback failed: {exc}")
            return False
        shutil.copy(Path(tmp) / path, target)
    return True


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=str(ROOT / "models"))
    ap.add_argument("--include-research-antispoof", action="store_true",
                    help="also download the NON-COMMERCIAL research anti-spoofing model (academic use only)")
    args = ap.parse_args()
    out_dir = Path(args.dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ok = True
    for name, (urls, digest, git) in MODELS.items():
        target = out_dir / name
        if name in RESEARCH_ONLY and not args.include_research_antispoof:
            if not target.exists():
                print(f"[skip] {name} (research-only license; use --include-research-antispoof for academic use)")
            continue
        if target.exists() and sha256(target) == digest:
            print(f"[ok]   {name} already present")
            continue
        done = False
        for url in urls:
            try:
                print(f"[get]  {url}")
                urllib.request.urlretrieve(url, target)
            except OSError as exc:
                print(f"       failed: {exc}")
                continue
            if sha256(target) == digest:
                done = True
                break
            print("       checksum mismatch (Git LFS pointer?), trying next source")
        if not done and git:
            print(f"[git]  {git[0]}")
            done = git_fetch(*git, target) and sha256(target) == digest
        if done:
            print(f"[ok]   {name} ({target.stat().st_size / 1e6:.1f} MB)")
        else:
            ok = False
            target.unlink(missing_ok=True)
            print(f"[fail] could not download {name}; download it manually into {out_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
