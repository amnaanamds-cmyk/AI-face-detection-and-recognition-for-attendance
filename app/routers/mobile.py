"""Mobile app support: PWA manifest + service worker, and the "Mobile setup" page
(QR code of the server address and the certificate phones need to trust it)."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import joinedload

from app.config import DATA_DIR, settings
from app.database import SessionLocal
from app.deps import render
from app.models import User

router = APIRouter()
STATIC = Path(__file__).resolve().parent.parent / "static"
CA_CERT = DATA_DIR / "tls" / "ca.pem"


def _optional_user(request: Request) -> User | None:
    uid = request.session.get("uid")
    if not uid:
        return None
    with SessionLocal() as db:
        user = db.get(User, uid, options=[joinedload(User.org)])
        return user if user and user.is_active else None


def public_url(request: Request) -> str | None:
    """Address phones should open: set by `run.py --lan`, or the https address in use."""
    if os.environ.get("PUBLIC_URL"):
        return os.environ["PUBLIC_URL"]
    host = request.url.hostname or ""
    if request.url.scheme == "https" and host not in ("localhost", "127.0.0.1"):
        return str(request.base_url).rstrip("/")
    return None


@router.get("/sw.js", include_in_schema=False)
def service_worker():
    # served from the site root so the worker controls the whole app
    return FileResponse(STATIC / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@router.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    data = json.loads((STATIC / "manifest.webmanifest").read_text())
    data["name"] = settings.app_name
    data["short_name"] = settings.app_name[:12]
    return Response(json.dumps(data), media_type="application/manifest+json")


@router.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(STATIC / "icons" / "favicon-32.png", media_type="image/png")


@router.get("/offline", include_in_schema=False)
def offline(request: Request):
    return render(request, "offline.html", None)


@router.get("/mobile")
def mobile_setup(request: Request):
    url = public_url(request)
    return render(request, "mobile.html", _optional_user(request), url=url, has_ca=CA_CERT.exists(),
                  on_https=request.url.scheme == "https")


@router.get("/mobile/qr.svg", include_in_schema=False)
def qr(request: Request):
    import segno

    url = public_url(request) or str(request.base_url).rstrip("/")
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="svg", scale=8, border=2, dark="#1f4e79")
    return Response(buf.getvalue(), media_type="image/svg+xml")


@router.get("/mobile/attendance-ca.crt", include_in_schema=False)
def ca_certificate():
    """The local certificate authority created by `run.py --lan` (public, contains no secret)."""
    if not CA_CERT.exists():
        return Response("Start the server with --lan first (start-mobile.bat / ./start.sh --lan).", status_code=404)
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding

    der = x509.load_pem_x509_certificate(CA_CERT.read_bytes()).public_bytes(Encoding.DER)
    return Response(der, media_type="application/x-x509-ca-cert",
                    headers={"Content-Disposition": 'attachment; filename="attendance-ca.crt"'})
