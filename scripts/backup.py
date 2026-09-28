"""Back up the database (SQLite: consistent online copy; PostgreSQL: pg_dump).

    python scripts/backup.py [--out backups/]

Keep backups encrypted/off-site: they contain personal data. The SECRET_KEY (or
EMBEDDING_KEY) is needed to use the face templates in a restored backup.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "backups")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    url = settings.database_url
    if url.startswith("sqlite:///"):
        src = sqlite3.connect(url.removeprefix("sqlite:///"))
        target = args.out / f"attendance-{stamp}.db"
        dst = sqlite3.connect(target)
        src.backup(dst)
        dst.close()
        src.close()
    elif url.startswith("postgresql"):
        target = args.out / f"attendance-{stamp}.sql"
        pg_url = url.replace("postgresql+psycopg://", "postgresql://")
        with target.open("wb") as f:
            subprocess.run(["pg_dump", pg_url], stdout=f, check=True)
    else:
        print("Unsupported database URL for backup:", url)
        return 1
    print("Backup written to", target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
