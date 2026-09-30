from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import current_user, flash, render
from app.config import settings
from app.models import OrgKind, Role, User
from app.services.billing import TRIAL_DAYS, start_trial
from app.tenancy import create_org
from app.security import hash_password, verify_password

router = APIRouter()

KIND_LABELS = {OrgKind.school.value: "School, college or university", OrgKind.office.value: "Company or office",
               OrgKind.event.value: "Gym, club, training center or events"}

# Brute-force protection: max 5 failed logins per username+IP in 15 minutes.
MAX_FAILURES, WINDOW_SECONDS = 5, 15 * 60
_failures: dict[str, deque] = defaultdict(deque)
_failures_lock = threading.Lock()


def _locked_out(key: str) -> bool:
    now = time.monotonic()
    with _failures_lock:
        q = _failures[key]
        while q and now - q[0] > WINDOW_SECONDS:
            q.popleft()
        return len(q) >= MAX_FAILURES


def _record_failure(key: str) -> None:
    with _failures_lock:
        _failures[key].append(time.monotonic())


def _safe_next(url: str | None) -> str:
    return url if url and url.startswith("/") and not url.startswith("//") else "/"


@router.get("/login")
def login_page(request: Request, next: str = "/"):
    return render(request, "login.html", None, next=_safe_next(next))


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/"),
          db: Session = Depends(get_db)):
    key = f"{username.strip().lower()}|{request.client.host if request.client else ''}"
    if _locked_out(key):
        return render(request, "login.html", None, status_code=429, next=_safe_next(next),
                      error="Too many failed attempts. Try again in 15 minutes.")
    login_name = username.strip()
    user = db.scalar(select(User).where(or_(User.username == login_name, User.email == login_name.lower()))
                     .order_by(User.id).limit(1))
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        _record_failure(key)
        return render(request, "login.html", None, status_code=401, next=_safe_next(next),
                      error="Invalid username or password")
    _failures.pop(key, None)
    request.session.clear()
    request.session["uid"] = user.id
    return RedirectResponse(_safe_next(next), status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/account/password")
def password_page(request: Request, user: User = Depends(current_user)):
    return render(request, "password.html", user)


@router.post("/account/password")
def change_password(request: Request, current: str = Form(...), new: str = Form(...), confirm: str = Form(...),
                    user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not verify_password(current, user.password_hash):
        flash(request, "Current password is incorrect", "danger")
    elif len(new) < 8 or new != confirm:
        flash(request, "New password must be at least 8 characters and match the confirmation", "danger")
    else:
        user.password_hash = hash_password(new)
        db.commit()
        flash(request, "Password changed")
    return RedirectResponse("/account/password", status_code=303)


# ------------------------------------------------------------------ self-service signup (SaaS)
@router.get("/signup")
def signup_page(request: Request, plan: str = "trial"):
    if not settings.public_signup:
        return RedirectResponse("/login", status_code=303)
    return render(request, "signup.html", None, kinds=KIND_LABELS, form={}, plan=plan)


@router.post("/signup")
async def signup(request: Request, db: Session = Depends(get_db)):
    if not settings.public_signup:
        return RedirectResponse("/login", status_code=303)
    form = {k: str(v).strip() for k, v in (await request.form()).items()}
    org_name, kind = form.get("org_name", ""), form.get("kind", "school")
    full_name, email, password = form.get("full_name", ""), form.get("email", "").lower(), form.get("password", "")
    error = None
    if not org_name or not full_name or "@" not in email:
        error = "Please fill in the organization name, your name and a valid e-mail address."
    elif len(password) < 8:
        error = "The password must be at least 8 characters."
    elif kind not in KIND_LABELS:
        error = "Please choose what kind of organization you are."
    elif form.get("terms") != "on":
        error = "Please accept the terms of service and the privacy policy."
    elif db.scalar(select(User.id).where(or_(User.email == email, User.username == email))):
        error = "An account with this e-mail already exists. Log in instead."
    if error:
        return render(request, "signup.html", None, kinds=KIND_LABELS, form=form, error=error, status_code=400)
    org = create_org(db, org_name, kind)
    start_trial(org)
    user = User(username=email, email=email, full_name=full_name, password_hash=hash_password(password),
                role=Role.admin, org_id=org.id)
    db.add(user)
    db.commit()
    request.session.clear()
    request.session["uid"] = user.id
    flash(request, f"Welcome! Your {TRIAL_DAYS}-day free trial has started. Follow the checklist below to get going.")
    return RedirectResponse("/", status_code=303)
