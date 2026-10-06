"""Weekly timetable, one-tap Start of a period, holidays, and periods taken / missed."""
from datetime import date, datetime, timedelta

import pytest

from app import database
from app.models import ClassSession, Course, Holiday, SessionState, TimetableSlot
from app.services import overview as ov
from app.services import timetable as tt
from tests.conftest import login


def test_parse_day_and_time():
    assert [tt.parse_day(x) for x in ("Monday", "tue", "Thu", "Sat", "4", "jumma", "Itwar")] == [0, 1, 3, 5, 4, 4, 6]
    assert [tt.parse_time(x) for x in ("8:00", "08.40", "1:30 pm", "12:05 am")] == ["08:00", "08:40", "13:30", "00:05"]
    for bad in ("25:00", "noon"):
        with pytest.raises(ValueError):
            tt.parse_time(bad)
    with pytest.raises(ValueError):
        tt.parse_day("x")


def school(client):
    login(client)
    client.post("/users/new", data={"username": "t.math", "full_name": "Ms. Math", "password": "teacher123", "role": "teacher"})
    client.post("/users/new", data={"username": "t.eng", "full_name": "Mr. Eng", "password": "teacher123", "role": "teacher"})
    client.post("/courses/new", data={"code": "MATH", "name": "Mathematics", "semester": 9, "section": "A", "teacher_id": 2})
    client.post("/courses/new", data={"code": "ENG", "name": "English", "semester": 9, "section": "A", "teacher_id": 3})


def test_periods_taken_missed_and_holidays(client):
    school(client)
    monday = date(2026, 10, 5)
    csv = "Day,Start,Minutes,Subject code,Room\nMonday,08:00,40,MATH,R1\nMonday,08:40,40,ENG,R1\nTuesday,08:00,40,MATH,R1\n"
    client.post("/timetable/import", files={"file": ("t.csv", csv.encode())})
    db = database.SessionLocal()
    org_id = db.query(Course).first().org_id
    assert len(tt.slots(db, org_id)) == 3
    math = db.query(Course).filter_by(code="MATH").one()
    # Ms. Math took Monday 08:00 (started 5 min early), Mr. Eng did not take his 08:40 period
    db.add(ClassSession(course_id=math.id, date=monday, start_time=datetime(2026, 10, 5, 7, 55), state=SessionState.closed))
    db.commit()
    ps = tt.periods(db, org_id, monday, monday, when=datetime(2026, 10, 5, 9, 0))
    assert [(p.course.code, p.state) for p in ps] == [("MATH", "done"), ("ENG", "due")]
    ps = tt.periods(db, org_id, monday, monday + timedelta(days=1), when=datetime(2026, 10, 6, 7, 0))
    assert [p.state for p in ps] == ["done", "missed", "upcoming"]
    assert tt.taken_rate(ps) == (1, 2, 50.0)
    # Tuesday is a holiday: its period disappears
    db.add(Holiday(org_id=org_id, start=monday + timedelta(days=1), end=monday + timedelta(days=1), name="Strike"))
    db.commit()
    assert len(tt.periods(db, org_id, monday, monday + timedelta(days=1))) == 2
    d = ov.overview(db, org_id, monday, monday + timedelta(days=1), today=monday + timedelta(days=1))
    by = {x["user"].username: x for x in d["teachers"]}
    assert by["t.math"]["periods"] == (1, 1, 100.0) and by["t.eng"]["periods"] == (0, 1, 0.0)
    assert d["has_timetable"] and d["periods_taken"] == (1, 2, 50.0)
    db.close()


def test_one_tap_start_and_pages(client):
    school(client)
    today = date.today()
    start = (datetime.now() - timedelta(minutes=5)).strftime("%H:%M")
    r = client.post("/timetable/new", data={"course_id": 1, "days": [str(today.weekday())], "start": start, "minutes": 40},
                    follow_redirects=True)
    assert "1 period(s) added" in r.text and "MATH" in client.get("/timetable").text
    assert client.post("/timetable/new", data={"course_id": 1, "start": "08:00"}, follow_redirects=True).text.count("choose at least one day")
    with database.SessionLocal() as db:
        slot_id = db.query(TimetableSlot).one().id
    client.get("/logout")
    login(client, "t.eng", "teacher123")
    assert client.post(f"/timetable/{slot_id}/start").status_code == 404          # not his subject
    client.get("/logout")
    login(client, "t.math", "teacher123")
    page = client.get("/").text
    assert "My periods today" in page and f'/timetable/{slot_id}/start' in page
    r = client.post(f"/timetable/{slot_id}/start", follow_redirects=False)
    assert r.headers["location"].endswith("/live")
    again = client.post(f"/timetable/{slot_id}/start", follow_redirects=False).headers["location"]
    assert again == r.headers["location"]                                          # the same session, not a second one
    with database.SessionLocal() as db:
        s = db.query(ClassSession).one()
        assert (s.duration_minutes, s.state) == (40, SessionState.active)
    assert "Running" in client.get("/").text
    client.get("/logout")
    login(client)
    client.post("/calendar/add", data={"name": "Eid", "start": today.isoformat()})
    assert "Holiday" in client.get("/").text and "Eid" in client.get("/calendar").text
    assert "Eid" in client.get("/overview").text
