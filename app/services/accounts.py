"""Helpers for login accounts in a multi-tenant installation."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Organization, User


def username_taken(db: Session, username: str) -> bool:
    return db.scalar(select(User.id).where(User.username == username)) is not None


def person_username(db: Session, org: Organization, code: str) -> str:
    """Login name for a student / employee / member account.

    Usernames are unique across the whole installation (many organizations share one
    server), so the ID is prefixed with the organization's short name when needed.
    """
    code = code.strip()
    if not username_taken(db, code):
        return code
    candidate = f"{org.slug}-{code}".lower()
    n = 2
    while username_taken(db, candidate):
        candidate = f"{org.slug}-{code}-{n}".lower()
        n += 1
    return candidate
