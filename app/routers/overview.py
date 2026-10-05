from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import admin_only, render
from app.models import User
from app.services import overview as ov
from app.services.app_settings import get_setting
from app.services.attendance import sync_scheduled_sessions

router = APIRouter()


def _data(db: Session, user: User, period: str, start: str, end: str) -> dict:
    sync_scheduled_sessions(db, user.org_id)
    first, last = ov.period_range(period, start=start, end=end)
    return ov.overview(db, user.org_id, first, last, threshold=get_setting(db, user.org_id, "low_attendance_threshold"))


@router.get("/overview")
def overview_page(request: Request, period: str = "today", start: str = "", end: str = "",
                  user: User = Depends(admin_only), db: Session = Depends(get_db)):
    """The principal's view of the whole school (every teacher's phone reports here)."""
    period = period if period in ov.PERIODS else "today"
    return render(request, "overview.html", user, d=_data(db, user, period, start, end), period=period,
                  periods=ov.PERIODS, custom_start=start, custom_end=end)


@router.get("/overview.xlsx")
def overview_xlsx(period: str = "today", start: str = "", end: str = "",
                  user: User = Depends(admin_only), db: Session = Depends(get_db)):
    d = _data(db, user, period, start, end)
    name = f"school_overview_{d['start'] or 'all'}_{d['end']}.xlsx"
    return Response(ov.to_xlsx(d, user.org.name if user.org else ""),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
