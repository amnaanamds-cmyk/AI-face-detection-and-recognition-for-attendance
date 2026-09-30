"""Entrance kiosk: an always-on check-in / check-out screen (offices, gyms, events, school gates)."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import render, staff
from app.models import User
from app.services import attendance as att
from app.services.billing import subscription_problem
from app.vision.backends import ModelsMissingError
from app.vision.base import decode_image

router = APIRouter()


@router.get("/kiosk")
def kiosk_page(request: Request, user: User = Depends(staff)):
    return render(request, "kiosk.html", user, problem=subscription_problem(user.org))


@router.post("/api/kiosk/frame")
def kiosk_frame(payload: dict = Body(...), user: User = Depends(staff), db: Session = Depends(get_db)):
    problem = subscription_problem(user.org)
    if problem:
        raise HTTPException(402, problem)
    try:
        frame = decode_image(payload.get("image", ""))
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, "Invalid image") from exc
    try:
        return att.process_kiosk_frame(db, user.org_id, frame)
    except ModelsMissingError as exc:
        raise HTTPException(503, str(exc)) from exc
