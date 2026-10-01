"""Build the FaceAttend desktop app: a folder with FaceAttend.exe (no Python needed on the target PC),
a portable .zip of it, and, if Inno Setup is installed, a FaceAttend-Setup.exe installer.

    pip install -r requirements.txt pyinstaller pillow
    python packaging/build_exe.py                                 # -> dist/FaceAttend/FaceAttend.exe
    python packaging/build_exe.py --include-research-antispoof    # academic/FYP builds only

Build on Windows for Windows (PyInstaller does not cross-compile). The GitHub Actions workflow
.github/workflows/windows-exe.yml does exactly this on every push and publishes the files.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build" / "desktop"
NAME = "FaceAttend"


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)


def make_icon() -> Path:
    from PIL import Image

    ico = BUILD / "icon.ico"
    Image.open(ROOT / "app/static/icons/icon-512.png").save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return ico


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--include-research-antispoof", action="store_true",
                    help="bundle the NON-COMMERCIAL research anti-spoofing model (never for a product you sell)")
    ap.add_argument("--no-installer", action="store_true")
    args = ap.parse_args()

    BUILD.mkdir(parents=True, exist_ok=True)
    models = BUILD / "models"
    if models.exists():
        shutil.rmtree(models)
    dl = [sys.executable, "scripts/download_models.py", "--dir", str(models)]
    run(dl + (["--include-research-antispoof"] if args.include_research_antispoof else []))
    for custom in ("antispoof.onnx", "antispoof.json"):  # your own trained model, if present
        if (ROOT / "models" / custom).exists():
            shutil.copy2(ROOT / "models" / custom, models / custom)

    data = [(ROOT / "app/templates", "app/templates"), (ROOT / "app/static", "app/static"), (models, "models")]
    apk = ROOT / "dist" / "FaceAttend.apk"   # the Android app, served to phones at /download/android
    if apk.exists():
        data.append((apk, "downloads"))
    if (ROOT / "app/license_public_key.pem").exists():
        data.append((ROOT / "app/license_public_key.pem", "app"))
    cmd = [sys.executable, "-m", "PyInstaller", "desktop.py", "--name", NAME, "--noconfirm", "--clean",
           "--windowed", "--icon", str(make_icon()), "--distpath", str(ROOT / "dist"),
           "--workpath", str(BUILD / "work"), "--specpath", str(BUILD),
           "--collect-data", "reportlab", "--collect-submodules", "app",
           "--hidden-import", "uvicorn.logging", "--hidden-import", "uvicorn.loops.auto",
           "--hidden-import", "uvicorn.protocols.http.auto", "--hidden-import", "uvicorn.protocols.http.h11_impl",
           "--hidden-import", "uvicorn.lifespan.on", "--hidden-import", "multipart",
           "--hidden-import", "pystray._win32", "--collect-submodules", "pystray",
           "--exclude-module", "pytest", "--exclude-module", "tkinter.test"]
    for src, dest in data:
        cmd += ["--add-data", f"{src}{os.pathsep}{dest}"]
    run(cmd)

    out = ROOT / "dist" / NAME
    shutil.copy2(ROOT / "packaging" / "README-desktop.txt", out / "README.txt")
    archive = shutil.make_archive(str(ROOT / "dist" / f"{NAME}-portable"), "zip", out.parent, NAME)
    print(f"\nApp folder : {out}\nPortable   : {archive}")

    iscc = shutil.which("iscc") or next((str(p) for p in (Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Inno Setup 6" / "ISCC.exe",)
                                          if p.exists()), None)
    if iscc and not args.no_installer:
        run([iscc, str(ROOT / "packaging" / "installer.iss")])
        print(f"Installer  : {ROOT / 'dist' / (NAME + '-Setup.exe')}")
    elif sys.platform == "win32" and not args.no_installer:
        print("Inno Setup not found - skipped the installer (https://jrsoftware.org/isinfo.php)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
