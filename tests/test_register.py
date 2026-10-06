"""Monthly attendance register (students x days)."""
import io
from datetime import date, datetime

from openpyxl import load_workbook

from app import database
from app.models import Attendance, AttendanceStatus as S, ClassSession, Course, Holiday, SessionState, Student
from app.services import register
from tests.conftest import login


def test_register_marks_and_exports(client):
    login(client)
    client.post("/users/new", data={"username": "t1", "full_name": "T One", "password": "teacher123", "role": "teacher"})
    client.post("/users/new", data={"username": "t2", "full_name": "T Two", "password": "teacher123", "role": "teacher"})
    client.post("/courses/new", data={"code": "MATH", "name": "Mathematics", "semester": 9, "section": "A", "teacher_id": 2})
    client.post("/courses/new", data={"code": "ENG", "name": "English", "semester": 9, "section": "A", "teacher_id": 3})
    for i, n in enumerate(["Amina", "Bilal"], 1):
        client.post("/students/new", data={"student_code": f"S{i}", "name": n, "roll_number": str(i), "semester": 9,
                                           "section": "A", "department": "Computer Science", "consent": "on", "auto_enroll": "on"})
    db = database.SessionLocal()
    math = db.query(Course).filter_by(code="MATH").one()
    a, b = db.query(Student).order_by(Student.student_code).all()

    def period(day, hour, marks):
        s = ClassSession(course_id=math.id, date=day, start_time=datetime.combine(day, datetime.min.time()).replace(hour=hour),
                         state=SessionState.closed)
        db.add(s)
        db.flush()
        for st, status in marks.items():
            db.add(Attendance(student_id=st.id, session_id=s.id, course_id=math.id, date=day, status=status))

    period(date(2026, 10, 5), 8, {a: S.present, b: S.absent})
    period(date(2026, 10, 6), 8, {a: S.present, b: S.present})
    period(date(2026, 10, 6), 11, {a: S.late, b: S.absent})          # two periods that day
    period(date(2026, 10, 7), 8, {a: S.leave, b: S.present})
    db.add(Holiday(org_id=math.org_id, start=date(2026, 10, 9), end=date(2026, 10, 9), name="Iqbal Day"))
    db.commit()

    reg = register.build(db, math, 2026, 10)
    rows = {r["student"].student_code: r for r in reg["rows"]}
    marks = lambda code, day: rows[code]["marks"][date(2026, 10, day)]
    assert [marks("S1", d) for d in (5, 6, 7, 8, 9)] == ["P", "L", "E", "", "H"]
    assert [marks("S2", d) for d in (5, 6, 7)] == ["A", "P/A", "P"]
    assert (rows["S2"]["P"], rows["S2"]["A"], rows["S2"]["pct"]) == (2, 2, 50.0)
    assert reg["present_per_day"][date(2026, 10, 6)] == 2 and len(reg["days"]) == 31
    db.close()

    page = client.get(f"/reports/register?course_id={math.id}&month=2026-10")
    assert page.status_code == 200 and "Amina" in page.text and "P/A" in page.text
    x = client.get(f"/reports/register?course_id={math.id}&month=2026-10&fmt=xlsx")
    ws = load_workbook(io.BytesIO(x.content)).active
    assert ws.cell(4, 3).value == "Amina" and ws.cell(4, 4 + 4).value == "P" and ws.cell(4, 4 + 8).value == "H"
    assert client.get(f"/reports/register?course_id={math.id}&month=2026-10&fmt=pdf").content[:4] == b"%PDF"
    assert client.get(f"/reports/register?course_id={math.id}&month=bad").status_code == 400
    client.get("/logout")
    login(client, "t2", "teacher123")
    assert client.get(f"/reports/register?course_id={math.id}&month=2026-10").status_code == 404   # not his subject
