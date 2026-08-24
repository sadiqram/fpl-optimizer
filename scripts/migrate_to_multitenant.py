"""One-off forward migration (M8): add the `users`/`plan_runs` tables and thread `user_id`
through the owned-squad layer (owned_squad, team_state, squad_transfers, recommendations).

schema.sql's `CREATE TABLE IF NOT EXISTS` self-heals *new* tables on every connect (db.py's
own docstring), but it can't retroactively add a column to a table that already exists with
the old shape — that's what this script is for. Forward-only, no down-migration, matching
this project's existing schema.sql convention. Safe to re-run: each tenant table is migrated
independently and skipped once it already has `user_id`.

Usage: python scripts/migrate_to_multitenant.py --email you@example.com --password '...' [--db-path data/db/fpl.sqlite]
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fpl_optimizer.api import auth
from fpl_optimizer.storage import db

TENANT_TABLES = ["owned_squad", "team_state", "squad_transfers", "recommendations"]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (table,)
    ).fetchone() is not None


def _has_user_id(conn: sqlite3.Connection, table: str) -> bool:
    cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    return "user_id" in cols


def migrate(db_path: Path, email: str, password: str) -> None:
    # Raw connection, deliberately not db.connect(): running the full (new-shape) schema
    # via init_db before old-shape tenant tables are out of the way fails the moment it
    # hits a `CREATE INDEX ... ON recommendations (user_id, ...)` against a table that
    # still lacks that column.
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")  # re-enabled after the table swap below
    conn.row_factory = sqlite3.Row

    to_migrate = [t for t in TENANT_TABLES if _table_exists(conn, t) and not _has_user_id(conn, t)]
    if not to_migrate:
        print("Already migrated (or nothing to migrate) — nothing to do.")
        conn.close()
        return
    print(f"Tables to migrate: {to_migrate}")

    for table in to_migrate:
        conn.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
    conn.commit()

    db.init_db(conn)  # now safe: every *_old rename cleared the way for a fresh-shape table

    existing = db.get_user_by_email(conn, email)
    if existing:
        user_id = existing["id"]
        print(f"Using existing user {email!r} (id={user_id}) as the migration target.")
    else:
        user_id = db.create_user(
            conn, email=email, password_hash=auth.hash_password(password),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        print(f"Created user {email!r} (id={user_id}) to own all pre-existing squad/plan/recommendation data.")

    for table in to_migrate:
        old_rows = conn.execute(f"SELECT * FROM {table}_old").fetchall()
        if old_rows:
            columns = list(old_rows[0].keys())
            placeholders = ", ".join(f":{c}" for c in columns)
            col_list = ", ".join(columns)
            rows = [{**dict(r), "user_id": user_id} for r in old_rows]
            conn.executemany(
                f"INSERT INTO {table} (user_id, {col_list}) VALUES (:user_id, {placeholders})",
                rows,
            )
        conn.execute(f"DROP TABLE {table}_old")
        conn.commit()
        print(f"  {table}: migrated {len(old_rows)} rows -> user_id={user_id}")

    conn.execute("PRAGMA foreign_keys = ON")
    conn.close()
    print("Migration complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--db-path", type=Path, default=Path("data/db/fpl.sqlite"))
    args = parser.parse_args()
    migrate(args.db_path, args.email, args.password)


if __name__ == "__main__":
    main()
