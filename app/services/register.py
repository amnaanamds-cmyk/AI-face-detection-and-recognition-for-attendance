"""Monthly attendance register: the classic school register sheet, one row per student, one column per day.

Marks: P present, L late, A absent, E excused / on leave, H holiday, blank = no class that day.
When a subject has two periods on one day, the day shows P only if the student attended both,
A if they missed both, and P/A when they attended one of them.
"""
from __future__ import annotations

import calendar
import io
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Attendance, AttendanceStatus as S, Course, Enrollment, Student
from app.services.analytics import rate
from app.services.timetable import holiday_days

MARK = {S.present: "P", S.late: "L", S.absent: "A", S.excused: "E", S.leave: "E"}


def _day_mark(statuses: list[S]) -> str:
    marks = {MARK[s] for s in statuses}
    if len(marks) == 1:
        return marks.pop()
    attended = marks & {"P", "L"}
    if "A" in marks and attended:
        return "P/A"
    if attended:
        return "L" if "L" in attended else "P"
    return "E" if "E" in marks else "A"


def build(db: Session, course: Course, year: int, month: int) -> dict:
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    days = [date(year, month, d) for d in range(1, last.day + 1)]
    off = holiday_days(db, course.org_id, first, last)
    students = db.scalars(select(Student).join(Enrollment).where(Enrollment.course_id == course.id)
                          .order_by(Student.roll_number, Student.student_code)).all()
    recs: dict[tuple[int, date], list[S]] = {}
    class_days: set[date] = set()
    for r in db.scalars(select(Attendance).where(Attendance.course_id == course.id, Attendance.date >= first,
                                                 Attendance.date <= last)):
        recs.setdefault((r.student_id, r.date), []).append(r.status)
        class_days.add(r.date)
    known = {s.id for s in students}
    extra = {sid for sid, _ in recs if sid not in known}       # records of students no longer enrolled
    if extra:
        students = list(students) + list(db.scalars(select(Student).where(Student.id.in_(extra))))
    rows = []
    for st in students:
        marks, all_status = {}, []
        for d in days:
            got = recs.get((st.id, d))
            if got:
                marks[d] = _day_mark(got)
                all_status += got
            elif d in off:
                marks[d] = "H"
            else:
                marks[d] = ""
        rows.append({"student": st, "marks": marks,
                     "P": sum(1 for s in all_status if s == S.present), "L": sum(1 for s in all_status if s == S.late),
                     "A": sum(1 for s in all_status if s == S.absent),
                     "E": sum(1 for s in all_status if s in (S.excused, S.leave)), "pct": rate(all_status)})
    present_per_day = {d: sum(1 for r in rows if r["marks"][d] in ("P", "L", "P/A")) for d in days}
    return {"course": course, "month": first, "days": days, "holidays": off, "class_days": sorted(class_days),
            "rows": rows, "present_per_day": present_per_day}


def to_xlsx(reg: dict, org_name: str = "") -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    c = reg["course"]
    wb = Workbook()
    ws = wb.active
    ws.title = reg["month"].strftime("%b %Y")
    ws.append([f"{org_name}  -  Attendance register  -  {c.code} {c.name} ({c.semester}-{c.section})  -  "
               f"{reg['month']:%B %Y}" + (f"  -  Teacher: {c.teacher.full_name}" if c.teacher else "")])
    ws["A1"].font = Font(bold=True, size=12)
    header = ["Roll", "ID", "Name"] + [str(d.day) for d in reg["days"]] + ["P", "L", "A", "E", "%"]
    ws.append(header)
    ws.append(["", "", ""] + [d.strftime("%a")[:2] for d in reg["days"]] + ["", "", "", "", ""])
    thin = Side(style="thin", color="BBBBBB")
    fills = {"A": PatternFill("solid", fgColor="F8D7DA"), "L": PatternFill("solid", fgColor="FFF3CD"),
             "H": PatternFill("solid", fgColor="D1ECF1"), "E": PatternFill("solid", fgColor="E2E3E5"),
             "P/A": PatternFill("solid", fgColor="FFF3CD")}
    for r in reg["rows"]:
        st = r["student"]
        ws.append([st.roll_number or "", st.student_code, st.name] + [r["marks"][d] for d in reg["days"]]
                  + [r["P"], r["L"], r["A"], r["E"], r["pct"]])
        for i, d in enumerate(reg["days"]):
            fill = fills.get(r["marks"][d])
            if fill:
                ws.cell(ws.max_row, 4 + i).fill = fill
    ws.append(["", "", "Present"] + [reg["present_per_day"][d] or "" for d in reg["days"]])
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        for cell in row:
            cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
            if cell.column > 3:
                cell.alignment = Alignment(horizontal="center")
    for cell in ws[2]:
        cell.font = Font(bold=True)
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width, ws.column_dimensions["C"].width = 6, 14, 24
    for i in range(len(reg["days"]) + 5):
        ws.column_dimensions[get_column_letter(4 + i)].width = 4.2
    ws.freeze_panes = "D4"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def to_pdf(reg: dict, org_name: str = "") -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    c = reg["course"]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=8 * mm, rightMargin=8 * mm,
                            topMargin=10 * mm, bottomMargin=10 * mm, title="Attendance register")
    st = getSampleStyleSheet()
    n = len(reg["days"])
    data = [["Roll", "Name"] + [str(d.day) for d in reg["days"]] + ["P", "L", "A", "%"],
            ["", ""] + [d.strftime("%a")[:2] for d in reg["days"]] + ["", "", "", ""]]
    for r in reg["rows"]:
        data.append([r["student"].roll_number or r["student"].student_code, r["student"].name[:22]]
                    + [r["marks"][d] for d in reg["days"]]
                    + [r["P"], r["L"], r["A"], f"{r['pct']:.0f}" if r["pct"] is not None else "-"])
    data.append(["", "Present"] + [reg["present_per_day"][d] or "" for d in reg["days"]] + ["", "", "", ""])
    widths = [16 * mm, 38 * mm] + [6.6 * mm] * n + [7 * mm, 7 * mm, 7 * mm, 8 * mm]
    t = Table(data, colWidths=widths, repeatRows=2)
    style = [("FONTSIZE", (0, 0), (-1, -1), 6.5), ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
             ("ALIGN", (2, 0), (-1, -1), "CENTER"), ("BACKGROUND", (0, 0), (-1, 1), colors.HexColor("#1F4E79")),
             ("TEXTCOLOR", (0, 0), (-1, 1), colors.white), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
    paint = {"A": "#F8D7DA", "L": "#FFF3CD", "P/A": "#FFF3CD", "H": "#D1ECF1", "E": "#E2E3E5"}
    for ri, row in enumerate(data[2:-1], start=2):
        for ci in range(2, 2 + n):
            if row[ci] in paint:
                style.append(("BACKGROUND", (ci, ri), (ci, ri), colors.HexColor(paint[row[ci]])))
    t.setStyle(TableStyle(style))
    title = (f"{org_name} - Attendance register - {c.code} {c.name} ({c.semester}-{c.section}) - {reg['month']:%B %Y}"
             + (f" - Teacher: {c.teacher.full_name}" if c.teacher else ""))
    doc.build([Paragraph(title, st["Heading4"]), Spacer(1, 4), t, Spacer(1, 6),
               Paragraph("P present, L late, A absent, E excused / leave, H holiday, P/A attended one of two periods.",
                         st["Normal"])])
    return buf.getvalue()
