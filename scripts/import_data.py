"""Bulk-import a real class list and face photos.

    # class list: CSV or Excel with columns like Student ID, Name, Roll No, Department,
    # Semester, Section, Email, Phone, Consent (yes/no)
    python scripts/import_data.py --students bscs7A.xlsx

    # face photos: one folder per student ID (or files named <StudentID>_1.jpg ...)
    python scripts/import_data.py --faces photos/            # or photos.zip
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import database  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.models import Organization  # noqa: E402
from app.services import importer  # noqa: E402
from app.tenancy import upgrade_database  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--students", type=Path, help="CSV / XLSX class list")
    ap.add_argument("--faces", type=Path, help="folder or ZIP of face photos")
    ap.add_argument("--consent", action="store_true",
                    help="mark imported students as having given biometric consent (only if you collected it!)")
    ap.add_argument("--org", help="organization short name (slug); default: the first organization")
    ap.add_argument("--no-auto-enroll", action="store_true", help="do not enrol students in matching courses")
    args = ap.parse_args()
    if not args.students and not args.faces:
        ap.print_help()
        return 1
    database.init_db()
    upgrade_database(database.engine)
    with database.SessionLocal() as db:
        q = select(Organization).order_by(Organization.id)
        org = db.scalar(q.where(Organization.slug == args.org) if args.org else q.limit(1))
        if org is None:
            print("No such organization" if args.org else "No organization yet - start the server once first")
            return 1
        if args.students:
            rows = importer.read_table(args.students.name, args.students.read_bytes())
            rep = importer.import_students(db, org.id, rows, default_consent=args.consent, auto_enroll=not args.no_auto_enroll)
            print(f"Students: {rep.created} created, {rep.updated} updated, {rep.enrolled_courses} course enrolments")
            for e in rep.errors:
                print("  !", e)
        if args.faces:
            files = importer.read_zip(args.faces.read_bytes()) if args.faces.suffix.lower() == ".zip" \
                else importer.read_folder(args.faces)
            rep = importer.import_faces(db, org.id, files)
            print(f"Faces: {rep.templates} template(s) for {rep.students} student(s)")
            for m in rep.messages:
                print("  -", m)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
