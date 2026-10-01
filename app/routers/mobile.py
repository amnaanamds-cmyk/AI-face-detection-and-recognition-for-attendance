"""Mobile app support: PWA manifest + service worker, and the "Mobile setup" page
(QR code of the server address and the certificate phones need to trust it)."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response
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


def lan_url(request: Request) -> str | None:
    """Address for phones on the same Wi-Fi: set by `run.py --lan` / the desktop app, or the https address in use."""
    if os.environ.get("PUBLIC_URL"):
        return os.environ["PUBLIC_URL"]
    host = request.url.hostname or ""
    if request.url.scheme == "https" and host not in ("localhost", "127.0.0.1"):
        return str(request.base_url).rstrip("/")
    return None


def public_url(request: Request) -> str | None:
    """Best address for phones: the online link while sharing, else the Wi-Fi address."""
    from app.services.tunnel import tunnel

    return tunnel.url if tunnel.running and tunnel.url else lan_url(request)


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
    from app.services.tunnel import tunnel

    user = _optional_user(request)
    return render(request, "mobile.html", user, url=lan_url(request), has_ca=CA_CERT.exists(),
                  on_https=request.url.scheme == "https", share=tunnel.status(), can_share=_can_share(user),
                  saas=settings.edition == "saas")


@router.get("/mobile/qr.svg", include_in_schema=False)
def qr(request: Request, u: str = ""):
    import segno

    from app.services.tunnel import tunnel

    online = tunnel.url if tunnel.running else None
    url = (online if u == "online" else None) or lan_url(request) or str(request.base_url).rstrip("/")
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


# ------------------------------------------------------------------ share online (Cloudflare quick tunnel)
def _can_share(user: User | None) -> bool:
    return bool(user and user.is_superadmin and settings.edition != "saas")


def _default_password_in_use() -> bool:
    from sqlalchemy import select

    from app.models import Role
    from app.security import verify_password

    with SessionLocal() as db:
        return any(verify_password("admin123", u.password_hash)
                   for u in db.scalars(select(User).where(User.role == Role.admin, User.is_active.is_(True))))


def _local_target(request: Request) -> tuple[str, bool]:
    """Where the tunnel forwards to: the port this request came in on, on this computer."""
    host, port = request.scope.get("server") or ("127.0.0.1", 8000)
    https = request.url.scheme == "https" and request.headers.get("x-forwarded-proto") is None
    return f"{'https' if https else 'http'}://127.0.0.1:{port}", https


@router.post("/share/start")
def share_start(request: Request):
    from app.services.tunnel import tunnel

    user = _optional_user(request)
    if not _can_share(user):
        return JSONResponse({"error": "Only the administrator of this installation can share it online."}, status_code=403)
    if _default_password_in_use():
        return JSONResponse({"error": "First change the admin password (it is still admin123): your name (top right) "
                                      "> Change password. Anyone on the internet could log in otherwise."}, status_code=400)
    target, https = _local_target(request)
    tunnel.start(target, insecure_tls=https)
    return tunnel.status()


@router.post("/share/stop")
def share_stop(request: Request):
    from app.services.tunnel import tunnel

    if not _can_share(_optional_user(request)):
        return JSONResponse({"error": "not allowed"}, status_code=403)
    tunnel.stop()
    return tunnel.status()


@router.get("/share/status")
def share_status(request: Request):
    from app.services.tunnel import tunnel

    if not _can_share(_optional_user(request)):
        return JSONResponse({"error": "not allowed"}, status_code=403)
    return tunnel.status()
