from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import course_scope, render, staff
from app.models import User
from app.services import analytics

router = APIRouter()


def _payload(db: Session, scope):
    return {
        "daily": analytics.daily_trend(db, 30, scope),
        "weekly": analytics.weekly_trend(db, 12, scope),
        "monthly": analytics.monthly_trend(db, scope),
        "courses": analytics.course_rates(db, scope),
        "weekday": analytics.absence_by_weekday(db, scope),
    }


@router.get("/analytics")
def analytics_page(request: Request, user: User = Depends(staff), db: Session = Depends(get_db)):
    scope = course_scope(db, user)
    return render(request, "analytics.html", user, charts=_payload(db, scope),
                  ranking=analytics.student_ranking(db, scope, 5), overall=analytics.dashboard(db, scope, org_id=user.org_id)["overall_rate"])


@router.get("/api/analytics")
def analytics_api(user: User = Depends(staff), db: Session = Depends(get_db)):
    return _payload(db, course_scope(db, user))
