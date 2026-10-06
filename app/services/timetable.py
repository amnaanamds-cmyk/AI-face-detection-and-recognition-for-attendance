"""Weekly timetable and holiday calendar.

The timetable says which subject is taught when (one row per period). It is used to
* show every teacher "My periods today" with a one-tap Start;
* tell the principal which periods were taken, are running, or were missed;
* measure each teacher's attendance-taking (periods taken / periods scheduled).
Holidays remove the periods of those days: no attendance is expected.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ClassSession, Course, Holiday, SessionState, TimetableSlot, now

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MATCH_EARLY = timedelta(minutes=20)   # a session started up to 20 min before the period still counts


def parse_day(value: str) -> int:
    v = str(value).strip().lower()
    if v.isdigit() and 0 <= int(v) <= 6:
        return int(v)
    for i, name in enumerate(DAYS):
        if name.lower().startswith(v[:3]) and len(v) >= 2:
            return i
    urdu = {"pir": 0, "peer": 0, "mangal": 1, "budh": 2, "jumerat": 3, "jumeraat": 3, "juma": 4, "jumma": 4,
            "hafta": 5, "itwar": 6, "itwaar": 6}
    if v in urdu:
        return urdu[v]
    raise ValueError(f"unknown day '{value}'")


def parse_time(value: str) -> str:
    m = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*(am|pm)?\s*", str(value).lower())
    if not m:
        raise ValueError(f"time '{value}' is not like 08:40")
    h, mi = int(m.group(1)), int(m.group(2))
    if m.group(3) == "pm" and h < 12:
        h += 12
    if m.group(3) == "am" and h == 12:
        h = 0
    if not (0 <= h < 24 and 0 <= mi < 60):
        raise ValueError(f"time '{value}' is not valid")
    return f"{h:02d}:{mi:02d}"


# ------------------------------------------------------------------ holidays
def holiday_on(db: Session, org_id: int, day: date) -> Holiday | None:
    return db.scalar(select(Holiday).where(Holiday.org_id == org_id, Holiday.start <= day, Holiday.end >= day))


def holiday_days(db: Session, org_id: int, start: date, end: date) -> set[date]:
    out = set()
    for h in db.scalars(select(Holiday).where(Holiday.org_id == org_id, Holiday.start <= end, Holiday.end >= start)):
        d = max(h.start, start)
        while d <= min(h.end, end):
            out.add(d)
            d += timedelta(days=1)
    return out


# ------------------------------------------------------------------ periods
@dataclass
class Period:
    slot: TimetableSlot
    day: date
    session: ClassSession | None
    state: str            # done | running | due | upcoming | missed

    @property
    def course(self) -> Course:
        return self.slot.course

    @property
    def starts(self) -> datetime:
        return datetime.combine(self.day, datetime.strptime(self.slot.start, "%H:%M").time())

    @property
    def ends(self) -> datetime:
        return self.starts + timedelta(minutes=self.slot.minutes)


def slots(db: Session, org_id: int, course_ids: list[int] | None = None) -> list[TimetableSlot]:
    q = select(TimetableSlot).where(TimetableSlot.org_id == org_id)
    if course_ids is not None:
        q = q.where(TimetableSlot.course_id.in_(course_ids))
    return list(db.scalars(q.order_by(TimetableSlot.weekday, TimetableSlot.start)).all())


def periods(db: Session, org_id: int, start: date, end: date, course_ids: list[int] | None = None,
            when: datetime | None = None) -> list[Period]:
    """Every timetabled period between two dates (holidays left out), each matched to the session taken for it."""
    when = when or now()
    all_slots = slots(db, org_id, course_ids)
    if not all_slots:
        return []
    off = holiday_days(db, org_id, start, end)
    sessions: dict[tuple[int, date], list[ClassSession]] = {}
    for s in db.scalars(select(ClassSession).where(ClassSession.course_id.in_({x.course_id for x in all_slots}),
                                                   ClassSession.date >= start, ClassSession.date <= end)):
        sessions.setdefault((s.course_id, s.date), []).append(s)
    out, used = [], set()
    d = start
    while d <= end:
        if d not in off:
            for slot in (x for x in all_slots if x.weekday == d.weekday()):
                p = Period(slot, d, None, "")
                for s in sorted(sessions.get((slot.course_id, d), []), key=lambda s: s.start_time):
                    if s.id not in used and p.starts - MATCH_EARLY <= s.start_time <= p.ends:
                        p.session = s
                        used.add(s.id)
                        break
                if p.session is not None:
                    p.state = {SessionState.closed: "done", SessionState.active: "running"}.get(p.session.state, "upcoming")
                elif when < p.starts:
                    p.state = "upcoming"
                elif when <= p.ends:
                    p.state = "due"
                else:
                    p.state = "missed"
                out.append(p)
        d += timedelta(days=1)
    return out


def taken_rate(ps: list[Period]) -> tuple[int, int, float | None]:
    """(taken, due so far, %) - periods still to come are not counted."""
    due = [p for p in ps if p.state in ("done", "running", "missed")]
    taken = sum(1 for p in due if p.state in ("done", "running"))
    return taken, len(due), (round(100.0 * taken / len(due), 1) if due else None)


def start_period(db: Session, slot: TimetableSlot, user_id: int, day: date | None = None) -> ClassSession:
    """Create today's session for a timetabled period (the teacher's one-tap Start)."""
    from app.services import attendance as att
    from app.services.app_settings import get_setting

    day = day or now().date()
    start = datetime.combine(day, datetime.strptime(slot.start, "%H:%M").time())
    s = ClassSession(course_id=slot.course_id, date=day, start_time=start, duration_minutes=slot.minutes,
                     present_window_minutes=get_setting(db, slot.org_id, "present_window_minutes"),
                     late_window_minutes=get_setting(db, slot.org_id, "late_window_minutes"),
                     liveness_required=get_setting(db, slot.org_id, "liveness_enabled"), created_by=user_id)
    db.add(s)
    db.commit()
    att.start_session(db, s)
    return s


# ------------------------------------------------------------------ import
TEMPLATE_CSV = ("Day,Start,Minutes,Subject code,Room\n"
                "Monday,08:00,40,MATH-9A,Room 4\nMonday,08:40,40,ENG-9A,Room 4\nTuesday,08:00,40,PHY-9A,Lab 1\n")


def import_rows(db: Session, org_id: int, rows: list[dict[str, str]], replace: bool = False) -> tuple[int, list[str]]:
    from app.services.importer import _norm

    alias = {"day": "day", "weekday": "day", "start": "start", "time": "start", "starttime": "start", "from": "start",
             "minutes": "minutes", "duration": "minutes", "length": "minutes", "subjectcode": "code", "coursecode": "code",
             "code": "code", "subject": "code", "course": "code", "room": "room", "classroom": "room"}
    courses = {c.code.lower(): c for c in db.scalars(select(Course).where(Course.org_id == org_id))}
    errors, new = [], []
    for line, raw in enumerate(rows, start=2):
        d = {alias[_norm(k)]: (v or "").strip() for k, v in raw.items() if _norm(k) in alias}
        try:
            course = courses.get(d.get("code", "").lower())
            if course is None:
                raise ValueError(f"subject code '{d.get('code', '')}' not found (add the subject first)")
            minutes = int(float(d.get("minutes") or 40))
            if not 5 <= minutes <= 600:
                raise ValueError("minutes must be between 5 and 600")
            new.append(TimetableSlot(org_id=org_id, course_id=course.id, weekday=parse_day(d.get("day", "")),
                                     start=parse_time(d.get("start", "")), minutes=minutes, room=d.get("room", "")[:40]))
        except ValueError as exc:
            errors.append(f"row {line}: {exc}")
    if replace and new:
        for old in slots(db, org_id):
            db.delete(old)
    db.add_all(new)
    db.commit()
    return len(new), errors
