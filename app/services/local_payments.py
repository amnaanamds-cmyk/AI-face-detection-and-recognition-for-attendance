"""Local payments for countries without card billing (Pakistan: bank transfer, JazzCash, Easypaisa).

The school pays to the account shown on its Billing page and enters the transaction ID. The operator
checks the bank / wallet statement and approves it in the platform console, which activates the plan
until a date (`Organization.paid_until`). Renewing early adds the new months to the end of the old period.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ManualPayment, Organization, User, now
from app.services.billing import MONTH_CHOICES, PAID_PLANS, local_price

METHODS = {"bank": "Bank transfer", "jazzcash": "JazzCash", "easypaisa": "Easypaisa"}


class PaymentError(ValueError):
    pass


def enabled() -> bool:
    return bool(settings.local_payment_details.strip())


def submit(db: Session, org: Organization, user: User, plan: str, months: int, method: str, reference: str,
           payer: str = "") -> ManualPayment:
    reference = reference.strip()
    if plan not in PAID_PLANS:
        raise PaymentError("choose a plan")
    if months not in MONTH_CHOICES:
        raise PaymentError("choose 1, 3, 6 or 12 months")
    if method not in METHODS:
        raise PaymentError("choose how you paid")
    if len(reference) < 4:
        raise PaymentError("enter the transaction ID or receipt number from your bank / wallet")
    if db.scalar(select(ManualPayment.id).where(ManualPayment.reference == reference, ManualPayment.status != "rejected")):
        raise PaymentError("this transaction ID was already submitted")
    p = ManualPayment(org_id=org.id, plan=plan, months=months, amount=local_price(plan, months), method=method,
                      reference=reference[:80], payer=payer.strip()[:120], created_by=user.id)
    db.add(p)
    db.commit()
    return p


def approve(db: Session, p: ManualPayment) -> None:
    if p.status != "pending":
        raise PaymentError("this payment was already decided")
    org = p.org
    start = org.paid_until if org.paid_until and org.paid_until > now() and org.plan in PAID_PLANS else now()
    org.paid_until = start + timedelta(days=30 * p.months)
    org.plan, org.plan_status = p.plan, "active"
    p.status, p.decided_at = "approved", now()
    db.commit()


def reject(db: Session, p: ManualPayment, note: str = "") -> None:
    if p.status != "pending":
        raise PaymentError("this payment was already decided")
    p.status, p.decided_at, p.note = "rejected", now(), (note.strip() or "payment not found")[:255]
    db.commit()


def history(db: Session, org_id: int) -> list[ManualPayment]:
    return db.scalars(select(ManualPayment).where(ManualPayment.org_id == org_id)
                      .order_by(ManualPayment.created_at.desc()).limit(20)).all()


def pending(db: Session) -> list[ManualPayment]:
    return db.scalars(select(ManualPayment).where(ManualPayment.status == "pending")
                      .order_by(ManualPayment.created_at)).all()
