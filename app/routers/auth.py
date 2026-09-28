from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import current_user, flash, render
from app.models import User
from app.security import hash_password, verify_password

router = APIRouter()


def _safe_next(url: str | None) -> str:
    return url if url and url.startswith("/") and not url.startswith("//") else "/"


@router.get("/login")
def login_page(request: Request, next: str = "/"):
    return render(request, "login.html", None, next=_safe_next(next))


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/"),
          db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == username.strip()))
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        return render(request, "login.html", None, status_code=401, next=_safe_next(next),
                      error="Invalid username or password")
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
