"""Billing: subscription plans (SaaS edition) or license key (self-hosted edition)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import admin_only, flash, render, superadmin
from app.models import User
from app.services import stripe_billing
from app.services.billing import PLANS, TRIAL_DAYS, people_count, plan_of, subscription_problem
from app.services.license import LicenseError, current_license, install_license

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/billing")
def billing_page(request: Request, paid: int = 0, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    if paid:
        flash(request, "Thank you! Your subscription is being activated (this can take a few seconds).")
    org = user.org
    return render(request, "billing.html", user, plan=plan_of(org), plans=PLANS, used=people_count(db, org.id),
                  problem=subscription_problem(org), stripe=stripe_billing.configured(),
                  license=current_license(), trial_days=TRIAL_DAYS, edition=settings.edition)


@router.post("/billing/checkout")
def checkout(request: Request, plan: str = Form(...), user: User = Depends(admin_only)):
    try:
        return RedirectResponse(stripe_billing.checkout_url(user.org, plan, user.email), status_code=303)
    except stripe_billing.StripeError as exc:
        flash(request, f"Payment page not available: {exc}", "danger")
        return RedirectResponse("/billing", status_code=303)


@router.post("/billing/portal")
def portal(request: Request, user: User = Depends(admin_only)):
    try:
        return RedirectResponse(stripe_billing.portal_url(user.org), status_code=303)
    except stripe_billing.StripeError as exc:
        flash(request, str(exc), "danger")
        return RedirectResponse("/billing", status_code=303)


@router.post("/billing/webhook", include_in_schema=False)
async def webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    try:
        event = stripe_billing.verify_webhook(payload, request.headers.get("stripe-signature", ""))
    except stripe_billing.StripeError as exc:
        log.warning("Rejected Stripe webhook: %s", exc)
        raise HTTPException(400, "invalid signature") from exc
    return JSONResponse({"received": True, "result": stripe_billing.handle_event(db, event)})


@router.post("/billing/license")
def upload_license(request: Request, key: str = Form(...), user: User = Depends(superadmin)):
    try:
        lic = install_license(key)
        flash(request, f"License installed for {lic.licensee} "
                       f"({lic.max_people or 'unlimited'} people, {'expires ' + str(lic.expires) if lic.expires else 'perpetual'}).")
    except LicenseError as exc:
        flash(request, f"License not accepted: {exc}", "danger")
    return RedirectResponse("/billing", status_code=303)
