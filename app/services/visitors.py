"""Visitor passes with self-destructing face templates.

The front desk photographs a guest (with consent) and gets a pass valid until the end of the day
(or a few hours). The entrance kiosk then recognises the guest - check-in, host alert, check-out -
without a badge. When the pass expires the face template is deleted automatically (not just hidden);
only the visit log (name, host, times) remains, and that too is deleted after `visitor_log_days`.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Notification, Visitor, now
from app.services import app_settings, biokey
from app.vision.backends import get_backend

_last_purge: dict[int, float] = {}


class VisitorError(ValueError):
    pass


def end_of_day(when: datetime) -> datetime:
    return when.replace(hour=23, minute=59, second=59, microsecond=0)


def register(db: Session, org_id: int, name: str, images: list[np.ndarray], host: str = "", purpose: str = "",
             phone: str = "", hours: int = 0, created_by: str = "") -> Visitor:
    from app.services.faces import get_gallery

    backend = get_backend()
    vecs = []
    for img in images:
        found = backend.detect(img)
        if found:
            face = max(found, key=lambda f: f.bbox[2] * f.bbox[3])
            vecs.append(backend.embed(img, face))
    if not vecs:
        raise VisitorError("No face found in the photos - take them again, facing the camera.")
    centre = np.mean(vecs, axis=0)
    centre /= np.linalg.norm(centre)
    threshold = float(app_settings.get_setting(db, org_id, "match_threshold"))
    member = get_gallery(db, org_id).match(centre, threshold)
    if member.student_id is not None:
        from app.models import Student
        st = db.get(Student, member.student_id)
        raise VisitorError(f"This face is already registered as {st.name} ({st.student_code}) - no visitor pass needed.")
    blob, version = biokey.protect(db, org_id, centre)
    t = now()
    v = Visitor(org_id=org_id, name=name.strip()[:120], host=host.strip()[:120] or None, purpose=purpose.strip()[:200] or None,
                phone=phone.strip()[:40] or None, created_by=created_by, created_at=t, embedding=blob, key_version=version,
                expires_at=t + timedelta(hours=hours) if hours else end_of_day(t))
    db.add(v)
    db.commit()
    return v


def purge(db: Session, org_id: int, when: datetime | None = None) -> dict[str, int]:
    """Delete expired face templates, and visit logs older than the retention period."""
    when = when or now()
    expired = db.scalars(select(Visitor).where(Visitor.org_id == org_id, Visitor.embedding.is_not(None),
                                               Visitor.expires_at < when)).all()
    for v in expired:
        v.embedding, v.purged_at = None, when
        if v.on_site:
            v.checked_out_at = v.expires_at          # pass ended
    keep = int(app_settings.get_setting(db, org_id, "visitor_log_days"))
    old = db.scalars(select(Visitor).where(Visitor.org_id == org_id, Visitor.created_at < when - timedelta(days=keep))).all()
    for v in old:
        db.delete(v)
    db.commit()
    return {"templates": len(expired), "logs": len(old)}


def maybe_purge(db: Session, org_id: int) -> None:
    if time.monotonic() - _last_purge.get(org_id, -1e9) > 60:
        _last_purge[org_id] = time.monotonic()
        purge(db, org_id)


def end_visit(db: Session, v: Visitor) -> None:
    """Signed out at the desk: check out and destroy the template right away."""
    t = now()
    if v.checked_in_at and not v.checked_out_at:
        v.checked_out_at = t
    v.embedding, v.purged_at, v.expires_at = None, t, min(v.expires_at, t)
    db.commit()


def match(db: Session, org_id: int, embedding: np.ndarray) -> Visitor | None:
    maybe_purge(db, org_id)
    threshold = float(app_settings.get_setting(db, org_id, "match_threshold"))
    vec = np.asarray(embedding, dtype=np.float32)
    vec = vec / max(float(np.linalg.norm(vec)), 1e-12)
    best, best_sim = None, threshold
    for v in db.scalars(select(Visitor).where(Visitor.org_id == org_id, Visitor.embedding.is_not(None),
                                              Visitor.expires_at >= now())).all():
        ref = biokey.unprotect(db, org_id, v.embedding, v.key_version or 0)
        if ref is not None and float(ref @ vec) >= best_sim:
            best, best_sim = v, float(ref @ vec)
    return best


def seen(db: Session, org_id: int, v: Visitor) -> tuple[str, str]:
    """Kiosk saw a visitor: check in (host alert) or, after the check-out gap, check out. Returns (state, message)."""
    t = now()
    if v.checked_in_at is None:
        v.checked_in_at = t
        host = f" for {v.host}" if v.host else ""
        db.add(Notification(level="visitor", message=f"Guest {v.name} arrived{host} at {t:%H:%M}"
                                                     + (f" ({v.purpose})" if v.purpose else "") + "."))
        db.commit()
        return "marked", f"Welcome, {v.name}!" + (f" {v.host} has been notified." if v.host else "")
    gap = float(app_settings.get_setting(db, org_id, "checkout_after_minutes"))
    if v.checked_out_at is None and (t - v.checked_in_at).total_seconds() >= gap * 60:
        end_visit(db, v)
        return "checked_out", f"Goodbye, {v.name}! Your visitor pass has ended and your face data was deleted."
    return "duplicate", f"{v.name}: already checked in at {v.checked_in_at:%H:%M}."


def on_site(db: Session, org_id: int) -> list[Visitor]:
    return db.scalars(select(Visitor).where(Visitor.org_id == org_id, Visitor.checked_in_at.is_not(None),
                                            Visitor.checked_out_at.is_(None)).order_by(Visitor.checked_in_at)).all()
