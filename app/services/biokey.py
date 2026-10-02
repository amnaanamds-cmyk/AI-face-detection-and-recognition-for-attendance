"""Cancelable (revocable) biometrics.

A face template cannot be changed like a password - unless it is never stored in its natural form.
Every stored template is first multiplied by a secret random rotation matrix Q that belongs to
the organization (an orthogonal 128x128 matrix). Rotations keep every angle between vectors, so
recognition accuracy is exactly the same, but:

* the stored numbers are meaningless without the organization's key - copying the database (and even
  breaking its encryption) gives templates that match nothing anywhere else;
* the same person registered in two organizations gets unrelated templates (no cross-linking);
* if a leak is suspected, "Rotate key" re-protects every template with a new matrix in one step,
  without anybody registering again - the leaked copies no longer match anything.

The key (a random seed) is stored encrypted with the server secret, separately per organization.
"""
from __future__ import annotations

import secrets
import threading

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FaceEmbedding, OrgSetting, Student, now
from app.security import decrypt_embedding, encrypt_embedding, _fernet

KEY = "biometric_key"          # org_settings row: "<version>:<encrypted seed>"
_cache: dict[int, tuple[int, np.ndarray]] = {}
_lock = threading.Lock()


def _matrix(seed: int, dim: int) -> np.ndarray:
    """Random orthogonal matrix (QR of a Gaussian matrix, sign-corrected so it is uniformly random)."""
    rng = np.random.default_rng(seed)
    q, r = np.linalg.qr(rng.standard_normal((dim, dim)))
    return (q * np.sign(np.diag(r))).astype(np.float32)


def _row(db: Session, org_id: int) -> OrgSetting | None:
    return db.scalar(select(OrgSetting).where(OrgSetting.org_id == org_id, OrgSetting.key == KEY))


def _new_seed() -> int:
    return secrets.randbits(63)


def _store(db: Session, org_id: int, version: int, seed: int) -> None:
    value = f"{version}:" + _fernet().encrypt(str(seed).encode()).decode()
    row = _row(db, org_id)
    if row:
        row.value = value
    else:
        db.add(OrgSetting(org_id=org_id, key=KEY, value=value))


def current(db: Session, org_id: int, dim: int = 128) -> tuple[int, np.ndarray]:
    """(key version, rotation matrix) of the organization; created on first use."""
    row = _row(db, org_id)
    if row is None:
        _store(db, org_id, 1, _new_seed())
        db.flush()
        row = _row(db, org_id)
    version_s, token = row.value.split(":", 1)
    version = int(version_s)
    with _lock:
        hit = _cache.get(org_id)
        if hit and hit[0] == version and hit[1].shape[0] == dim:
            return hit
        seed = int(_fernet().decrypt(token.encode()).decode())
        entry = (version, _matrix(seed, dim))
        _cache[org_id] = entry
        return entry


def protect(db: Session, org_id: int, vec: np.ndarray) -> tuple[bytes, int]:
    """Template as stored: encrypt(Q @ v), plus the key version used."""
    version, q = current(db, org_id, len(vec))
    return encrypt_embedding(q @ np.asarray(vec, dtype=np.float32)), version


def unprotect(db: Session, org_id: int, blob: bytes, version: int) -> np.ndarray | None:
    """Back to the model's own embedding space (in memory only). None if the key version is unknown."""
    v = decrypt_embedding(blob)
    if version == 0:            # stored before cancelable biometrics existed
        return v
    cur, q = current(db, org_id, len(v))
    return q.T @ v if version == cur else None


def rotate(db: Session, org_id: int) -> int:
    """Re-protect every template of the organization with a brand-new key. Returns templates updated."""
    from app.vision.matcher import gallery_cache

    from app.models import Visitor

    rows = list(db.scalars(select(FaceEmbedding).join(Student).where(Student.org_id == org_id)).all())
    rows += list(db.scalars(select(Visitor).where(Visitor.org_id == org_id, Visitor.embedding.is_not(None))).all())
    raw = [unprotect(db, org_id, r.embedding, r.key_version or 0) for r in rows]
    old_version, _ = current(db, org_id)
    new_version = old_version + 1
    _store(db, org_id, new_version, _new_seed())
    with _lock:
        _cache.pop(org_id, None)
    db.flush()
    count = 0
    for r, v in zip(rows, raw):
        if v is None:           # unreadable template (should not happen): drop it rather than keep it unprotected
            if isinstance(r, FaceEmbedding):
                db.delete(r)
            else:
                r.embedding = None
            continue
        r.embedding, r.key_version = protect(db, org_id, v)
        count += 1
    from app.models import Organization
    org = db.get(Organization, org_id)
    if org is not None:
        org.biometric_key_rotated_at = now()
    db.commit()
    gallery_cache.invalidate(org_id)
    return count


def protect_legacy(db: Session, org_id: int) -> int:
    """Templates stored before this feature existed get protected with the current key (once, at start-up)."""
    rows = db.scalars(select(FaceEmbedding).join(Student).where(Student.org_id == org_id,
                                                                (FaceEmbedding.key_version == 0) |
                                                                FaceEmbedding.key_version.is_(None))).all()
    for r in rows:
        r.embedding, r.key_version = protect(db, org_id, decrypt_embedding(r.embedding))
    db.commit()
    return len(rows)


def status(db: Session, org_id: int) -> dict:
    from app.models import Organization

    version, _ = current(db, org_id)
    db.commit()
    rows = db.execute(select(FaceEmbedding.key_version).join(Student).where(Student.org_id == org_id)).all()
    org = db.get(Organization, org_id)
    return {"version": version, "templates": len(rows),
            "unprotected": sum(1 for (v,) in rows if not v),
            "rotated_at": getattr(org, "biometric_key_rotated_at", None)}
