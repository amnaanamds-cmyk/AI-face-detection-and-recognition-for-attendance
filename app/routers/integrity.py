"""Admin > Integrity (tamper-evident ledger) and verifiable attendance certificates."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import admin_only, course_scope, current_user, get_student, render, staff
from app.models import Attendance, LedgerEntry, Role, Student, User
from app.routers.mobile import public_url
from app.services import certificates, ledger

router = APIRouter()


@router.get("/integrity")
def integrity(request: Request, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    report = ledger.verify(db, user.org_id)
    recent = db.scalars(select(LedgerEntry).where(LedgerEntry.org_id == user.org_id)
                        .order_by(LedgerEntry.id.desc()).limit(50)).all()
    return render(request, "admin/integrity.html", user, r=report, recent=recent, kid=certificates.key_id())


def _verify_url(request: Request) -> str:
    return (public_url(request) or str(request.base_url).rstrip("/")) + "/verify"


def _certificate(request: Request, db: Session, st: Student, start: str, end: str) -> Response:
    first = db.scalar(select(func.min(Attendance.date)).where(Attendance.student_id == st.id))
    d0 = date.fromisoformat(start) if start else (first or date.today())
    d1 = date.fromisoformat(end) if end else date.today()
    data = certificates.build(db, st, d0, d1)
    token = certificates.sign(data)
    pdf = certificates.pdf(data, token, _verify_url(request))
    name = f"attendance-certificate-{st.student_code}.pdf".replace(" ", "_")
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/students/{sid}/certificate")
def student_certificate(sid: int, request: Request, start: str = "", end: str = "", user: User = Depends(staff),
                        db: Session = Depends(get_db)):
    st = get_student(db, user, sid)
    if user.role != Role.admin and not ({e.course_id for e in st.enrollments} & set(course_scope(db, user))):
        raise HTTPException(403, "This person is not in your groups")
    return _certificate(request, db, st, start, end)


@router.get("/me/certificate")
def my_certificate(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.student_id is None:
        return RedirectResponse("/", status_code=303)
    return _certificate(request, db, user.student, "", "")


@router.get("/verify")
def verify_page(request: Request, c: str = ""):
    result = certificates.verify(c) if c else None
    return render(request, "verify.html", None, result=result, code=c, kid=certificates.key_id())
