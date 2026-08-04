"""CLI entry point. `ingest` is the first subcommand (M1); recommend/backtest/evaluate land in later milestones (Architecture §4.7)."""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from fpl_optimizer.ingestion import archive_loader, understat
from fpl_optimizer.ingestion.fpl_api import FPLClient
from fpl_optimizer.ingestion.snapshots import save_raw
from fpl_optimizer.storage import db

DEFAULT_DB_PATH = Path("data/db/fpl.sqlite")


def infer_current_season(bootstrap: dict) -> str:
    """FPL runs Aug-May; derive '2026-27' from GW1's deadline year rather than wall-clock
    time, since that's a property of the data, not of when ingest happens to run."""
    gw1_deadline = bootstrap["events"][0]["deadline_time"]
    start_year = int(gw1_deadline[:4])
    return f"{start_year}-{str(start_year + 1)[2:]}"


def _cmd_ingest(args: argparse.Namespace) -> None:
    load_dotenv()
    client = FPLClient()
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    bootstrap = client.bootstrap_static()
    save_raw("fpl_api", "bootstrap-static", bootstrap, when=now)
    season = infer_current_season(bootstrap)

    fixtures = client.fixtures()
    save_raw("fpl_api", "fixtures", fixtures, when=now)

    conn = db.connect(args.db_path)
    db.init_db(conn)
    db.upsert_teams(conn, bootstrap["teams"])
    db.upsert_element_types(conn, bootstrap["element_types"])
    db.upsert_players(conn, bootstrap["elements"], updated_at=now.isoformat())
    db.insert_player_snapshots(conn, bootstrap["elements"], snapshot_date=today, fetched_at=now.isoformat())
    db.insert_fixtures(conn, fixtures, season=season)

    print(
        f"Ingested {len(bootstrap['elements'])} players, "
        f"{len(bootstrap['teams'])} teams, {len(fixtures)} fixtures -> {args.db_path}"
    )

    team_id = os.environ.get("FPL_TEAM_ID", "").strip()
    if team_id:
        try:
            entry = client.entry(int(team_id))
            save_raw("fpl_api", f"entry-{team_id}", entry, when=now)
            print(f'Fetched entry for team {team_id}: "{entry.get("name")}".')
        except Exception as exc:
            # NFR2: degrade gracefully on missing/unavailable data rather than fail outright.
            print(f"Could not fetch entry {team_id}, continuing without it: {exc}")
    else:
        print("FPL_TEAM_ID not set in .env — skipping entry fetch.")

    conn.close()


def _cmd_bootstrap_season(args: argparse.Namespace) -> None:
    conn = db.connect(args.db_path)
    summary = archive_loader.bootstrap_season(conn, args.season)
    conn.close()
    print(f"Bootstrapped {args.season} from the vaastav archive:")
    for key, value in summary.items():
        print(f"  {key}: {value}")


def _cmd_understat(args: argparse.Namespace) -> None:
    conn = db.connect(args.db_path)
    summary = understat.ingest_season(conn, args.season, max_players=args.max_players)
    conn.close()
    print(f"Understat ingest for {args.season}:")
    for key, value in summary.items():
        print(f"  {key}: {value}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fpl-optimizer")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser("ingest", help="Fetch current FPL state; write raw snapshots and parse into SQLite.")
    ingest.set_defaults(func=_cmd_ingest)

    bootstrap_season = subparsers.add_parser(
        "bootstrap-season", help="Load a past season's FPL data from the vaastav archive (PRD §6a.4)."
    )
    bootstrap_season.add_argument("--season", required=True, help="e.g. 2024-25")
    bootstrap_season.set_defaults(func=_cmd_bootstrap_season)

    understat_cmd = subparsers.add_parser(
        "understat", help="Fetch live Understat data for a season and match it to FPL players."
    )
    understat_cmd.add_argument("--season", required=True, help="e.g. 2026-27")
    understat_cmd.add_argument("--max-players", type=int, default=None, help="Cap per-player match-log fetches.")
    understat_cmd.set_defaults(func=_cmd_understat)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
