"""Global FPL-wide data refresh — shared by every tenant, so this takes no user_id. Extracted
from `cli._cmd_ingest` (the bootstrap/fixtures/players/snapshots portion; the CLI's optional
single-team entry-name sanity check stays CLI-only, see cli.py)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from fpl_optimizer.ingestion.fpl_api import FPLClient, infer_current_season
from fpl_optimizer.ingestion.snapshots import save_raw
from fpl_optimizer.storage import db


def run_ingest(conn: sqlite3.Connection) -> dict:
    client = FPLClient()
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    bootstrap = client.bootstrap_static()
    save_raw("fpl_api", "bootstrap-static", bootstrap, when=now)
    season = infer_current_season(bootstrap)

    fixtures = client.fixtures()
    save_raw("fpl_api", "fixtures", fixtures, when=now)

    db.upsert_teams(conn, bootstrap["teams"])
    db.upsert_element_types(conn, bootstrap["element_types"])
    db.upsert_players(conn, bootstrap["elements"], updated_at=now.isoformat())
    db.insert_player_snapshots(conn, bootstrap["elements"], snapshot_date=today, fetched_at=now.isoformat())
    db.insert_fixtures(conn, fixtures, season=season)

    return {
        "season": season,
        "players": len(bootstrap["elements"]),
        "teams": len(bootstrap["teams"]),
        "fixtures": len(fixtures),
        "ingested_at": now.isoformat(),
    }
