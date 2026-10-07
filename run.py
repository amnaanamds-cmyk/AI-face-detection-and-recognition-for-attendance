"""Start the attendance server.

    python run.py                      # http://127.0.0.1:8000 (this computer only)
    python run.py --host 0.0.0.0 --port 443 --cert fullchain.pem --key key.pem   # your own domain + certificate

Phones connect through "Share online" (a secure https link, Connect phones page) or the hosted
service - not over the local Wi-Fi.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", action="store_true", help="(used by start.bat) run the console server, not the desktop app")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--cert", type=Path, help="use your own TLS certificate (PEM)")
    ap.add_argument("--key", type=Path, help="private key for --cert")
    args = ap.parse_args()

    import uvicorn

    from app.config import settings
    from app.vision.backends import ANTISPOOF_FILE, SFACE_FILE, YUNET_FILE

    missing = [f for f in (YUNET_FILE, SFACE_FILE) if not (settings.models_dir / f).exists()]
    if missing and settings.vision_backend == "opencv":
        print("Face models missing - downloading them first ...")
        import subprocess
        subprocess.run([sys.executable, str(ROOT / "scripts" / "download_models.py"), "--dir", str(settings.models_dir)])
    if not (settings.models_dir / ANTISPOOF_FILE).exists():
        print("Note: anti-spoofing model not installed - liveness uses the head-movement test.")

    kwargs = {}
    if args.cert:
        host, port = args.host or "0.0.0.0", args.port or 443
        kwargs = {"ssl_certfile": str(args.cert), "ssl_keyfile": str(args.key)}
        print(f"\n  Serving https on port {port} with your certificate\n")
    else:
        host, port = args.host or "127.0.0.1", args.port or 8000
        print(f"\n  Open:  http://{host}:{port}\n")
    # one worker: live face trackers are kept in process memory
    uvicorn.run("app.main:app", host=host, port=port, workers=1, proxy_headers=True, **kwargs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
