"""Backup and restore for installations that run on the school's own computer (SQLite).

A backup is one .zip: a consistent copy of the database plus the keys that unlock it
(the secret key that encrypts face templates, the certificate signing key, the license).
Without those keys a copied database is useless, so they always travel together.

Every day the server also keeps an automatic backup in DATA_DIR/backups (the newest 14).
The hosted edition uses the database provider's backups instead.
"""
from __future__ import annotations

import io
import json
import logging
import sqlite3
import tempfile
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path

from app.config import DATA_DIR, settings

log = logging.getLogger(__name__)
BACKUP_DIR = DATA_DIR / "backups"
KEEP = 14
DB_NAME = "attendance.db"
REQUIRED_TABLES = {"organizations", "users", "students", "attendance"}
_thread_started = False


def _conn(path: str):
    """A SQLite connection that is really closed at the end of the with-block."""
    from contextlib import closing

    return closing(sqlite3.connect(path, timeout=30))


class BackupError(ValueError):
    pass


def db_path() -> Path | None:
    """The SQLite file in use (None for PostgreSQL)."""
    from app import database

    url = database.engine.url                                   # the database actually in use
    if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
        return None
    return Path(url.database)


def available() -> bool:
    return settings.edition != "saas" and db_path() is not None


def _key_files() -> dict[str, Path]:
    return {".secret_key": DATA_DIR / ".secret_key",
            "certificate-signing-key.pem": DATA_DIR / "certificate-signing-key.pem",
            "license.key": settings.license_file}


def make_backup() -> bytes:
    src = db_path()
    if src is None or not src.exists():
        raise BackupError("there is no local database to back up")
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / DB_NAME
        # SQLite's online backup: a consistent snapshot even while the server is writing
        with _conn(str(src)) as source, _conn(str(copy)) as target:
            source.backup(target)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(copy, DB_NAME)
            for name, path in _key_files().items():
                if path.exists():
                    z.write(path, f"keys/{name}")
            if not (DATA_DIR / ".secret_key").exists() and settings.secret_key:
                z.writestr("keys/.secret_key", settings.secret_key)      # the key came from the environment
            z.writestr("backup.json", json.dumps({"app": settings.app_name, "created": datetime.now().isoformat(timespec="seconds"),
                                                  "format": 1}))
    return buf.getvalue()


def auto_backup(now: datetime | None = None) -> Path | None:
    """Today's automatic backup (made once a day), keeping the newest KEEP files."""
    if not available():
        return None
    now = now or datetime.now()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"faceattend-{now:%Y-%m-%d}.zip"
    if not target.exists():
        target.write_bytes(make_backup())
        log.info("Automatic backup written: %s", target)
    for old in sorted(BACKUP_DIR.glob("faceattend-*.zip"))[:-KEEP]:
        old.unlink()
    return target


def list_backups() -> list[dict]:
    if not BACKUP_DIR.exists():
        return []
    return [{"name": p.name, "size": p.stat().st_size, "time": datetime.fromtimestamp(p.stat().st_mtime)}
            for p in sorted(BACKUP_DIR.glob("faceattend-*.zip"), reverse=True)]


def restore(data: bytes) -> dict:
    """Replace the database (and keys) with those in a backup. The current database is kept aside."""
    from app import database
    from app.services.biokey import _cache as biokey_cache
    from app.vision.matcher import gallery_cache

    dest = db_path()
    if dest is None or settings.edition == "saas":
        raise BackupError("restore is only available for installations with a local database")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        names = set(z.namelist())
    except zipfile.BadZipFile as exc:
        raise BackupError("this is not a FaceAttend backup (.zip)") from exc
    if DB_NAME not in names:
        raise BackupError("the backup does not contain a database")
    with tempfile.TemporaryDirectory() as tmp:
        candidate = Path(tmp) / DB_NAME
        candidate.write_bytes(z.read(DB_NAME))
        try:
            with _conn(str(candidate)) as conn:
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                ok = conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        except sqlite3.DatabaseError as exc:
            raise BackupError("the database in the backup is damaged") from exc
        if not REQUIRED_TABLES <= tables or not ok:
            raise BackupError("the database in the backup is damaged or not from FaceAttend")
        kept = None
        if dest.exists():                                           # keep the current data aside first
            kept = dest.with_name(f"{dest.name}.before-restore-{datetime.now():%Y%m%d-%H%M%S}")
            with _conn(str(dest)) as live, _conn(str(kept)) as aside:
                live.backup(aside)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # copy page by page into the live file (safe while the server has it open), then drop pooled connections
        # (other connections simply see the new content; the pool is not reset, so no connection is left open)
        with _conn(str(candidate)) as src, _conn(str(dest)) as live:
            src.backup(live)
    keys_changed = False
    for name, path in _key_files().items():
        member = f"keys/{name}"
        if member in names:
            content = z.read(member)
            if not path.exists() or path.read_bytes() != content:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                keys_changed = True
            if name == ".secret_key":
                settings.secret_key = content.decode().strip()      # face templates readable right away
    from app.tenancy import upgrade_database

    database.init_db()
    upgrade_database(database.engine)                               # a backup from an older version
    gallery_cache.invalidate()
    biokey_cache.clear()
    return {"kept": kept.name if kept else None, "keys_changed": keys_changed}


def start_daily_thread() -> None:
    """Background thread: first backup 10 minutes after start, then checked every hour."""
    global _thread_started
    import os

    if _thread_started or not available() or "PYTEST_CURRENT_TEST" in os.environ:
        return
    _thread_started = True

    def loop():
        time.sleep(600)
        while True:
            try:
                auto_backup()
            except Exception:                                       # never let backups crash the server
                log.exception("Automatic backup failed")
            time.sleep(3600)

    threading.Thread(target=loop, name="daily-backup", daemon=True).start()
