"""Signed license keys for the self-hosted edition.

A license is ``base64url(json payload) + "." + base64url(Ed25519 signature)``. Only the
vendor (you) holds the private key (scripts/license_tool.py keygen); installations contain
only the public key in app/license_public_key.pem, so nobody else can create licenses.
"""
from __future__ import annotations

import base64
import json
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from app.config import settings

PUBLIC_KEY_FILE = Path(__file__).resolve().parent.parent / "license_public_key.pem"


class LicenseError(ValueError):
    pass


@dataclass(frozen=True)
class License:
    licensee: str
    max_people: int | None
    expires: date | None
    issued: date

    @property
    def expired(self) -> bool:
        return self.expires is not None and self.expires < date.today()


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign_license(private_key: Ed25519PrivateKey, licensee: str, max_people: int | None,
                 expires: date | None = None) -> str:
    payload = {"licensee": licensee, "max_people": max_people,
               "expires": expires.isoformat() if expires else None, "issued": date.today().isoformat()}
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return _b64e(body) + "." + _b64e(private_key.sign(body))


def load_public_key(path: Path | None = None) -> Ed25519PublicKey | None:
    path = path or PUBLIC_KEY_FILE
    if not path.exists():
        return None
    key = serialization.load_pem_public_key(path.read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise LicenseError("license public key is not an Ed25519 key")
    return key


def verify_license(text: str, public_key: Ed25519PublicKey | None = None) -> License:
    public_key = public_key or load_public_key()
    if public_key is None:
        raise LicenseError("this build has no license public key")
    try:
        body_b64, sig_b64 = text.strip().split(".")
        body, sig = _b64d(body_b64), _b64d(sig_b64)
        public_key.verify(sig, body)
        data = json.loads(body)
    except (ValueError, InvalidSignature) as exc:
        raise LicenseError("invalid license key") from exc
    lic = License(
        licensee=data["licensee"], max_people=data.get("max_people"),
        expires=date.fromisoformat(data["expires"]) if data.get("expires") else None,
        issued=date.fromisoformat(data["issued"]),
    )
    if lic.expired:
        raise LicenseError(f"license expired on {lic.expires}")
    return lic


_cache: dict[str, License | None] = {}
_lock = threading.Lock()


def current_license() -> License | None:
    """The installed, valid license (cached until the file changes), or None."""
    path = settings.license_file
    try:
        stamp = f"{path}:{path.stat().st_mtime_ns}"
    except OSError:
        return None
    with _lock:
        if stamp not in _cache:
            try:
                _cache.clear()
                _cache[stamp] = verify_license(path.read_text())
            except (LicenseError, OSError):
                _cache[stamp] = None
        return _cache[stamp]


def install_license(text: str) -> License:
    lic = verify_license(text)  # raises LicenseError if bad
    settings.license_file.parent.mkdir(parents=True, exist_ok=True)
    settings.license_file.write_text(text.strip())
    return lic
