"""CLI entry point. `ingest` is the first subcommand (M1); recommend/backtest/evaluate land in later milestones (Architecture §4.7)."""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from fpl_optimizer.features import build as feature_build
from fpl_optimizer.ingestion import archive_loader, understat
from fpl_optimizer.ingestion.fpl_api import FPLClient
from fpl_optimizer.ingestion.snapshots import save_raw
from fpl_optimizer.models.baseline import NaivePredictor, PoissonPredictor
from fpl_optimizer.optimize import constraints, lineup, squad
from fpl_optimizer.storage import db

PREDICTORS = {"naive": NaivePredictor, "poisson": PoissonPredictor}

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


def _cmd_features(args: argparse.Namespace) -> None:
    conn = db.connect(args.db_path)
    as_of_date = args.as_of or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    df = feature_build.assemble_features(conn, as_of_date, args.season, args.gameweek)
    path = feature_build.materialize_features(df, as_of_date)
    conn.close()
    print(f"Built features for {args.season} GW{args.gameweek} as of {as_of_date}: {len(df)} players -> {path}")


def _cmd_recommend(args: argparse.Namespace) -> None:
    conn = db.connect(args.db_path)
    as_of_date = args.as_of or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    features_df = feature_build.assemble_features(conn, as_of_date, args.season, args.gameweek)

    predictor = PREDICTORS[args.model]()
    predictor.fit(features_df)
    predictions = predictor.predict(features_df)

    merged = predictions.merge(
        features_df[["player_id", "element_type", "team_id", "now_cost"]], on="player_id", how="left"
    )
    usable = merged.dropna(subset=["element_type", "team_id", "now_cost", "expected_points"])
    dropped = len(merged) - len(usable)

    squad_df = squad.build_squad(usable)
    picked = lineup.build_lineup(squad_df)

    names = {r["id"]: r["web_name"] for r in conn.execute("SELECT id, web_name FROM players").fetchall()}
    conn.close()

    def label(player_id: int) -> str:
        pts = usable.loc[usable.player_id == player_id, "expected_points"].iloc[0]
        tag = " (C)" if player_id == picked["captain"] else " (VC)" if player_id == picked["vice_captain"] else ""
        return f"{names.get(player_id, f'#{player_id}')}{tag} — {pts:.1f} xPts"

    print(f"Recommendation for {args.season} GW{args.gameweek} ({args.model} model, as of {as_of_date})")
    if dropped:
        print(f"  ({dropped} players excluded — missing price/position/prediction data)")
    print(f"  Squad cost: {squad_df['now_cost'].sum() / 10:.1f}m / {constraints.BUDGET / 10:.1f}m")
    print(f"  Squad expected points: {squad_df['expected_points'].sum():.1f}")
    print("\n  Starting XI:")
    for player_id in picked["starting_xi"]:
        print(f"    {label(player_id)}")
    print("  Bench:")
    for player_id in picked["bench"]:
        print(f"    {label(player_id)}")


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

    features_cmd = subparsers.add_parser("features", help="Build and materialize features for one (season, gameweek).")
    features_cmd.add_argument("--season", required=True, help="e.g. 2024-25")
    features_cmd.add_argument("--gameweek", type=int, required=True)
    features_cmd.add_argument("--as-of", default=None, help="ISO date; defaults to today.")
    features_cmd.set_defaults(func=_cmd_features)

    recommend_cmd = subparsers.add_parser("recommend", help="Predict + optimize: squad, XI, captain for one gameweek.")
    recommend_cmd.add_argument("--season", required=True, help="e.g. 2024-25")
    recommend_cmd.add_argument("--gameweek", type=int, required=True)
    recommend_cmd.add_argument("--as-of", default=None, help="ISO date; defaults to today.")
    recommend_cmd.add_argument("--model", choices=sorted(PREDICTORS), default="poisson")
    recommend_cmd.set_defaults(func=_cmd_recommend)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
