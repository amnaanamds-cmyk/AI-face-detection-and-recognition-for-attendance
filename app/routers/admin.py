"""Administration: user accounts, system settings, notifications, AI audit log."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import admin_only, course_scope, flash, get_student, render, staff
from app.models import ClassSession, Notification, RecognitionEvent, Role, Student, User
from app.security import hash_password
from app.services import app_settings

router = APIRouter()


@router.get("/users")
def users(request: Request, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    return render(request, "admin/users.html", user,
                  users=db.scalars(select(User).where(User.org_id == user.org_id).order_by(User.role, User.username)).all(),
                  students=db.scalars(select(Student).where(Student.org_id == user.org_id).order_by(Student.student_code)).all(),
                  roles=[r.value for r in Role])


@router.post("/users/new")
def create_user(request: Request, username: str = Form(...), full_name: str = Form(...), password: str = Form(...),
                role: str = Form(...), email: str = Form(""), student_id: int = Form(0),
                user: User = Depends(admin_only), db: Session = Depends(get_db)):
    if len(password) < 8:
        flash(request, "Password must be at least 8 characters", "danger")
        return RedirectResponse("/users", status_code=303)
    if student_id:
        get_student(db, user, student_id)
    db.add(User(username=username.strip(), full_name=full_name.strip(), password_hash=hash_password(password),
                role=Role(role), email=(email or "").strip().lower() or None, student_id=student_id or None,
                org_id=user.org_id))
    try:
        db.commit()
        flash(request, f"User {username} created")
    except IntegrityError:
        db.rollback()
        flash(request, "Username already exists (usernames are unique across the whole service)", "danger")
    return RedirectResponse("/users", status_code=303)


@router.post("/users/{uid}/toggle")
def toggle_user(uid: int, request: Request, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    target = db.get(User, uid)
    if target is None or target.org_id != user.org_id:
        raise HTTPException(404)
    if target.id == user.id:
        flash(request, "You cannot deactivate your own account", "danger")
    else:
        target.is_active = not target.is_active
        db.commit()
    return RedirectResponse("/users", status_code=303)


@router.post("/users/{uid}/password")
def reset_password(uid: int, request: Request, password: str = Form(...), user: User = Depends(admin_only),
                   db: Session = Depends(get_db)):
    target = db.get(User, uid)
    if target is None or target.org_id != user.org_id:
        raise HTTPException(404)
    if len(password) < 8:
        flash(request, "Password must be at least 8 characters", "danger")
    else:
        target.password_hash = hash_password(password)
        db.commit()
        flash(request, f"Password reset for {target.username}")
    return RedirectResponse("/users", status_code=303)


@router.get("/settings")
def settings_page(request: Request, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    from app.services.attendance import resolve_liveness_mode
    from app.vision.backends import ModelsMissingError, get_backend

    try:
        backend = get_backend()
        effective = resolve_liveness_mode(str(app_settings.get_setting(db, user.org_id, "liveness_mode")), backend)
        antispoof = bool(getattr(backend, "has_antispoof", False))
        models_ok = True
    except ModelsMissingError:
        effective, antispoof, models_ok = "motion", False, False
    return render(request, "admin/settings.html", user, values=app_settings.all_settings(db, user.org_id),
                  defs={k: v for k, v in app_settings.DEFAULTS.items() if k not in app_settings.MESSAGE_KEYS},
                  choices=app_settings.CHOICES, effective_mode=effective,
                  antispoof=antispoof, models_ok=models_ok)


@router.post("/settings")
async def save_settings(request: Request, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    form = await request.form()
    try:
        for key, (_, typ, _) in app_settings.DEFAULTS.items():
            if key in app_settings.MESSAGE_KEYS:
                continue
            if typ is bool:
                app_settings.set_setting(db, user.org_id, key, form.get(key) == "on")
            elif key in form:
                app_settings.set_setting(db, user.org_id, key, form[key])
        flash(request, "Settings saved")
    except ValueError as exc:
        flash(request, f"Invalid value: {exc}", "danger")
    return RedirectResponse("/settings", status_code=303)


@router.get("/notifications")
def notifications(request: Request, user: User = Depends(staff), db: Session = Depends(get_db)):
    q = select(Notification).order_by(Notification.created_at.desc())
    scope = course_scope(db, user)
    if scope is not None:
        q = q.where(Notification.course_id.in_(scope))
    return render(request, "admin/notifications.html", user, notes=db.scalars(q.limit(200)).all())


@router.post("/notifications/read")
def mark_read(user: User = Depends(staff), db: Session = Depends(get_db)):
    q = select(Notification).where(Notification.is_read.is_(False))
    scope = course_scope(db, user)
    if scope is not None:
        q = q.where(Notification.course_id.in_(scope))
    for n in db.scalars(q).all():
        n.is_read = True
    db.commit()
    return RedirectResponse("/notifications", status_code=303)


@router.get("/audit")
def audit(request: Request, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    events = db.scalars(select(RecognitionEvent).join(ClassSession, RecognitionEvent.session_id == ClassSession.id)
                        .where(ClassSession.course_id.in_(course_scope(db, user)))
                        .order_by(RecognitionEvent.created_at.desc()).limit(300)).all()
    return render(request, "admin/audit.html", user, events=events)
