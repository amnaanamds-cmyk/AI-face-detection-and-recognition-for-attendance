"""Plans and usage limits (SaaS subscriptions and self-hosted licenses)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Organization, Student, now


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    max_people: int | None      # None = unlimited
    price_month_usd: int | None  # shown on the pricing page
    stripe_price_env: str = ""   # environment variable holding the Stripe price id
    price_month_local: int | None = None  # local payments (PKR by default)


PLANS: dict[str, Plan] = {
    "trial": Plan("trial", "Free trial (14 days)", 50, 0),
    "starter": Plan("starter", "Starter", 100, 29, "STRIPE_PRICE_STARTER", 3000),
    "pro": Plan("pro", "Pro", 1000, 99, "STRIPE_PRICE_PRO", 9000),
    "business": Plan("business", "Business", 10000, 299, "STRIPE_PRICE_BUSINESS", 25000),
    "selfhosted": Plan("selfhosted", "Self-hosted", None, None),
}
PAID_PLANS = ("starter", "pro", "business")
MONTH_CHOICES = {1: 1, 3: 3, 6: 6, 12: 10}     # months paid for -> months charged (a year = 10 months)


def local_price(plan: str, months: int) -> int:
    return (PLANS[plan].price_month_local or 0) * MONTH_CHOICES[months]
TRIAL_DAYS = 14


def plan_of(org: Organization) -> Plan:
    if settings.edition == "selfhosted":
        from app.services.license import current_license

        lic = current_license()
        return Plan("selfhosted", "Self-hosted", lic.max_people if lic else settings.unlicensed_max_people, None)
    return PLANS.get(org.plan, PLANS["trial"])


def people_count(db: Session, org_id: int) -> int:
    return db.scalar(select(func.count(Student.id)).where(Student.org_id == org_id, Student.is_active.is_(True),
                                                         Student.staff_user_id.is_(None))) or 0


def subscription_problem(org: Organization) -> str | None:
    """Reason why the organization cannot use paid features right now (None = fine)."""
    if settings.edition == "selfhosted":
        return None
    if org.plan == "trial" and org.trial_ends_at and org.trial_ends_at < now():
        return "Your free trial has ended. Choose a plan under Billing to continue."
    if org.plan_status in ("past_due", "unpaid"):
        return "Your last payment failed. Update your payment method under Billing."
    if org.plan_status == "canceled":
        return "Your subscription was canceled. Choose a plan under Billing to continue."
    if (org.plan in PAID_PLANS and org.paid_until and org.paid_until < now()
            and not org.stripe_subscription_id):
        return f"Your paid period ended on {org.paid_until:%d %b %Y}. Renew under Billing to continue."
    return None


def days_left(org: Organization) -> int | None:
    """Days until a locally paid plan or the trial runs out (None = not time-limited)."""
    end = org.trial_ends_at if org.plan == "trial" else (org.paid_until if not org.stripe_subscription_id else None)
    return None if end is None else (end - now()).days


def plan_limit_error(db: Session, org: Organization, adding: int = 1) -> str | None:
    """Message if adding `adding` people would exceed the plan, else None."""
    plan = plan_of(org)
    if plan.max_people is None:
        return None
    if people_count(db, org.id) + adding > plan.max_people:
        return (f"Your plan ({plan.name}) allows {plan.max_people} people. "
                + ("Upgrade under Billing." if settings.edition == "saas" else "Install a license for more people."))
    return None


def start_trial(org: Organization) -> None:
    org.plan, org.plan_status = "trial", "active"
    org.trial_ends_at = now() + timedelta(days=TRIAL_DAYS)
