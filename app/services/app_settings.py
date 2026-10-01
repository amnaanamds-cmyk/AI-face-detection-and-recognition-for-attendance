"""Administrator-configurable settings stored in the database."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings as env
from app.models import OrgSetting

# key -> (default, type, label)
DEFAULTS: dict[str, tuple[object, type, str]] = {
    "present_window_minutes": (10, int, "Minutes after start counted as Present"),
    "late_window_minutes": (20, int, "Minutes after start counted as Late (after this: Absent)"),
    "low_attendance_threshold": (75.0, float, "Low-attendance warning threshold (%)"),
    "term_weeks": (16, int, "Length of the term / semester in weeks (attendance forecast)"),
    "match_threshold": (env.match_threshold, float, "Face match threshold (cosine similarity)"),
    "match_margin": (env.match_margin, float, "Required margin over the 2nd-best student"),
    "votes_required": (env.votes_required, int, "Agreeing frames required before marking"),
    "checkout_after_minutes": (30, int, "Offices / gyms: seen again after this many minutes = check-out"),
    "face_retention_days": (365, int, "Delete face templates of people not seen for this many days (0 = never)"),
    "attendance_retention_days": (0, int, "Delete attendance records older than this many days (0 = keep)"),
    "liveness_enabled": (env.liveness_enabled, bool, "Require liveness (anti-spoofing) by default"),
    "liveness_mode": (env.liveness_mode, str, "Liveness method: auto | cnn | motion | cnn+motion"),
    "antispoof_threshold": (env.antispoof_threshold, float, "Anti-spoofing CNN: min. P(live) to accept"),
    "liveness_motion_threshold": (env.liveness_motion_threshold, float, "Liveness 3-D motion threshold"),
    # --- messages to parents / guardians (page: Admin > Parent messages)
    "parent_alerts": ("off", str, "Message parents when their child is: off | absent | absent+late"),
    "parent_channel": ("phone", str, "Send text messages with: phone (Android app SIM) | sms | whatsapp (Twilio) | manual"),
    "parent_email": (True, bool, "Also e-mail the guardian when an e-mail address is known"),
    "parent_template": ("Dear {parent}, {student} was {status} in {group} on {date}. - {org}", str,
                        "Message text ({parent} {student} {status} {group} {date} {time} {org})"),
    "country_code": ("92", str, "Country calling code for local numbers like 03001234567 (e.g. 92 = Pakistan)"),
    "gateway_token": ("", str, "Secret that pairs the FaceAttend Android app (SMS gateway) with this organization"),
}


# shown on their own page (Admin > Parent messages), not on System settings
MESSAGE_KEYS = ("parent_alerts", "parent_channel", "parent_email", "parent_template", "country_code", "gateway_token")

CHOICES = {"liveness_mode": ("auto", "cnn", "motion", "cnn+motion"),
           "parent_alerts": ("off", "absent", "absent+late"),
           "parent_channel": ("phone", "sms", "whatsapp", "manual")}


def _cast(value: str, typ: type):
    if typ is bool:
        return str(value).lower() in {"1", "true", "yes", "on"}
    return typ(value)


def _row(db: Session, org_id: int, key: str) -> OrgSetting | None:
    return db.scalar(select(OrgSetting).where(OrgSetting.org_id == org_id, OrgSetting.key == key))


def get_setting(db: Session, org_id: int, key: str):
    default, typ, _ = DEFAULTS[key]
    row = _row(db, org_id, key)
    return _cast(row.value, typ) if row else default


def all_settings(db: Session, org_id: int) -> dict[str, object]:
    rows = {r.key: r.value for r in db.scalars(select(OrgSetting).where(OrgSetting.org_id == org_id))}
    return {k: (_cast(rows[k], typ) if k in rows else default) for k, (default, typ, _) in DEFAULTS.items()}


def set_setting(db: Session, org_id: int, key: str, value) -> None:
    if key not in DEFAULTS:
        raise KeyError(key)
    _, typ, _ = DEFAULTS[key]
    value = _cast(value, typ)  # validate
    if key in CHOICES and value not in CHOICES[key]:
        raise ValueError(f"{key} must be one of {', '.join(CHOICES[key])}")
    row = _row(db, org_id, key)
    if row:
        row.value = str(value)
    else:
        db.add(OrgSetting(org_id=org_id, key=key, value=str(value)))
    db.commit()
