"""CLI entry point. `ingest` is the first subcommand (M1); recommend/backtest/evaluate land in later milestones (Architecture §4.7)."""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

import joblib
import pandas as pd

from fpl_optimizer import clock
from fpl_optimizer.evaluation import backtest
from fpl_optimizer.features import build as feature_build
from fpl_optimizer.ingestion import archive_loader, understat
from fpl_optimizer.ingestion.fpl_api import FPLClient
from fpl_optimizer.ingestion.snapshots import save_raw
from fpl_optimizer.models import training_data
from fpl_optimizer.models.baseline import NaivePredictor, PoissonPredictor
from fpl_optimizer.models.ensemble import EnsemblePredictor
from fpl_optimizer.optimize import constraints
from fpl_optimizer.storage import db
from fpl_optimizer.strategy import horizon

PREDICTORS = {"naive": NaivePredictor, "poisson": PoissonPredictor}

DEFAULT_DB_PATH = Path("data/db/fpl.sqlite")
DEFAULT_MODELS_DIR = Path("data/artifacts/models")


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
    the_clock = clock.FixedClock(args.as_of) if args.as_of else clock.SystemClock()

    predictor = PREDICTORS[args.model]()
    predictor.fit(pd.DataFrame())  # baselines are stateless; fit() is a documented no-op

    result = horizon.recommend_gameweek(conn, the_clock, args.season, args.gameweek, predictor)
    usable = result["predictions"].dropna(subset=["element_type", "team_id", "now_cost", "expected_points"])
    dropped = len(result["predictions"]) - len(usable)
    squad_df, picked = result["squad"], result["lineup"]

    names = {r["id"]: r["web_name"] for r in conn.execute("SELECT id, web_name FROM players").fetchall()}
    conn.close()

    def label(player_id: int) -> str:
        pts = usable.loc[usable.player_id == player_id, "expected_points"].iloc[0]
        tag = " (C)" if player_id == picked["captain"] else " (VC)" if player_id == picked["vice_captain"] else ""
        return f"{names.get(player_id, f'#{player_id}')}{tag} — {pts:.1f} xPts"

    print(f"Recommendation for {args.season} GW{args.gameweek} ({args.model} model, as of {result['as_of_date']})")
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


def _cmd_train(args: argparse.Namespace) -> None:
    conn = db.connect(args.db_path)

    print(f"Building training set: {args.season} GW{args.train_start}-{args.train_end}...")
    train_features, train_targets = training_data.build_training_set(
        conn, args.season, range(args.train_start, args.train_end + 1)
    )
    print(f"  {len(train_features)} (player, gameweek) rows")

    print(f"Building held-out set: {args.season} GW{args.test_start}-{args.test_end}...")
    test_features, test_targets = training_data.build_training_set(
        conn, args.season, range(args.test_start, args.test_end + 1)
    )
    print(f"  {len(test_features)} (player, gameweek) rows")
    conn.close()

    if train_features.empty or test_features.empty:
        raise SystemExit("Not enough data to train/evaluate — check the season and gameweek ranges.")

    ensemble = EnsemblePredictor()
    results = {}
    for name, predictor in [("naive", NaivePredictor()), ("poisson", PoissonPredictor()), ("gbm_ensemble", ensemble)]:
        predictor.fit(train_features, train_targets)
        predictions = predictor.predict(test_features)
        merged = predictions.merge(test_targets[["player_id", "gameweek", "total_points"]], on=["player_id", "gameweek"])
        results[name] = (merged["expected_points"] - merged["total_points"]).abs().mean()

    print(f"\nMean Absolute Error on GW{args.test_start}-{args.test_end} (lower is better):")
    for name, mae in sorted(results.items(), key=lambda kv: kv[1]):
        print(f"  {name}: {mae:.3f}")

    if args.save:
        DEFAULT_MODELS_DIR.mkdir(parents=True, exist_ok=True)
        model_path = DEFAULT_MODELS_DIR / f"gbm_ensemble_{args.season}.joblib"
        joblib.dump(ensemble, model_path)
        print(f"\nSaved trained ensemble -> {model_path}")


def _cmd_backtest(args: argparse.Namespace) -> None:
    conn = db.connect(args.db_path)

    if args.model == "gbm":
        # Train only on gameweeks strictly before the backtest window — training on data
        # that overlaps it would leak the backtest's own future into itself.
        print(f"Training GBM ensemble on {args.season} GW2-{args.start_gameweek - 1}...")
        train_features, train_targets = training_data.build_training_set(conn, args.season, range(2, args.start_gameweek))
        predictor = EnsemblePredictor()
        predictor.fit(train_features, train_targets)
    else:
        predictor = PREDICTORS[args.model]()
        predictor.fit(pd.DataFrame())  # stateless baseline; fit() is a documented no-op

    print(f"Backtesting {args.season} GW{args.start_gameweek}-{args.end_gameweek} ({args.model})...")
    results = backtest.backtest_season(conn, args.season, args.start_gameweek, args.end_gameweek, predictor)
    conn.close()

    if results.empty:
        raise SystemExit("No gameweeks in that range had recorded outcomes to backtest against.")

    print(f"\n{len(results)} gameweeks scored:")
    print(f"  Mean MAE: {results['mae'].mean():.3f}   Mean RMSE: {results['rmse'].mean():.3f}")
    print(f"  Recommended-squad points (total): {results['recommended_squad_points'].sum():.0f}")
    print(f"  Hindsight-optimal points (total): {results['hindsight_squad_points'].sum():.0f}")
    print(f"  Squad regret (total): {results['squad_regret'].sum():.0f}  (mean {results['squad_regret'].mean():.1f}/gameweek)")
    print("\nPer gameweek:")
    print(results.to_string(index=False))


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

    train_cmd = subparsers.add_parser(
        "train", help="Train the GBM ensemble on a gameweek range; compare MAE against baselines on a held-out range."
    )
    train_cmd.add_argument("--season", required=True, help="e.g. 2024-25")
    train_cmd.add_argument("--train-start", type=int, required=True)
    train_cmd.add_argument("--train-end", type=int, required=True)
    train_cmd.add_argument("--test-start", type=int, required=True)
    train_cmd.add_argument("--test-end", type=int, required=True)
    train_cmd.add_argument("--save", action="store_true", help="Persist the trained ensemble to data/artifacts/models/.")
    train_cmd.set_defaults(func=_cmd_train)

    backtest_cmd = subparsers.add_parser(
        "backtest", help="Replay a season gameweek-by-gameweek through the live pipeline (Architecture §4.8, P2)."
    )
    backtest_cmd.add_argument("--season", required=True, help="e.g. 2024-25")
    backtest_cmd.add_argument("--start-gameweek", type=int, required=True)
    backtest_cmd.add_argument("--end-gameweek", type=int, required=True)
    backtest_cmd.add_argument("--model", choices=[*sorted(PREDICTORS), "gbm"], default="poisson")
    backtest_cmd.set_defaults(func=_cmd_backtest)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
