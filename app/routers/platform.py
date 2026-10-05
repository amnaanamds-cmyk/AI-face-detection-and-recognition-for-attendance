"""Platform console for the operator of the service (superadmin): all customer organizations."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import flash, render, superadmin
from app.config import settings
from app.models import ManualPayment, Organization, Student, User
from app.services import local_payments
from app.services.billing import PLANS, plan_of
from app.services.privacy import purge_biometrics

router = APIRouter()


@router.get("/platform")
def platform(request: Request, user: User = Depends(superadmin), db: Session = Depends(get_db)):
    people = dict(db.execute(select(Student.org_id, func.count(Student.id)).group_by(Student.org_id)).all())
    users = dict(db.execute(select(User.org_id, func.count(User.id)).group_by(User.org_id)).all())
    orgs = db.scalars(select(Organization).order_by(Organization.created_at.desc())).all()
    rows = [{"org": o, "people": people.get(o.id, 0), "users": users.get(o.id, 0), "plan": plan_of(o)} for o in orgs]
    stats = {"orgs": len(orgs), "active": sum(1 for o in orgs if o.is_active),
             "paying": sum(1 for o in orgs if o.plan in ("starter", "pro", "business") and o.plan_status == "active"),
             "people": sum(people.values())}
    mrr = sum(PLANS[o.plan].price_month_usd or 0 for o in orgs
              if o.plan in PLANS and o.plan_status == "active" and o.plan != "trial")
    return render(request, "platform.html", user, rows=rows, stats=stats, mrr=mrr, plans=PLANS,
                  payments=local_payments.pending(db), methods=local_payments.METHODS, currency=settings.local_currency)


@router.post("/platform/payments/{pid}")
def decide_payment(pid: int, request: Request, action: str = Form(...), note: str = Form(""),
                   user: User = Depends(superadmin), db: Session = Depends(get_db)):
    p = db.get(ManualPayment, pid)
    if p is None:
        raise HTTPException(404)
    try:
        if action == "approve":
            local_payments.approve(db, p)
            flash(request, f"{p.org.name}: {PLANS[p.plan].name} active until {p.org.paid_until:%d %b %Y}")
        else:
            local_payments.reject(db, p, note)
            flash(request, f"{p.org.name}: payment {p.reference} rejected")
    except local_payments.PaymentError as exc:
        flash(request, str(exc), "danger")
    return RedirectResponse("/platform", status_code=303)


@router.post("/platform/orgs/{oid}")
def update_org(oid: int, request: Request, action: str = Form(...), plan: str = Form(""),
               user: User = Depends(superadmin), db: Session = Depends(get_db)):
    org = db.get(Organization, oid)
    if org is None:
        raise HTTPException(404)
    if action == "toggle":
        if org.id == user.org_id:
            flash(request, "You cannot suspend your own organization", "danger")
        else:
            org.is_active = not org.is_active
            flash(request, f"{org.name} {'activated' if org.is_active else 'suspended'}")
    elif action == "purge":
        if org.is_active:
            flash(request, "Suspend the organization before deleting its face data", "danger")
        else:
            n = purge_biometrics(db, org.id)
            flash(request, f"{org.name}: {n} face templates deleted")
    elif action == "plan" and plan in PLANS:
        org.plan, org.plan_status = plan, "active"
        flash(request, f"{org.name}: plan set to {PLANS[plan].name}")
    db.commit()
    return RedirectResponse("/platform", status_code=303)
