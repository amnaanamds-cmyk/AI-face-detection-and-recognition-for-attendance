"""Multi-tenancy: organizations, slugs and the automatic upgrade of older single-school databases."""
from __future__ import annotations

import logging
import re

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.models import Organization, OrgKind

log = logging.getLogger(__name__)

# Columns added after the first release: (table, column, SQL type, default)
_NEW_COLUMNS = [
    ("users", "org_id", "INTEGER", None),
    ("users", "is_superadmin", "BOOLEAN", "0"),
    ("students", "org_id", "INTEGER", None),
    ("students", "consent_at", "DATETIME", None),
    ("students", "last_seen_at", "DATETIME", None),
    ("courses", "org_id", "INTEGER", None),
    ("courses", "schedule_start", "VARCHAR(5)", None),
    ("courses", "schedule_days", "VARCHAR(20)", "'0,1,2,3,4'"),
    ("courses", "schedule_minutes", "INTEGER", "480"),
    ("attendance", "checked_out_at", "DATETIME", None),
]


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:40] or "org"


def unique_slug(db: Session, name: str) -> str:
    base, n = slugify(name), 1
    slug = base
    while db.scalar(select(Organization.id).where(Organization.slug == slug)):
        n += 1
        slug = f"{base}-{n}"
    return slug


def create_org(db: Session, name: str, kind: OrgKind | str = OrgKind.school, **kw) -> Organization:
    org = Organization(name=name.strip(), slug=unique_slug(db, name), kind=OrgKind(kind), **kw)
    db.add(org)
    db.flush()
    return org


def upgrade_database(engine) -> None:
    """Bring an older database up to the current schema (adds columns, moves data into a default org).

    Safe to run on every start-up; it only changes what is missing.
    """
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, column, sqltype, default in _NEW_COLUMNS:
            if table not in tables:
                continue
            cols = {c["name"] for c in insp.get_columns(table)}
            if column not in cols:
                ddl = f"ALTER TABLE {table} ADD COLUMN {column} {sqltype}"
                if default is not None:
                    ddl += f" DEFAULT {default}"
                conn.execute(text(ddl))
                log.warning("Database upgraded: added %s.%s", table, column)

        orphans = sum(conn.execute(text(f"SELECT COUNT(*) FROM {t} WHERE org_id IS NULL")).scalar()
                      for t in ("users", "students", "courses") if t in tables)
        if orphans:
            org_id = conn.execute(text("SELECT id FROM organizations ORDER BY id LIMIT 1")).scalar()
            if org_id is None:
                conn.execute(text("INSERT INTO organizations (name, slug, kind, plan, plan_status, is_active, created_at) "
                                  "VALUES ('My organization', 'my-organization', 'school', 'selfhosted', 'active', 1, CURRENT_TIMESTAMP)"))
                org_id = conn.execute(text("SELECT id FROM organizations ORDER BY id LIMIT 1")).scalar()
            for t in ("users", "students", "courses"):
                conn.execute(text(f"UPDATE {t} SET org_id = :o WHERE org_id IS NULL"), {"o": org_id})
            # the original administrator operates the installation
            conn.execute(text("UPDATE users SET is_superadmin = 1 WHERE id = (SELECT MIN(id) FROM users WHERE role = 'admin')"))
            log.warning("Existing data moved into organization %s", org_id)

        if "app_settings" in tables:  # settings of the single-school version -> first organization
            org_id = conn.execute(text("SELECT id FROM organizations ORDER BY id LIMIT 1")).scalar()
            if org_id is not None:
                for key, value in conn.execute(text("SELECT key, value FROM app_settings")).all():
                    exists = conn.execute(text("SELECT 1 FROM org_settings WHERE org_id = :o AND key = :k"),
                                          {"o": org_id, "k": key}).scalar()
                    if not exists:
                        conn.execute(text("INSERT INTO org_settings (org_id, key, value) VALUES (:o, :k, :v)"),
                                     {"o": org_id, "k": key, "v": value})
            conn.execute(text("DROP TABLE app_settings"))
