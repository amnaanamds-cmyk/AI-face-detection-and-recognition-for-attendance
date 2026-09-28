"""Face enrollment: turn several photos of a student into encrypted embeddings."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import FaceEmbedding, Student
from app.security import decrypt_embedding, encrypt_embedding
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
        if len(faces) > 1:
            rejected.append(f"image {idx}: {len(faces)} faces detected - only the student should be in the frame")
            continue
        face = faces[0]
        vec = backend.embed(img, face)
        quality = float(face.score) * min(1.0, sharpness(crop(img, face.bbox)) / 100.0)
        new_vectors.append(vec)
        db.add(FaceEmbedding(student_id=student.id, embedding=encrypt_embedding(vec), model_name=backend.name, quality=quality))
        accepted += 1

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
    gallery_cache.invalidate()
    return EnrollmentReport(accepted, rejected, count_templates(db, student.id))


def count_templates(db: Session, student_id: int) -> int:
    return len(db.scalars(select(FaceEmbedding.id).where(FaceEmbedding.student_id == student_id)).all())


def delete_faces(db: Session, student_id: int) -> int:
    rows = db.scalars(select(FaceEmbedding).where(FaceEmbedding.student_id == student_id)).all()
    for r in rows:
        db.delete(r)
    db.commit()
    gallery_cache.invalidate()
    return len(rows)


def load_gallery(db: Session) -> Gallery:
    backend = get_backend()
    rows = db.execute(
        select(FaceEmbedding.student_id, FaceEmbedding.embedding)
        .join(Student)
        .where(Student.is_active.is_(True), FaceEmbedding.model_name == backend.name)
    ).all()
    if not rows:
        return Gallery()
    vecs = np.stack([decrypt_embedding(blob) for _, blob in rows])
    return Gallery(vecs, [sid for sid, _ in rows])


def get_gallery(db: Session) -> Gallery:
    return gallery_cache.get(lambda: load_gallery(db))
