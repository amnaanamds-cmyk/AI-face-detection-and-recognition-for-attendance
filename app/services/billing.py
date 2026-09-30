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


PLANS: dict[str, Plan] = {
    "trial": Plan("trial", "Free trial (14 days)", 50, 0),
    "starter": Plan("starter", "Starter", 100, 29, "STRIPE_PRICE_STARTER"),
    "pro": Plan("pro", "Pro", 1000, 99, "STRIPE_PRICE_PRO"),
    "business": Plan("business", "Business", 10000, 299, "STRIPE_PRICE_BUSINESS"),
    "selfhosted": Plan("selfhosted", "Self-hosted", None, None),
}
TRIAL_DAYS = 14


def plan_of(org: Organization) -> Plan:
    if settings.edition == "selfhosted":
        from app.services.license import current_license

        lic = current_license()
        return Plan("selfhosted", "Self-hosted", lic.max_people if lic else settings.unlicensed_max_people, None)
    return PLANS.get(org.plan, PLANS["trial"])


def people_count(db: Session, org_id: int) -> int:
    return db.scalar(select(func.count(Student.id)).where(Student.org_id == org_id, Student.is_active.is_(True))) or 0


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
    return None


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
