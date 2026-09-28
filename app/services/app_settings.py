"""Administrator-configurable settings stored in the database."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import settings as env
from app.models import AppSetting

# key -> (default, type, label)
DEFAULTS: dict[str, tuple[object, type, str]] = {
    "present_window_minutes": (10, int, "Minutes after start counted as Present"),
    "late_window_minutes": (20, int, "Minutes after start counted as Late (after this: Absent)"),
    "low_attendance_threshold": (75.0, float, "Low-attendance warning threshold (%)"),
    "match_threshold": (env.match_threshold, float, "Face match threshold (cosine similarity)"),
    "match_margin": (env.match_margin, float, "Required margin over the 2nd-best student"),
    "votes_required": (env.votes_required, int, "Agreeing frames required before marking"),
    "liveness_enabled": (env.liveness_enabled, bool, "Require liveness (anti-spoofing) by default"),
    "liveness_mode": (env.liveness_mode, str, "Liveness method: auto | cnn | motion | cnn+motion"),
    "antispoof_threshold": (env.antispoof_threshold, float, "Anti-spoofing CNN: min. P(live) to accept"),
    "liveness_motion_threshold": (env.liveness_motion_threshold, float, "Liveness 3-D motion threshold"),
}


CHOICES = {"liveness_mode": ("auto", "cnn", "motion", "cnn+motion")}


def _cast(value: str, typ: type):
    if typ is bool:
        return str(value).lower() in {"1", "true", "yes", "on"}
    return typ(value)


def get_setting(db: Session, key: str):
    default, typ, _ = DEFAULTS[key]
    row = db.get(AppSetting, key)
    return _cast(row.value, typ) if row else default


def all_settings(db: Session) -> dict[str, object]:
    return {k: get_setting(db, k) for k in DEFAULTS}


def set_setting(db: Session, key: str, value) -> None:
    if key not in DEFAULTS:
        raise KeyError(key)
    _, typ, _ = DEFAULTS[key]
    value = _cast(value, typ)  # validate
    if key in CHOICES and value not in CHOICES[key]:
        raise ValueError(f"{key} must be one of {', '.join(CHOICES[key])}")
    row = db.get(AppSetting, key)
    if row:
        row.value = str(value)
    else:
        db.add(AppSetting(key=key, value=str(value)))
    db.commit()
