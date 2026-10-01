"""Application configuration.

All settings can be overridden with environment variables (or a ``.env`` file in
the project root), so the same code runs on a laptop, in a lab or in tests.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
# Writable folder for the database, keys, licence and certificates. The desktop app (.exe)
# sets DATA_DIR to %LOCALAPPDATA%\FaceAttend because Program Files is read-only.
DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR / "data")))


def _load_dotenv(path: Path) -> None:
    """Minimal ``.env`` loader (KEY=VALUE per line) so no extra dependency is needed."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(DATA_DIR / ".env")
_load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _secret_key() -> str:
    """Use SECRET_KEY if set, otherwise persist a random one in data/ so sessions and
    encrypted embeddings survive restarts."""
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    key_file = DATA_DIR / ".secret_key"
    key_file.parent.mkdir(parents=True, exist_ok=True)
    if not key_file.exists():
        key_file.write_text(secrets.token_urlsafe(48))
        try:
            key_file.chmod(0o600)
        except OSError:
            pass
    return key_file.read_text().strip()


@dataclass
class Settings:
    # product / brand name shown everywhere (set APP_NAME to your own, trademark-checked name)
    app_name: str = field(default_factory=lambda: _env("APP_NAME", "FaceAttend"))
    support_email: str = field(default_factory=lambda: _env("SUPPORT_EMAIL", ""))
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", f"sqlite:///{DATA_DIR / 'attendance.db'}"))
    secret_key: str = field(default_factory=_secret_key)
    # Optional separate key for encrypting face embeddings (defaults to SECRET_KEY).
    embedding_key: str = field(default_factory=lambda: _env("EMBEDDING_KEY", ""))

    # "selfhosted" = customer runs it on their own server (license key); "saas" = you host it for many customers
    edition: str = field(default_factory=lambda: _env("EDITION", "selfhosted"))
    public_signup: bool = field(default_factory=lambda: _env_bool("PUBLIC_SIGNUP", _env("EDITION", "selfhosted") == "saas"))
    # without a license key a self-hosted installation is limited to this many people
    unlicensed_max_people: int = field(default_factory=lambda: _env_int("UNLICENSED_MAX_PEOPLE", 25))
    license_file: Path = field(default_factory=lambda: Path(_env("LICENSE_FILE", str(DATA_DIR / "license.key"))))
    default_org_name: str = field(default_factory=lambda: _env("DEFAULT_ORG_NAME", "My organization"))

    # Initial administrator created on first start-up.
    admin_username: str = field(default_factory=lambda: _env("ADMIN_USERNAME", "admin"))
    admin_password: str = field(default_factory=lambda: _env("ADMIN_PASSWORD", "admin123"))

    # --- AI / vision -------------------------------------------------------
    # "opencv" = YuNet detector + SFace recogniser (real models, see scripts/download_models.py)
    # "fake"   = deterministic stand-in used by the automated tests (no models required)
    vision_backend: str = field(default_factory=lambda: _env("VISION_BACKEND", "opencv"))
    models_dir: Path = field(default_factory=lambda: Path(_env("MODELS_DIR", str(BASE_DIR / "models"))))
    detection_score_threshold: float = field(default_factory=lambda: _env_float("DETECTION_SCORE", 0.85))
    min_face_size: int = field(default_factory=lambda: _env_int("MIN_FACE_SIZE", 40))
    # Cosine-similarity threshold for SFace. The model authors suggest 0.363 for 1:1
    # verification; for 1:N identification of a whole class a stricter value is needed.
    # 0.45 gave 100 % identification and 0 % unknown-accepted on the real-photo benchmark
    # (docs/EVALUATION.md). Re-tune with scripts/evaluate.py on your own students.
    match_threshold: float = field(default_factory=lambda: _env_float("MATCH_THRESHOLD", 0.45))
    # Best match must beat the best *other* student by this margin, otherwise "unknown".
    match_margin: float = field(default_factory=lambda: _env_float("MATCH_MARGIN", 0.05))
    # Number of agreeing recognitions of the same face track before attendance is marked.
    votes_required: int = field(default_factory=lambda: _env_int("VOTES_REQUIRED", 3))

    # --- Liveness ----------------------------------------------------------
    liveness_enabled: bool = field(default_factory=lambda: _env_bool("LIVENESS_ENABLED", True))
    # auto = anti-spoofing CNN if its model is installed, otherwise head-motion test;
    # or explicitly: cnn | motion | cnn+motion (strictest)
    liveness_mode: str = field(default_factory=lambda: _env("LIVENESS_MODE", "auto"))
    antispoof_threshold: float = field(default_factory=lambda: _env_float("ANTISPOOF_THRESHOLD", 0.7))
    liveness_min_frames: int = field(default_factory=lambda: _env_int("LIVENESS_MIN_FRAMES", 6))
    # Required variation of the affine-invariant nose coordinates (see app/vision/liveness.py)
    liveness_motion_threshold: float = field(default_factory=lambda: _env_float("LIVENESS_MOTION_THRESHOLD", 0.12))
    liveness_timeout_seconds: float = field(default_factory=lambda: _env_float("LIVENESS_TIMEOUT", 12.0))
    liveness_min_sharpness: float = field(default_factory=lambda: _env_float("LIVENESS_MIN_SHARPNESS", 15.0))

    # --- Enrollment --------------------------------------------------------
    min_enrollment_images: int = field(default_factory=lambda: _env_int("MIN_ENROLLMENT_IMAGES", 3))

    # --- Billing (SaaS edition, Stripe) -----------------------------------
    public_base_url: str = field(default_factory=lambda: _env("PUBLIC_BASE_URL", "http://127.0.0.1:8000"))
    stripe_secret_key: str = field(default_factory=lambda: _env("STRIPE_SECRET_KEY", ""))
    stripe_webhook_secret: str = field(default_factory=lambda: _env("STRIPE_WEBHOOK_SECRET", ""))

    # --- Legal pages (shown in /legal/*; have them reviewed by a lawyer) ---
    legal_company: str = field(default_factory=lambda: _env("LEGAL_COMPANY_NAME", "[Your company name]"))
    legal_email: str = field(default_factory=lambda: _env("LEGAL_CONTACT_EMAIL", "[privacy@your-domain.com]"))
    legal_address: str = field(default_factory=lambda: _env("LEGAL_ADDRESS", "[Your registered address]"))
    legal_country: str = field(default_factory=lambda: _env("LEGAL_COUNTRY", "[Country of registration]"))

    # --- Text messages to parents via Twilio (optional; the Android app can send SMS for free) ---
    twilio_account_sid: str = field(default_factory=lambda: _env("TWILIO_ACCOUNT_SID", ""))
    twilio_auth_token: str = field(default_factory=lambda: _env("TWILIO_AUTH_TOKEN", ""))
    twilio_sms_from: str = field(default_factory=lambda: _env("TWILIO_SMS_FROM", ""))            # e.g. +15017122661
    twilio_whatsapp_from: str = field(default_factory=lambda: _env("TWILIO_WHATSAPP_FROM", ""))  # e.g. +14155238886

    # --- Notifications (optional e-mail) ------------------------------------
    smtp_host: str = field(default_factory=lambda: _env("SMTP_HOST", ""))
    smtp_port: int = field(default_factory=lambda: _env_int("SMTP_PORT", 587))
    smtp_user: str = field(default_factory=lambda: _env("SMTP_USER", ""))
    smtp_password: str = field(default_factory=lambda: _env("SMTP_PASSWORD", ""))
    smtp_sender: str = field(default_factory=lambda: _env("SMTP_SENDER", ""))


settings = Settings()
