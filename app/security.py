"""Password hashing and encryption of biometric templates."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

import numpy as np
from cryptography.fernet import Fernet

from app.config import settings

_PBKDF2_ITERATIONS = 240_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations))
    return hmac.compare_digest(digest.hex(), digest_hex)


def _fernet() -> Fernet:
    material = (settings.embedding_key or settings.secret_key).encode()
    key = base64.urlsafe_b64encode(hashlib.sha256(b"face-embedding:" + material).digest())
    return Fernet(key)


def encrypt_embedding(vec: np.ndarray) -> bytes:
    """Serialise a float32 vector and encrypt it (AES-128-CBC + HMAC via Fernet)."""
    return _fernet().encrypt(np.asarray(vec, dtype=np.float32).tobytes())


def decrypt_embedding(blob: bytes) -> np.ndarray:
    return np.frombuffer(_fernet().decrypt(blob), dtype=np.float32).copy()
