"""Bulk import pages (registered before the students router so /students/import is not
captured by /students/{sid})."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import admin_only, render, require_active_subscription
from app.models import User
from app.services import importer
from app.vision.backends import ModelsMissingError

router = APIRouter()

TEMPLATE_CSV = ("Student ID,Name,Roll No,Department,Semester,Section,Email,Phone,Consent\n"
                "BSCS-2023-001,Amina Bibi,1,Computer Science,7,A,amina@example.edu,03001234567,yes\n")


@router.get("/students/import")
def import_page(request: Request, user: User = Depends(admin_only)):
    return render(request, "students/import.html", user)


@router.get("/students/import/template.csv")
def template_csv(user: User = Depends(admin_only)):
    return Response(TEMPLATE_CSV.encode("utf-8-sig"), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="students_template.csv"'})


@router.post("/students/import")
async def import_students(request: Request, file: UploadFile = File(...), consent: str = Form(""),
                          auto_enroll: str = Form(""), user: User = Depends(admin_only),
                          db: Session = Depends(get_db)):
    try:
        rows = importer.read_table(file.filename or "", await file.read())
        rep = importer.import_students(db, user.org_id, rows, default_consent=consent == "on", auto_enroll=auto_enroll == "on")
        result = {"kind": "students", "created": rep.created, "updated": rep.updated,
                  "enrolled": rep.enrolled_courses, "messages": rep.errors}
    except Exception as exc:  # malformed file -> show the reason instead of a 500
        result = {"kind": "students", "created": 0, "updated": 0, "enrolled": 0, "messages": [f"could not read file: {exc}"]}
    return render(request, "students/import.html", user, result=result)


@router.post("/students/import/faces")
async def import_faces(request: Request, file: UploadFile = File(...), user: User = Depends(admin_only),
                       db: Session = Depends(get_db)):
    try:
        require_active_subscription(user)
        rep = importer.import_faces(db, user.org_id, importer.read_zip(await file.read()))
        result = {"kind": "faces", "students": rep.students, "templates": rep.templates, "messages": rep.messages}
    except HTTPException as exc:
        result = {"kind": "faces", "students": 0, "templates": 0, "messages": [str(exc.detail)]}
    except ModelsMissingError as exc:
        result = {"kind": "faces", "students": 0, "templates": 0, "messages": [str(exc)]}
    except Exception as exc:
        result = {"kind": "faces", "students": 0, "templates": 0, "messages": [f"could not read ZIP: {exc}"]}
    return render(request, "students/import.html", user, result=result)
