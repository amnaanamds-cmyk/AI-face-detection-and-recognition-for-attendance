"""First-run checklist for a new organization (shown on the administrator's dashboard)."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ClassSession, Course, FaceEmbedding, OrgSetting, Role, SessionState, Student, User
from app.terminology import terms_of

DISMISSED = "onboarding_dismissed"


def steps(db: Session, org) -> list[dict]:
    t = terms_of(org)
    staff = db.scalar(select(func.count(User.id)).where(User.org_id == org.id, User.role == Role.teacher)) or 0
    groups = db.scalar(select(func.count(Course.id)).where(Course.org_id == org.id, Course.code.not_in(("GENERAL", "STAFF")))) or 0
    people = db.scalar(select(func.count(Student.id)).where(Student.org_id == org.id, Student.is_active.is_(True),
                                                           Student.staff_user_id.is_(None))) or 0
    with_face = db.scalar(select(func.count(func.distinct(FaceEmbedding.student_id))).join(Student)
                          .where(Student.org_id == org.id, Student.is_active.is_(True))) or 0
    taken = db.scalar(select(func.count(ClassSession.id)).join(Course)
                      .where(Course.org_id == org.id, ClassSession.state != SessionState.scheduled)) or 0
    return [
        {"title": f"Add your {t.staffs.lower()} and {t.groups.lower()}", "done": staff > 0 and groups > 0,
         "detail": f"{staff} {t.staffs.lower()}, {groups} {t.groups.lower()} - one Excel sheet does both",
         "link": "/setup/staff", "action": "Import sheet"},
        {"title": f"Add your {t.people.lower()}", "done": people > 0,
         "detail": f"{people} so far - import your class lists from Excel", "link": "/students/import", "action": "Import list"},
        {"title": "Register faces", "done": people > 0 and with_face >= people,
         "detail": f"{with_face} of {people} registered - a ZIP of photos, or the phone camera",
         "link": "/students/import", "action": "Add photos"},
        {"title": f"Give {t.staffs.lower()} the app", "done": taken > 0,
         "detail": "Android app or any phone browser - they log in with their own account", "link": "/mobile",
         "action": "Phone set-up"},
        {"title": "Take the first attendance", "done": taken > 0,
         "detail": "Start attendance, choose the class, point the camera", "link": "/sessions/new", "action": "Start"},
    ]


def visible(db: Session, org) -> bool:
    if db.scalar(select(OrgSetting.id).where(OrgSetting.org_id == org.id, OrgSetting.key == DISMISSED)):
        return False
    return not all(s["done"] for s in steps(db, org))


def dismiss(db: Session, org) -> None:
    if not db.scalar(select(OrgSetting.id).where(OrgSetting.org_id == org.id, OrgSetting.key == DISMISSED)):
        db.add(OrgSetting(org_id=org.id, key=DISMISSED, value="1"))
        db.commit()
