"""Face enrollment: turn several photos of a student into encrypted embeddings."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import FaceEmbedding, Student
from app.services import app_settings, biokey
from app.vision.backends import get_backend
from app.vision.base import crop
from app.vision.liveness import sharpness
from app.vision.matcher import Gallery, gallery_cache


@dataclass
class EnrollmentReport:
    accepted: int
    rejected: list[str]
    total_templates: int


def enroll_images(db: Session, student: Student, images: list[np.ndarray]) -> EnrollmentReport:
    """Detect exactly one face per image, embed it and store it encrypted.

    Raw images are *not* stored - only the embedding vectors (privacy by design).
    """
    backend = get_backend()
    accepted, rejected = 0, []
    new_vectors = []
    for idx, img in enumerate(images, start=1):
        faces = backend.detect(img)
        if not faces:
            rejected.append(f"image {idx}: no face detected")
            continue
        faces.sort(key=lambda f: f.bbox[2] * f.bbox[3], reverse=True)
        if len(faces) > 1 and faces[0].bbox[2] * faces[0].bbox[3] < 2.5 * faces[1].bbox[2] * faces[1].bbox[3]:
            rejected.append(f"image {idx}: {len(faces)} faces of similar size - only the student should be in the frame")
            continue
        face = faces[0]  # the student is the dominant face; small background faces are ignored
        vec = backend.embed(img, face)
        quality = float(face.score) * min(1.0, sharpness(crop(img, face.bbox)) / 100.0)
        new_vectors.append(vec)
        blob, version = biokey.protect(db, student.org_id, vec)
        db.add(FaceEmbedding(student_id=student.id, embedding=blob, key_version=version, model_name=backend.name,
                             quality=quality))
        accepted += 1

    # Identity check: the new face must not already belong to another registered student
    # (prevents the same person being enrolled twice, or photos being attached to the wrong record).
    if new_vectors:
        threshold = float(app_settings.get_setting(db, student.org_id, "match_threshold"))
        others = load_gallery(db, student.org_id, exclude_student=student.id)
        centroid = np.mean(new_vectors, axis=0)
        centroid /= np.linalg.norm(centroid)
        m = others.match(centroid, threshold)
        if m.student_id is not None:
            other = db.get(Student, m.student_id)
            db.rollback()
            return EnrollmentReport(0, [f"this face is already registered as {other.name} ({other.student_code}), "
                                        f"similarity {m.similarity:.2f} - enrollment rejected"],
                                    count_templates(db, student.id))

    # Sanity check: all images of one enrollment should show the same person.
    if len(new_vectors) >= 2:
        m = np.stack(new_vectors)
        sims = m @ m.T
        mean_sim = (sims.sum() - len(m)) / (len(m) * (len(m) - 1))
        if mean_sim < settings.match_threshold * 0.8:
            db.rollback()
            return EnrollmentReport(0, ["images do not appear to show the same person - enrollment rejected"],
                                    count_templates(db, student.id))
    db.commit()
    gallery_cache.invalidate(student.org_id)
    return EnrollmentReport(accepted, rejected, count_templates(db, student.id))


def count_templates(db: Session, student_id: int) -> int:
    return len(db.scalars(select(FaceEmbedding.id).where(FaceEmbedding.student_id == student_id)).all())


def delete_faces(db: Session, student_id: int) -> int:
    rows = db.scalars(select(FaceEmbedding).where(FaceEmbedding.student_id == student_id)).all()
    org_id = db.scalar(select(Student.org_id).where(Student.id == student_id))
    for r in rows:
        db.delete(r)
    db.commit()
    gallery_cache.invalidate(org_id)
    return len(rows)


def load_gallery(db: Session, org_id: int, exclude_student: int | None = None) -> Gallery:
    """All active face templates of ONE organization - faces never match across customers."""
    backend = get_backend()
    q = (select(FaceEmbedding.student_id, FaceEmbedding.embedding, FaceEmbedding.key_version).join(Student)
         .where(Student.org_id == org_id, Student.is_active.is_(True), FaceEmbedding.model_name == backend.name))
    if exclude_student is not None:
        q = q.where(FaceEmbedding.student_id != exclude_student)
    pairs = [(sid, biokey.unprotect(db, org_id, blob, version or 0)) for sid, blob, version in db.execute(q).all()]
    pairs = [(sid, v) for sid, v in pairs if v is not None]
    if not pairs:
        return Gallery()
    return Gallery(np.stack([v for _, v in pairs]), [sid for sid, _ in pairs])


def get_gallery(db: Session, org_id: int) -> Gallery:
    return gallery_cache.get(org_id, lambda: load_gallery(db, org_id))


# ------------------------------------------------------------------ self-learning gallery
ADAPT_MIN_DAYS = 7          # at most one learned template per person per week
ADAPT_MAX = 3               # learned templates kept per person (oldest replaced)
ADAPT_EXTRA = 0.15          # must match this much better than the normal threshold
ADAPT_NOVELTY = 0.90        # ...but differ from every stored template (otherwise nothing new to learn)


def learn_from_sighting(db: Session, student: Student, embedding: np.ndarray, similarity: float) -> str | None:
    """Faces change (beard, glasses, growing up). After a confident, liveness-verified recognition, keep the
    new appearance as an extra template so recognition stays reliable without re-registration.

    Guards against learning the wrong face: very high similarity required, liveness must have passed (checked
    by the caller), the face must not resemble anyone else, at most one new template per week, and at most
    ADAPT_MAX learned templates (registration photos are never replaced). Returns a reason when skipped.
    """
    from datetime import timedelta

    from app.models import now

    if not app_settings.get_setting(db, student.org_id, "adaptive_gallery"):
        return "switched off"
    threshold = float(app_settings.get_setting(db, student.org_id, "match_threshold"))
    if similarity < threshold + ADAPT_EXTRA:
        return "not confident enough"
    rows = db.scalars(select(FaceEmbedding).where(FaceEmbedding.student_id == student.id)
                      .order_by(FaceEmbedding.created_at)).all()
    if not rows:
        return "no registration"
    learned = [r for r in rows if r.source == "adaptive"]
    if learned and learned[-1].created_at > now() - timedelta(days=ADAPT_MIN_DAYS):
        return "learned recently"
    vec = np.asarray(embedding, dtype=np.float32)
    vec = vec / max(float(np.linalg.norm(vec)), 1e-12)
    own = [biokey.unprotect(db, student.org_id, r.embedding, r.key_version or 0) for r in rows]
    own = [v for v in own if v is not None]
    if own and max(float(v @ vec) for v in own) >= ADAPT_NOVELTY:
        return "nothing new"
    others = load_gallery(db, student.org_id, exclude_student=student.id)
    if others.per_student_scores(vec) and max(others.per_student_scores(vec).values()) >= threshold:
        return "resembles someone else"
    if len(learned) >= ADAPT_MAX:
        db.delete(learned[0])
    blob, version = biokey.protect(db, student.org_id, vec)
    db.add(FaceEmbedding(student_id=student.id, embedding=blob, key_version=version, model_name=get_backend().name,
                         quality=float(similarity), source="adaptive"))
    db.commit()
    gallery_cache.invalidate(student.org_id)
    return None


def recognition_health(db: Session, org_id: int, days: int = 30) -> list[dict]:
    """Per person: how well the camera recognises them, and what to do about it."""
    from datetime import date, timedelta

    from app.models import Attendance, AttendanceStatus, now

    threshold = float(app_settings.get_setting(db, org_id, "match_threshold"))
    since = date.today() - timedelta(days=days)
    people = db.scalars(select(Student).where(Student.org_id == org_id, Student.is_active.is_(True))
                        .order_by(Student.name)).all()
    rows = []
    for p in people:
        temps = db.scalars(select(FaceEmbedding).where(FaceEmbedding.student_id == p.id)).all()
        recs = db.scalars(select(Attendance).where(Attendance.student_id == p.id, Attendance.date >= since,
                                                   Attendance.status.in_((AttendanceStatus.present,
                                                                          AttendanceStatus.late)))).all()
        by_face = [r.confidence for r in recs if r.method in ("face", "kiosk") and r.confidence is not None]
        manual = sum(1 for r in recs if r.method == "manual")
        mean = sum(by_face) / len(by_face) if by_face else None
        newest = max((t.created_at for t in temps if t.source == "enrolled"), default=None)
        issues = []
        if not temps:
            issues.append("No face registered.")
        if len(by_face) >= 3 and mean is not None and mean < threshold + 0.07:
            issues.append("Recognised only just above the threshold - register new photos in today's appearance and light.")
        if manual >= 3 and manual >= len(recs) / 2:
            issues.append(f"Marked present by hand {manual} of {len(recs)} times - the camera may not recognise them.")
        if newest and newest < now() - timedelta(days=365):
            issues.append("Registration photos are more than a year old.")
        rows.append({"student": p, "templates": len(temps), "learned": sum(1 for t in temps if t.source == "adaptive"),
                     "face_marks": len(by_face), "manual": manual, "mean": mean, "issues": issues})
    rows.sort(key=lambda r: (not r["issues"], r["mean"] if r["mean"] is not None else 2.0))
    return rows
