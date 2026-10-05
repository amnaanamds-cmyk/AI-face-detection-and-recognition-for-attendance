"""Bulk set-up of a new school: teachers and their subjects from one Excel / CSV sheet.

One row per subject (course):  Subject code, Subject, Class, Section, Teacher, [Teacher username], [Teacher email], [Department]

* a teacher account is created the first time a teacher name appears, with a random password that
  is shown once (to print and hand out); a teacher who already exists is reused;
* a subject that already exists (same code) gets its name / class / teacher updated;
* students of the same class and section are enrolled automatically.
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Course, Enrollment, Organization, Role, Student, User
from app.security import hash_password
from app.services.accounts import username_taken
from app.services.importer import _norm, class_number

ALIASES = {
    "subjectcode": "code", "coursecode": "code", "code": "code", "subjectid": "code",
    "subject": "name", "subjectname": "name", "course": "name", "coursename": "name", "title": "name",
    "class": "semester", "grade": "semester", "semester": "semester", "sem": "semester",
    "section": "section", "sec": "section",
    "teacher": "teacher", "teachername": "teacher", "instructor": "teacher", "lecturer": "teacher",
    "faculty": "teacher", "manager": "teacher",
    "teacherusername": "username", "username": "username", "login": "username",
    "teacheremail": "email", "email": "email",
    "department": "department", "dept": "department", "program": "department", "wing": "department",
}
TEMPLATE_CSV = ("Subject code,Subject,Class,Section,Teacher,Teacher username,Teacher email\n"
                "MATH-9A,Mathematics,9,A,Ayesha Khan,ayesha.khan,ayesha@school.edu.pk\n"
                "ENG-9A,English,9,A,Imran Ali,,\n"
                "PHY-9A,Physics,9,A,Ayesha Khan,ayesha.khan,\n")
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # no 0/o, 1/l/i: easy to read from a printout


@dataclass
class StaffReport:
    teachers: list[dict] = field(default_factory=list)   # new accounts: name, username, password
    reused_teachers: int = 0
    subjects_created: int = 0
    subjects_updated: int = 0
    enrolled: int = 0
    errors: list[str] = field(default_factory=list)


def new_password() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(8))


def _username(db: Session, org: Organization, wanted: str, full_name: str) -> str:
    base = re.sub(r"[^a-z0-9.]+", "", (wanted or ".".join(full_name.lower().split()[:2])).lower()) or "teacher"
    candidate, n = base, 2
    if username_taken(db, candidate):                    # usernames are unique across all schools
        candidate = f"{org.slug}.{base}"
    while username_taken(db, candidate):
        candidate, n = f"{org.slug}.{base}{n}", n + 1
    return candidate


def import_staff(db: Session, org: Organization, rows: list[dict[str, str]]) -> StaffReport:
    rep = StaffReport()
    if not rows:
        rep.errors.append("the file is empty")
        return rep
    mapped = {ALIASES[_norm(c)] for c in rows[0] if _norm(c) in ALIASES}
    if not {"code", "name"} <= mapped:
        rep.errors.append(f"the file needs at least 'Subject code' and 'Subject' columns (found: {', '.join(rows[0])})")
        return rep
    unknown = [c for c in rows[0] if _norm(c) not in ALIASES]
    if unknown:
        rep.errors.append(f"ignored columns: {', '.join(unknown)}")

    teachers = {u.full_name.strip().lower(): u for u in db.scalars(
        select(User).where(User.org_id == org.id, User.role.in_((Role.teacher, Role.admin)))).all()}
    by_username = {u.username: u for u in teachers.values()}
    touched: list[Course] = []
    created_ids: set[int] = set()
    reused_ids: set[int] = set()
    for line, raw in enumerate(rows, start=2):
        d = {ALIASES[_norm(k)]: (v or "").strip() for k, v in raw.items() if _norm(k) in ALIASES}
        code, name = d.get("code", ""), d.get("name", "")
        if not code or not name:
            rep.errors.append(f"row {line}: missing subject code or name - skipped")
            continue
        try:
            semester = class_number(d["semester"]) if d.get("semester") else 1
        except ValueError:
            rep.errors.append(f"row {line}: class '{d['semester']}' is not a number - skipped")
            continue

        teacher = None
        tname = d.get("teacher", "")
        if tname or d.get("username"):
            teacher = by_username.get(d.get("username", "").lower()) or teachers.get(tname.lower())
            if teacher is None:
                if not tname:
                    rep.errors.append(f"row {line}: teacher username '{d['username']}' not found and no teacher name given")
                else:
                    password = new_password()
                    teacher = User(org_id=org.id, role=Role.teacher, full_name=tname,
                                   username=_username(db, org, d.get("username", ""), tname),
                                   email=d.get("email", "").lower() or None, password_hash=hash_password(password))
                    db.add(teacher)
                    db.flush()
                    teachers[tname.lower()] = by_username[teacher.username] = teacher
                    created_ids.add(teacher.id)
                    rep.teachers.append({"name": tname, "username": teacher.username, "password": password})
            if teacher is not None and teacher.id not in created_ids:
                reused_ids.add(teacher.id)

        course = db.scalar(select(Course).where(Course.org_id == org.id, Course.code == code))
        if course is None:
            course = Course(org_id=org.id, code=code, name=name, department=d.get("department", ""),
                            semester=semester, section=d.get("section", "") or "A")
            db.add(course)
            rep.subjects_created += 1
        else:
            course.name, course.semester = name, semester
            course.section = d.get("section", "") or course.section
            if "department" in d:
                course.department = d["department"]
            rep.subjects_updated += 1
        if teacher is not None:
            course.teacher_id = teacher.id
        db.flush()
        touched.append(course)

    for c in touched:                                    # enrol the matching class
        have = set(db.scalars(select(Enrollment.student_id).where(Enrollment.course_id == c.id)).all())
        q = select(Student.id).where(Student.org_id == org.id, Student.is_active.is_(True),
                                     Student.semester == c.semester, Student.section == c.section)
        if c.department:
            q = q.where(Student.department == c.department)
        for sid in db.scalars(q).all():
            if sid not in have:
                db.add(Enrollment(student_id=sid, course_id=c.id))
                rep.enrolled += 1
    rep.reused_teachers = len(reused_ids)
    db.commit()
    return rep
