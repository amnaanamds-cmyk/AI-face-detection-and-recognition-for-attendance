"""Attendance forecast & recovery planner: the numbers must be checkable by hand."""
from datetime import date, timedelta
from types import SimpleNamespace

from app.models import AttendanceStatus as S
from app.services.forecast import forecast_course

COURSE = SimpleNamespace(code="CS-401", name="AI")


def recs(statuses, start=date(2026, 9, 7), step=1):
    return [SimpleNamespace(status=s, date=start + timedelta(days=i * step), session=None, id=i)
            for i, s in enumerate(statuses)]


def test_recovery_numbers():
    # 12 classes: 4 present, then 4 absent/4 present alternating -> 8 of 12 attended (66.7 %), last 8: 4 of 8 (50 %)
    history = [S.present] * 4 + [S.absent, S.present] * 4
    f = forecast_course(COURSE, recs(history), remaining=20, threshold=75)
    assert (f.attended, f.counted) == (8, 12) and round(f.current, 1) == 66.7 and f.recent == 50.0
    assert f.need == 16 and f.can_miss == 4          # 75 % of 32 = 24 -> 16 more of the 20 left
    assert round(f.projected, 2) == 56.25             # (8 + 0.5 * 20) / 32
    assert f.level == "high" and "Must attend 16 of the remaining 20" in f.advice


def test_safe_student():
    f = forecast_course(COURSE, recs([S.present] * 20), remaining=4, threshold=75)
    assert f.need <= 0 and f.risk < 20 and f.advice.startswith("Safe")


def test_cannot_recover():
    f = forecast_course(COURSE, recs([S.absent] * 8 + [S.present] * 2), remaining=4, threshold=75)
    assert f.need is None and f.risk == 100 and "Cannot reach 75%" in f.advice


def test_excused_and_leave_are_neutral():
    f = forecast_course(COURSE, recs([S.present, S.excused, S.leave, S.present]), remaining=0, threshold=75)
    assert f.counted == 2 and f.current == 100.0


def test_patterns_explain_the_risk():
    # Mondays every 7 days: absent on 4 of 5 Mondays; other days present
    days = []
    start = date(2026, 9, 7)  # a Monday
    for week in range(5):
        for d in range(5):
            st = S.absent if d == 0 and week < 4 else (S.late if d in (1, 2) else S.present)
            days.append(SimpleNamespace(status=st, date=start + timedelta(days=7 * week + d), session=None, id=len(days)))
    f = forecast_course(COURSE, days, remaining=10, threshold=75)
    text = " ".join(f.patterns)
    assert "Often absent on Mondays (4 of 5)" in text
    assert "Frequently late" in text


def test_absence_streak():
    f = forecast_course(COURSE, recs([S.present] * 10 + [S.absent] * 3), remaining=10, threshold=75)
    assert any("last 3 classes in a row" in p for p in f.patterns)
