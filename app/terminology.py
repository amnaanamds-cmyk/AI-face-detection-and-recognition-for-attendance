"""Words shown in the UI for each kind of organization.

Internally the data model keeps the names Student / Course / ClassSession; customers see
the vocabulary of their own world (school, office, gym / events).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models import OrgKind


@dataclass(frozen=True)
class Terms:
    person: str
    people: str
    person_id: str
    group: str
    groups: str
    session: str
    sessions: str
    staff: str          # the role that runs attendance
    staffs: str
    level: str          # "Semester" etc. ("" = hidden)
    subgroup: str       # "Section" etc. ("" = hidden)
    unit: str           # "Department" etc.
    check_out: bool     # show check-out / worked hours
    kiosk_hint: str

    def lower(self, word: str) -> str:
        return getattr(self, word).lower()


PRESETS: dict[OrgKind, Terms] = {
    OrgKind.school: Terms("Student", "Students", "Student ID", "Course", "Courses", "Class session", "Class sessions",
                          "Teacher", "Teachers", "Semester", "Section", "Department", False,
                          "Put a camera at the classroom door or on the teacher's desk."),
    OrgKind.office: Terms("Employee", "Employees", "Employee ID", "Team", "Teams", "Shift", "Shifts",
                          "Manager", "Managers", "", "", "Department", True,
                          "Put a tablet or webcam at the entrance: employees check in and out by looking at it."),
    OrgKind.event: Terms("Member", "Members", "Member ID", "Group", "Groups", "Event", "Events",
                         "Organizer", "Organizers", "", "", "Category", True,
                         "Put a tablet at the entrance: members check in by looking at it."),
}


# Schools (classes 1-12) say subject / class / period; colleges and universities keep course / semester.
SCHOOL_WORDS = Terms("Student", "Students", "Roll No.", "Subject", "Subjects", "Period", "Periods",
                     "Teacher", "Teachers", "Class", "Section", "Wing", False,
                     "Put a camera at the classroom door or let teachers use their phones.")
INSTITUTIONS = {"school": "School (classes 1-12)", "college": "College or university"}


def terms_of(org) -> Terms:
    """Words for an organization (None = default school wording)."""
    if org is None:
        return terms(None)
    if org.kind == OrgKind.school and getattr(org, "institution", None) == "school":
        return SCHOOL_WORDS
    return terms(org.kind)


def terms(kind: OrgKind | str | None) -> Terms:
    if kind is None:
        return PRESETS[OrgKind.school]
    return PRESETS[OrgKind(kind)]
