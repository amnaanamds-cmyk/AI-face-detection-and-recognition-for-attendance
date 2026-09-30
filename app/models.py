"""Relational data model.

Organizations ─< Users, Students, Courses, OrgSettings   (multi-tenant: every customer is an organization)
Students ─< FaceEmbeddings
Students >─< Courses          (through Enrollments)
Courses  ─< ClassSessions ─< Attendance >─ Students
Users (admin / teacher / student) teach Courses or are linked to a Student.
"""
from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def now() -> datetime:
    return datetime.now().replace(microsecond=0)


class Role(str, enum.Enum):
    admin = "admin"
    teacher = "teacher"
    student = "student"


class AttendanceStatus(str, enum.Enum):
    present = "present"
    late = "late"
    absent = "absent"
    excused = "excused"
    leave = "leave"


class SessionState(str, enum.Enum):
    scheduled = "scheduled"
    active = "active"
    closed = "closed"


class OrgKind(str, enum.Enum):
    school = "school"
    office = "office"
    event = "event"


class Organization(Base):
    """A customer (school, company, gym, ...). All their data is isolated from other organizations."""

    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    kind: Mapped[OrgKind] = mapped_column(Enum(OrgKind), default=OrgKind.school)
    plan: Mapped[str] = mapped_column(String(30), default="trial")
    plan_status: Mapped[str] = mapped_column(String(30), default="active")  # active | past_due | canceled
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(80))
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False)  # platform operator (you)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(120))
    role: Mapped[Role] = mapped_column(Enum(Role))
    student_id: Mapped[int | None] = mapped_column(ForeignKey("students.id", ondelete="SET NULL"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    student: Mapped["Student | None"] = relationship()
    courses: Mapped[list["Course"]] = relationship(back_populates="teacher")
    org: Mapped[Organization | None] = relationship()


class Student(Base):
    """A person whose attendance is tracked (student, employee or member - see app/terminology.py)."""

    __tablename__ = "students"
    __table_args__ = (UniqueConstraint("org_id", "student_code", name="uq_student_org_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    student_code: Mapped[str] = mapped_column(String(40), index=True)  # e.g. BSCS-2023-001 (unique per organization)
    roll_number: Mapped[str | None] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(120))
    department: Mapped[str] = mapped_column(String(120), default="Computer Science")
    semester: Mapped[int] = mapped_column(Integer, default=1)
    section: Mapped[str] = mapped_column(String(10), default="A")
    email: Mapped[str | None] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(40))
    consent_given: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    registration_date: Mapped[datetime] = mapped_column(DateTime, default=now)

    org: Mapped[Organization | None] = relationship()
    embeddings: Mapped[list["FaceEmbedding"]] = relationship(back_populates="student", cascade="all, delete-orphan")
    enrollments: Mapped[list["Enrollment"]] = relationship(back_populates="student", cascade="all, delete-orphan")
    attendance: Mapped[list["Attendance"]] = relationship(back_populates="student", cascade="all, delete-orphan")

    @property
    def face_registered(self) -> bool:
        return len(self.embeddings) > 0


class FaceEmbedding(Base):
    """A face template. The vector is stored *encrypted* (Fernet) - never the raw photo."""

    __tablename__ = "face_embeddings"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    embedding: Mapped[bytes] = mapped_column(LargeBinary)
    model_name: Mapped[str] = mapped_column(String(60))
    quality: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    student: Mapped[Student] = relationship(back_populates="embeddings")


class Course(Base):
    """A group whose attendance is taken: course, team/shift or class/event series (see app/terminology.py)."""

    __tablename__ = "courses"
    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_course_org_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(20), index=True)  # unique per organization
    name: Mapped[str] = mapped_column(String(120))
    department: Mapped[str] = mapped_column(String(120), default="Computer Science")
    semester: Mapped[int] = mapped_column(Integer, default=1)
    section: Mapped[str] = mapped_column(String(10), default="A")
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # Optional fixed schedule (offices, shifts, gyms): the kiosk opens today's session automatically.
    schedule_start: Mapped[str | None] = mapped_column(String(5))          # "09:00"
    schedule_days: Mapped[str] = mapped_column(String(20), default="0,1,2,3,4")  # Mon=0 ... Sun=6
    schedule_minutes: Mapped[int] = mapped_column(Integer, default=480)

    teacher: Mapped[User | None] = relationship(back_populates="courses")
    org: Mapped[Organization | None] = relationship()
    enrollments: Mapped[list["Enrollment"]] = relationship(back_populates="course", cascade="all, delete-orphan")
    sessions: Mapped[list["ClassSession"]] = relationship(back_populates="course", cascade="all, delete-orphan")


class Enrollment(Base):
    __tablename__ = "enrollments"
    __table_args__ = (UniqueConstraint("student_id", "course_id", name="uq_enrollment"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))

    student: Mapped[Student] = relationship(back_populates="enrollments")
    course: Mapped[Course] = relationship(back_populates="enrollments")


class ClassSession(Base):
    """One lecture / lab for which attendance is taken."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    start_time: Mapped[datetime] = mapped_column(DateTime)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    present_window_minutes: Mapped[int] = mapped_column(Integer, default=10)
    late_window_minutes: Mapped[int] = mapped_column(Integer, default=20)
    liveness_required: Mapped[bool] = mapped_column(Boolean, default=True)
    state: Mapped[SessionState] = mapped_column(Enum(SessionState), default=SessionState.scheduled)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    course: Mapped[Course] = relationship(back_populates="sessions")
    attendance: Mapped[list["Attendance"]] = relationship(back_populates="session", cascade="all, delete-orphan")

    @property
    def end_time(self) -> datetime:
        from datetime import timedelta

        return self.start_time + timedelta(minutes=self.duration_minutes)


class Attendance(Base):
    """One attendance record. (student, session) is UNIQUE -> duplicates are impossible."""

    __tablename__ = "attendance"
    __table_args__ = (UniqueConstraint("student_id", "session_id", name="uq_attendance_student_session"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    marked_at: Mapped[datetime | None] = mapped_column(DateTime)       # check-in time
    checked_out_at: Mapped[datetime | None] = mapped_column(DateTime)  # check-out time (offices, events)
    status: Mapped[AttendanceStatus] = mapped_column(Enum(AttendanceStatus))
    confidence: Mapped[float | None] = mapped_column(Float)
    liveness_score: Mapped[float | None] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(20), default="face")  # face | manual | auto-absent
    note: Mapped[str | None] = mapped_column(String(255))

    student: Mapped[Student] = relationship(back_populates="attendance")
    session: Mapped[ClassSession] = relationship(back_populates="attendance")
    course: Mapped[Course] = relationship()


class RecognitionEvent(Base):
    """Audit trail of what the AI pipeline decided (useful for evaluation and security)."""

    __tablename__ = "recognition_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[int | None] = mapped_column(ForeignKey("students.id", ondelete="SET NULL"))
    event: Mapped[str] = mapped_column(String(30))  # marked | duplicate | unknown | spoof
    similarity: Mapped[float | None] = mapped_column(Float)
    liveness_score: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    student: Mapped[Student | None] = relationship()


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int | None] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    course_id: Mapped[int | None] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    level: Mapped[str] = mapped_column(String(20), default="warning")
    message: Mapped[str] = mapped_column(Text)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    student: Mapped[Student | None] = relationship()
    course: Mapped[Course | None] = relationship()


class OrgSetting(Base):
    """Administrator-configurable key/value settings (attendance rules, thresholds), per organization."""

    __tablename__ = "org_settings"
    __table_args__ = (UniqueConstraint("org_id", "key", name="uq_org_setting"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(60))
    value: Mapped[str] = mapped_column(Text)
