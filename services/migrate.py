"""Apply the idempotent SQL files in database/init with a migration ledger."""
from __future__ import annotations

import os
from pathlib import Path

import psycopg


DATABASE_URL = os.environ["DATABASE_URL"]
MIGRATIONS = Path(__file__).resolve().parents[1] / "database" / "init"


def main() -> None:
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
    for path in sorted(MIGRATIONS.glob("*.sql")):
        with psycopg.connect(DATABASE_URL) as conn:
            applied = conn.execute("SELECT 1 FROM schema_migrations WHERE version=%s", (path.name,)).fetchone()
            if applied:
                continue
            sql = path.read_text(encoding="utf-8")
            conn.execute(sql, prepare=False)
            conn.execute("INSERT INTO schema_migrations(version) VALUES (%s)", (path.name,))
            conn.commit()
        print(f"Applied database migration {path.name}", flush=True)


if __name__ == "__main__":
    main()
