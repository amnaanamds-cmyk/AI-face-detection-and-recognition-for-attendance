"""Front desk: visitor passes with self-destructing face templates."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import flash, render, staff
from app.models import User, Visitor, now
from app.services import visitors
from app.vision.backends import ModelsMissingError
from app.vision.base import decode_image

router = APIRouter()


def _visitor(db: Session, user: User, vid: int) -> Visitor:
    v = db.get(Visitor, vid)
    if v is None or v.org_id != user.org_id:
        raise HTTPException(404, "Visitor not found")
    return v


@router.get("/visitors")
def visitors_page(request: Request, user: User = Depends(staff), db: Session = Depends(get_db)):
    visitors.purge(db, user.org_id)
    since = now() - timedelta(days=30)
    log = db.scalars(select(Visitor).where(Visitor.org_id == user.org_id, Visitor.created_at >= since)
                     .order_by(Visitor.created_at.desc()).limit(200)).all()
    return render(request, "visitors.html", user, log=log, now=now())


class NewVisitor(BaseModel):
    name: str
    host: str = ""
    purpose: str = ""
    phone: str = ""
    hours: int = 0
    consent: bool = False
    images: list[str]


@router.post("/api/visitors")
def create_visitor(body: NewVisitor, user: User = Depends(staff), db: Session = Depends(get_db)):
    if not body.consent:
        raise HTTPException(400, "The visitor must agree to their face being used for today's pass.")
    if not body.name.strip():
        raise HTTPException(400, "Name is required")
    if not body.images or len(body.images) > 6:
        raise HTTPException(400, "Take 1 to 6 photos")
    try:
        frames = [decode_image(i) for i in body.images]
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, "Invalid image") from exc
    try:
        v = visitors.register(db, user.org_id, body.name, frames, body.host, body.purpose, body.phone,
                              max(0, min(body.hours, 24)), user.username)
    except visitors.VisitorError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ModelsMissingError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"id": v.id, "name": v.name, "expires_at": v.expires_at.strftime("%H:%M on %d %b")}


@router.post("/visitors/{vid}/end")
def end_visit(vid: int, request: Request, user: User = Depends(staff), db: Session = Depends(get_db)):
    v = _visitor(db, user, vid)
    visitors.end_visit(db, v)
    flash(request, f"{v.name}: visit ended, face data deleted.")
    return RedirectResponse("/visitors", status_code=303)
