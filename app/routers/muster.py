"""Emergency muster (evacuation roll call)."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import flash, render, staff
from app.models import MusterEntry, MusterEvent, User
from app.services import muster, visitors
from app.vision.backends import ModelsMissingError
from app.vision.base import decode_image

router = APIRouter()


def _event(db: Session, user: User, eid: int) -> MusterEvent:
    ev = db.get(MusterEvent, eid)
    if ev is None or ev.org_id != user.org_id:
        raise HTTPException(404, "Muster not found")
    return ev


@router.get("/muster")
def muster_page(request: Request, user: User = Depends(staff), db: Session = Depends(get_db)):
    ev = muster.active(db, user.org_id)
    if ev:
        return render(request, "muster.html", user, ev=ev, s=muster.summary(ev))
    past = db.scalars(select(MusterEvent).where(MusterEvent.org_id == user.org_id)
                      .order_by(MusterEvent.id.desc()).limit(20)).all()
    return render(request, "muster.html", user, ev=None, people=muster.on_site(db, user.org_id),
                  guests=visitors.on_site(db, user.org_id), past=[(p, muster.summary(p)) for p in past])


@router.get("/muster/{eid}")
def muster_report(eid: int, request: Request, user: User = Depends(staff), db: Session = Depends(get_db)):
    ev = _event(db, user, eid)
    return render(request, "muster.html", user, ev=ev, s=muster.summary(ev))


@router.post("/muster/start")
def start(request: Request, note: str = Form(""), user: User = Depends(staff), db: Session = Depends(get_db)):
    ev = muster.start(db, user.org_id, user.username, note)
    flash(request, f"Roll call started: {len(ev.entries)} people on site.", "danger")
    return RedirectResponse("/muster", status_code=303)


@router.get("/api/muster/{eid}")
def status(eid: int, user: User = Depends(staff), db: Session = Depends(get_db)):
    return muster.summary(_event(db, user, eid))


@router.post("/api/muster/{eid}/frame")
def frame(eid: int, payload: dict = Body(...), user: User = Depends(staff), db: Session = Depends(get_db)):
    ev = _event(db, user, eid)
    if ev.ended_at:
        raise HTTPException(409, "This roll call has ended")
    try:
        img = decode_image(payload.get("image", ""))
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, "Invalid image") from exc
    try:
        found = muster.scan(db, ev, img, user.username)
    except ModelsMissingError as exc:
        raise HTTPException(503, str(exc)) from exc
    db.refresh(ev)
    return {"faces": found, **muster.summary(ev)}


@router.post("/api/muster/{eid}/entries/{entry_id}/safe")
def manual_safe(eid: int, entry_id: int, user: User = Depends(staff), db: Session = Depends(get_db)):
    ev = _event(db, user, eid)
    e = db.get(MusterEntry, entry_id)
    if e is None or e.event_id != ev.id:
        raise HTTPException(404, "Not on this roll call")
    muster.mark_safe(db, ev, e.kind, e.ref_id, "manual", user.username)
    db.refresh(ev)
    return muster.summary(ev)


@router.post("/muster/{eid}/notify")
def notify(eid: int, request: Request, user: User = Depends(staff), db: Session = Depends(get_db)):
    n = muster.notify_missing(db, _event(db, user, eid))
    flash(request, f"{n} emergency contact(s) messaged." if n else "No emergency contact numbers for the missing people.",
          "warning")
    return RedirectResponse("/muster", status_code=303)


@router.post("/muster/{eid}/end")
def end(eid: int, request: Request, user: User = Depends(staff), db: Session = Depends(get_db)):
    ev = _event(db, user, eid)
    muster.end(db, ev)
    flash(request, "Roll call ended. The report is kept below.")
    return RedirectResponse(f"/muster/{ev.id}", status_code=303)
