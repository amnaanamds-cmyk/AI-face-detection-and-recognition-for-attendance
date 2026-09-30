"""Fill the database with demo courses, students and 6 weeks of attendance history
so the dashboard, analytics and reports can be shown without real data.

    python scripts/seed_demo.py            # creates teacher 'teacher' / 'teacher123'

Demo students have no face templates - register real faces for live testing.
"""
from __future__ import annotations

import random
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app import database  # noqa: E402
from app.main import bootstrap_admin  # noqa: E402
from app.models import ClassSession, Course, Enrollment, Organization, Role, SessionState, Student, User  # noqa: E402
from app.tenancy import upgrade_database  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services import attendance as att  # noqa: E402

COURSES = [("CS-401", "Artificial Intelligence", time(9, 0)), ("CS-403", "Computer Vision", time(11, 0)),
           ("CS-405", "Software Engineering", time(14, 0))]


def main() -> int:
    database.init_db()
    upgrade_database(database.engine)
    bootstrap_admin()
    rng = random.Random(42)
    with database.SessionLocal() as db:
        org = db.scalar(select(Organization).order_by(Organization.id).limit(1))
        if db.scalar(select(Course.id).where(Course.org_id == org.id, Course.code == "CS-401")):
            print("Demo data already present")
            return 0
        teacher = User(username="teacher", password_hash=hash_password("teacher123"), full_name="Dr. Ahmad",
                       role=Role.teacher, org_id=org.id)
        db.add(teacher)
        students = [Student(student_code=f"BSCS-2023-{i:03d}", roll_number=str(i), name=f"Demo Student {i:02d}",
                            semester=7, section="A", consent_given=True, org_id=org.id) for i in range(1, 26)]
        db.add_all(students)
        db.flush()
        diligence = {s.id: rng.uniform(0.55, 0.98) for s in students}
        today = date.today()
        for code, name, start in COURSES:
            c = Course(code=code, name=name, semester=7, section="A", teacher_id=teacher.id, org_id=org.id)
            db.add(c)
            db.flush()
            db.add_all(Enrollment(student_id=s.id, course_id=c.id) for s in students)
            db.commit()
            for d in range(42, 0, -1):
                day = today - timedelta(days=d)
                if day.weekday() >= 5:
                    continue
                sess = ClassSession(course_id=c.id, date=day, start_time=datetime.combine(day, start),
                                    state=SessionState.active, created_by=teacher.id)
                db.add(sess)
                db.commit()
                for s in students:
                    if rng.random() < diligence[s.id]:
                        delay = rng.choice([1, 2, 3, 5, 7, 9, 12, 15, 18])
                        att.mark_attendance(db, sess, s.id, similarity=rng.uniform(0.55, 0.85),
                                            liveness_score=1.0, when=sess.start_time + timedelta(minutes=delay))
                att.close_session(db, sess)
        print("Demo data created: 25 students, 3 courses, ~6 weeks of sessions. Teacher login: teacher / teacher123")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
