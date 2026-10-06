from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.terminology import terms_of
from app.deps import can_manage_course, course_scope, render, staff
from app.models import ClassSession, Course, User
from app.services import reports

router = APIRouter()


def _scope(db: Session, user: User, course_id: int) -> list[int] | None:
    scope = course_scope(db, user)
    if course_id:
        if scope is not None and course_id not in scope:
            raise HTTPException(403, "Not your course")
        return [course_id]
    return scope


def _respond(request: Request, user: User, rep: reports.Report, fmt: str, filename: str):
    from app.database import SessionLocal
    from app.services import ledger
    with SessionLocal() as db:
        rep.footer = (f"Tamper-evident ledger fingerprint at export: {ledger.fingerprint(db, user.org_id)} - "
                      "keep this report: the integrity check (Admin > Integrity) proves the records still match it.")
    if fmt == "html":
        return render(request, "report_view.html", user, report=rep, query=request.url.query)
    if fmt not in reports.EXPORTERS:
        raise HTTPException(400, "Unknown format")
    fn, mime = reports.EXPORTERS[fmt]
    return Response(fn(rep), media_type=mime,
                    headers={"Content-Disposition": f'attachment; filename="{filename}.{fmt}"'})


@router.get("/reports")
def reports_page(request: Request, user: User = Depends(staff), db: Session = Depends(get_db)):
    q = select(Course).order_by(Course.code)
    scope = course_scope(db, user)
    if scope is not None:
        q = q.where(Course.id.in_(scope))
    today = date.today()
    return render(request, "reports.html", user, courses=db.scalars(q).all(), today=today.isoformat(),
                  month=today.strftime("%Y-%m"))


@router.get("/reports/daily")
def daily(request: Request, day: str = "", course_id: int = 0, fmt: str = "html",
          user: User = Depends(staff), db: Session = Depends(get_db)):
    d = date.fromisoformat(day) if day else date.today()
    rep = reports.daily_report(db, d, _scope(db, user, course_id), terms_of(user.org))
    return _respond(request, user, rep, fmt, f"attendance_daily_{d.isoformat()}")


@router.get("/reports/monthly")
def monthly(request: Request, month: str = "", course_id: int = 0, fmt: str = "html",
            user: User = Depends(staff), db: Session = Depends(get_db)):
    y, m = (int(x) for x in (month or date.today().strftime("%Y-%m")).split("-"))
    rep = reports.monthly_report(db, y, m, _scope(db, user, course_id), terms_of(user.org))
    return _respond(request, user, rep, fmt, f"attendance_monthly_{y}-{m:02d}")


@router.get("/reports/session/{sid}")
def session_report(sid: int, request: Request, fmt: str = "html", user: User = Depends(staff),
                   db: Session = Depends(get_db)):
    s = db.get(ClassSession, sid)
    if s is None or not can_manage_course(user, s.course):
        raise HTTPException(404, "Session not found")
    return _respond(request, user, reports.session_report(db, s, terms_of(user.org)), fmt, f"attendance_session_{sid}")


@router.get("/reports/register")
def register(request: Request, course_id: int = 0, month: str = "", fmt: str = "html",
             user: User = Depends(staff), db: Session = Depends(get_db)):
    """Monthly attendance register (students x days), as on paper."""
    from app.services import register as reg_service

    course = db.get(Course, course_id)
    if course is None or not can_manage_course(user, course):
        raise HTTPException(404, "Choose one of your " + terms_of(user.org).groups.lower())
    try:
        y, m = (int(x) for x in (month or date.today().strftime("%Y-%m")).split("-"))
        reg = reg_service.build(db, course, y, m)
    except ValueError as exc:
        raise HTTPException(400, "Month must look like 2026-10") from exc
    name = f"register_{course.code}_{y}-{m:02d}"
    org_name = user.org.name if user.org else ""
    if fmt == "xlsx":
        return Response(reg_service.to_xlsx(reg, org_name), headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'},
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if fmt == "pdf":
        return Response(reg_service.to_pdf(reg, org_name), media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})
    return render(request, "register.html", user, reg=reg, month=f"{y}-{m:02d}")
