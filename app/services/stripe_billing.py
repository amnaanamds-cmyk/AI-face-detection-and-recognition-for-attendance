"""Stripe subscriptions for the SaaS edition (Checkout, Customer Portal and webhooks).

Uses Stripe's REST API directly (no extra dependency). Configure in the environment:
STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, STRIPE_PRICE_STARTER / _PRO / _BUSINESS, PUBLIC_BASE_URL.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Organization
from app.services.billing import PLANS

log = logging.getLogger(__name__)
API = "https://api.stripe.com/v1/"


class StripeError(RuntimeError):
    pass


def configured() -> bool:
    return bool(settings.stripe_secret_key)


def price_id(plan_key: str) -> str:
    plan = PLANS.get(plan_key)
    return os.environ.get(plan.stripe_price_env, "") if plan and plan.stripe_price_env else ""


def plan_for_price(price: str) -> str | None:
    for key, plan in PLANS.items():
        if plan.stripe_price_env and os.environ.get(plan.stripe_price_env) == price:
            return key
    return None


def _flatten(data: dict, prefix: str = "") -> list[tuple[str, str]]:
    out = []
    for k, v in data.items():
        key = f"{prefix}[{k}]" if prefix else k
        if isinstance(v, dict):
            out += _flatten(v, key)
        elif isinstance(v, list):
            for i, item in enumerate(v):
                out += _flatten(item, f"{key}[{i}]") if isinstance(item, dict) else [(f"{key}[{i}]", str(item))]
        elif v is not None:
            out.append((key, str(v)))
    return out


def stripe_post(path: str, data: dict) -> dict:
    """POST to the Stripe API (form-encoded, as Stripe expects)."""
    if not configured():
        raise StripeError("Stripe is not configured (STRIPE_SECRET_KEY)")
    req = urllib.request.Request(API + path, data=urllib.parse.urlencode(_flatten(data)).encode(),
                                 headers={"Authorization": f"Bearer {settings.stripe_secret_key}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        log.warning("Stripe error %s: %s", exc.code, detail)
        raise StripeError(f"Stripe error {exc.code}") from exc


def checkout_url(org: Organization, plan_key: str, email: str | None) -> str:
    price = price_id(plan_key)
    if not price:
        raise StripeError(f"No Stripe price configured for plan '{plan_key}'")
    base = settings.public_base_url.rstrip("/")
    data = {
        "mode": "subscription",
        "line_items": [{"price": price, "quantity": 1}],
        "success_url": f"{base}/billing?paid=1",
        "cancel_url": f"{base}/billing",
        "client_reference_id": str(org.id),
        "metadata": {"org_id": org.id, "plan": plan_key},
        "subscription_data": {"metadata": {"org_id": org.id, "plan": plan_key}},
        "allow_promotion_codes": "true",
    }
    if org.stripe_customer_id:
        data["customer"] = org.stripe_customer_id
    elif email:
        data["customer_email"] = email
    return stripe_post("checkout/sessions", data)["url"]


def portal_url(org: Organization) -> str:
    if not org.stripe_customer_id:
        raise StripeError("No Stripe customer yet - choose a plan first")
    return stripe_post("billing_portal/sessions", {"customer": org.stripe_customer_id,
                                                   "return_url": settings.public_base_url.rstrip("/") + "/billing"})["url"]


def verify_webhook(payload: bytes, signature_header: str, secret: str | None = None, tolerance: int = 300) -> dict:
    """Check Stripe's `Stripe-Signature` header (HMAC-SHA256 of "timestamp.payload")."""
    secret = secret or settings.stripe_webhook_secret
    if not secret:
        raise StripeError("STRIPE_WEBHOOK_SECRET is not set")
    parts = dict(p.split("=", 1) for p in signature_header.split(",") if "=" in p)
    sigs = [v for k, v in (p.split("=", 1) for p in signature_header.split(",") if "=" in p) if k == "v1"]
    try:
        ts = int(parts.get("t", ""))
    except ValueError as exc:
        raise StripeError("bad signature header") from exc
    if abs(time.time() - ts) > tolerance:
        raise StripeError("webhook timestamp too old")
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, s) for s in sigs):
        raise StripeError("invalid webhook signature")
    return json.loads(payload)


def _org_for(db: Session, obj: dict) -> Organization | None:
    meta = obj.get("metadata") or {}
    org_id = meta.get("org_id") or obj.get("client_reference_id")
    if org_id:
        return db.get(Organization, int(org_id))
    customer = obj.get("customer")
    if customer:
        return db.scalar(select(Organization).where(Organization.stripe_customer_id == customer))
    return None


STATUS_MAP = {"active": "active", "trialing": "active", "past_due": "past_due", "unpaid": "unpaid",
              "canceled": "canceled", "incomplete_expired": "canceled", "incomplete": "past_due"}


def handle_event(db: Session, event: dict) -> str:
    """Apply a verified Stripe event to the organization's plan. Returns a short description."""
    kind, obj = event.get("type", ""), (event.get("data") or {}).get("object") or {}
    org = _org_for(db, obj)
    if org is None:
        return f"ignored {kind} (no organization)"
    if kind == "checkout.session.completed":
        org.stripe_customer_id = obj.get("customer") or org.stripe_customer_id
        org.stripe_subscription_id = obj.get("subscription") or org.stripe_subscription_id
        plan = (obj.get("metadata") or {}).get("plan")
        if plan in PLANS:
            org.plan = plan
        org.plan_status = "active"
    elif kind in ("customer.subscription.created", "customer.subscription.updated"):
        org.stripe_subscription_id = obj.get("id") or org.stripe_subscription_id
        org.stripe_customer_id = obj.get("customer") or org.stripe_customer_id
        items = ((obj.get("items") or {}).get("data") or [])
        plan = plan_for_price(((items[0].get("price") or {}).get("id")) if items else "") or (obj.get("metadata") or {}).get("plan")
        if plan in PLANS:
            org.plan = plan
        org.plan_status = STATUS_MAP.get(obj.get("status", ""), org.plan_status)
    elif kind == "customer.subscription.deleted":
        org.plan_status = "canceled"
    elif kind == "invoice.payment_failed":
        org.plan_status = "past_due"
    elif kind == "invoice.paid":
        if org.plan_status in ("past_due", "unpaid"):
            org.plan_status = "active"
    else:
        return f"ignored {kind}"
    db.commit()
    return f"{kind}: {org.slug} -> {org.plan}/{org.plan_status}"
