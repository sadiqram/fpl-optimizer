"""CLI entry point (Architecture §4.7)."""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

import pandas as pd

from fpl_optimizer import clock
from fpl_optimizer.evaluation import accuracy_log
from fpl_optimizer.features import build as feature_build
from fpl_optimizer.ingestion import archive_loader, understat
from fpl_optimizer.ingestion.fpl_api import FPLClient
from fpl_optimizer.ingestion.snapshots import save_raw
from fpl_optimizer.optimize import constraints
from fpl_optimizer.services import (
    backtest_service, evaluate_service, ingest_service, plan_service,
    predictor_service, recommend_service, squad_service, train_service,
)
from fpl_optimizer.storage import db
from fpl_optimizer.strategy import risk

PREDICTORS = predictor_service.PREDICTORS
MODEL_CHOICES = predictor_service.MODEL_CHOICES
CHIP_DISPLAY_NAMES = {"wildcard": "Wildcard", "freehit": "Free Hit", "bboost": "Bench Boost", "3xc": "Triple Captain"}

DEFAULT_DB_PATH = Path("data/db/fpl.sqlite")
DEFAULT_MODELS_DIR = predictor_service.DEFAULT_MODELS_DIR
ACCURACY_LOG_PATH = accuracy_log.ACCURACY_LOG_PATH


def _resolve_local_admin_user(conn):
    """The web app resolves user_id from a JWT (api/deps.py); the CLI has no login step, so
    it resolves the same way `squad`/`recommend`/`plan` all need one: a single local admin
    account, seeded once via `scripts/migrate_to_multitenant.py` and pointed at by
    FPL_ADMIN_EMAIL in .env. `SystemExit` with setup instructions rather than a stack trace,
    since this is a one-time local setup step, not a runtime failure (NFR2)."""
    email = os.environ.get("FPL_ADMIN_EMAIL", "").strip()
    if not email:
        raise SystemExit("FPL_ADMIN_EMAIL not set in .env — required to resolve which account this CLI run acts as.")
    user = db.get_user_by_email(conn, email)
    if user is None:
        raise SystemExit(
            f"No account found for FPL_ADMIN_EMAIL={email!r}. Run `python scripts/migrate_to_multitenant.py "
            "--email ... --password ...` once to create it."
        )
    return user


def _cmd_ingest(args: argparse.Namespace) -> None:
    load_dotenv()
    conn = db.connect(args.db_path)  # connect() already ensures the schema is current
    summary = ingest_service.run_ingest(conn)

    print(
        f"Ingested {summary['players']} players, "
        f"{summary['teams']} teams, {summary['fixtures']} fixtures -> {args.db_path}"
    )

    # CLI-only convenience, not part of the shared service: a quick sanity check that the
    # locally-configured team is reachable. The multi-tenant web app resolves this per
    # account instead (see api/routers/squad.py), not from a single env var.
    team_id = os.environ.get("FPL_TEAM_ID", "").strip()
    if team_id:
        try:
            client = FPLClient()
            now = datetime.now(timezone.utc)
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


def _cmd_squad(args: argparse.Namespace) -> None:
    """Owned-squad state ingestion (M6 FR1; multi-tenant since M8): current squad, purchase
    prices, free transfers, chip status — the state `plan` needs. The CLI's single admin
    account (seeded by `scripts/migrate_to_multitenant.py`) is resolved from FPL_TEAM_ID/
    a --user-id-less local flow; the web app resolves this per authenticated account instead
    (api/routers/squad.py)."""
    load_dotenv()
    team_id = os.environ.get("FPL_TEAM_ID", "").strip()
    if not team_id:
        raise SystemExit("FPL_TEAM_ID not set in .env — required for `squad`.")
    team_id = int(team_id)

    conn = db.connect(args.db_path)
    try:
        user = _resolve_local_admin_user(conn)
    except SystemExit:
        conn.close()
        raise

    try:
        summary = squad_service.sync_squad(conn, user_id=user["id"], team_id=team_id, season=args.season)
    except ValueError as exc:
        conn.close()
        raise SystemExit(str(exc)) from exc
    conn.close()

    print(f"Squad state for team {team_id}, {args.season}, as of GW{summary['gameweek']}:")
    print(f"  {summary['player_count']} players, bank {(summary['bank'] or 0) / 10:.1f}m")
    print(f"  Free transfers available for GW{summary['next_gameweek']}: {summary['free_transfers']}")
    print(f"  Chips available: {', '.join(summary['chips_available']) or 'none'}")


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


def _load_predictor(args: argparse.Namespace):
    try:
        return predictor_service.load_predictor(args.model, args.season)
    except FileNotFoundError as exc:
        raise SystemExit(f"{exc} (`fpl-optimizer train --season {args.season} ... --save`)") from exc


def _cmd_recommend(args: argparse.Namespace) -> None:
    load_dotenv()
    conn = db.connect(args.db_path)
    try:
        user = _resolve_local_admin_user(conn)
        the_clock = clock.FixedClock(args.as_of) if args.as_of else clock.SystemClock()
        predictor = _load_predictor(args)
        payload = recommend_service.run_recommend(
            conn, the_clock, user_id=user["id"], season=args.season, gameweek=args.gameweek,
            model=args.model, predictor=predictor,
        )
    finally:
        conn.close()

    def label(player: dict) -> str:
        tag = (
            " (C)" if player["player_id"] == payload["captain"]["player_id"]
            else " (VC)" if player["player_id"] == payload["vice_captain"]["player_id"]
            else ""
        )
        return f"{player['name']}{tag} — {player['expected_points']:.1f} xPts"

    print(f"Recommendation for {args.season} GW{args.gameweek} ({args.model} model, as of {payload['as_of_date']})")
    if payload["dropped_players"]:
        print(f"  ({payload['dropped_players']} players excluded — missing price/position/prediction data)")
    print(f"  Squad cost: {payload['squad_cost'] / 10:.1f}m / {constraints.BUDGET / 10:.1f}m")
    print(f"  Squad expected points: {payload['squad_expected_points']:.1f}")
    print("\n  Starting XI:")
    for player in payload["starting_xi"]:
        print(f"    {label(player)}")
    print("  Bench:")
    for player in payload["bench"]:
        print(f"    {label(player)}")
    print(f"\n  Logged {payload['predictions_logged']} predictions and recommendation {payload['run_id']} for later `evaluate`.")


def _cmd_plan(args: argparse.Namespace) -> None:
    """Rolling-horizon transfer + lineup planning against a real owned squad (M6, FR3/4/8).
    Distinct from `recommend`, which picks a fresh 15 from scratch (still useful for
    wildcards and backtesting) — `plan` requires `squad` to have been run first, same
    pattern `evaluate` already uses for `results`."""
    load_dotenv()
    conn = db.connect(args.db_path)
    try:
        user = _resolve_local_admin_user(conn)
        the_clock = clock.FixedClock(args.as_of) if args.as_of else clock.SystemClock()
        preset = risk.resolve_preset(args.preset or risk.default_preset_name())
        if args.risk is not None:
            preset = {**preset, "risk": args.risk}
        predictor = _load_predictor(args)
        try:
            payload = plan_service.run_plan(
                conn, the_clock, user_id=user["id"], season=args.season, gameweek=args.gameweek,
                model=args.model, predictor=predictor, preset=preset,
            )
        except plan_service.PlanNotReady as exc:
            raise SystemExit(f"{exc} (run `fpl-optimizer squad` first.)") from exc
    finally:
        conn.close()

    def label(player: dict) -> str:
        pts_str = f"{player['expected_points']:.1f} xPts" if player["expected_points"] is not None else "? xPts"
        tag = (
            " (C)" if player["player_id"] == payload["captain"]["player_id"]
            else " (VC)" if player["player_id"] == payload["vice_captain"]["player_id"]
            else ""
        )
        return f"{player['name']}{tag} — {pts_str}"

    preset_name = args.preset or risk.default_preset_name()
    print(f"Plan for {args.season} GW{args.gameweek} ({args.model} model, {preset_name} preset, as of {payload['as_of_date']})")
    if payload["transfers_in"]:
        print("\n  Transfers:")
        for out_name, in_name in zip(payload["transfers_out"], payload["transfers_in"]):
            print(f"    OUT {out_name}  ->  IN {in_name}")
        print(f"  Hits taken: {payload['hits_taken']} (-{payload['hit_cost']} pts)")
        print(f"  Net expected points gain (horizon, net of hits): {payload['expected_points_gain']:.1f}")
    else:
        print("\n  Hold — no transfer clears the hit threshold this week.")
    print("\n  Starting XI:")
    for player in payload["starting_xi"]:
        print(f"    {label(player)}")
    print("  Bench:")
    for player in payload["bench"]:
        print(f"    {label(player)}")

    if payload["chip_scenarios"]:
        print("\n  Chip opportunities:")
        for chip_name, scenario in payload["chip_scenarios"].items():
            print(f"    {CHIP_DISPLAY_NAMES.get(chip_name, chip_name)}: {scenario['delta']:+.1f} pts — {scenario['reasoning']}")


def _cmd_train(args: argparse.Namespace) -> None:
    conn = db.connect(args.db_path)
    print(f"Building training set: {args.season} GW{args.train_start}-{args.train_end}...")
    print(f"Building held-out set: {args.season} GW{args.test_start}-{args.test_end}...")
    try:
        result = train_service.run_train(
            conn, args.season, args.train_start, args.train_end, args.test_start, args.test_end, args.save,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        conn.close()

    print(f"  {result['train_rows']} train rows, {result['test_rows']} held-out rows")
    print(f"\nMean Absolute Error on GW{args.test_start}-{args.test_end} (lower is better):")
    for name, mae in sorted(result["mae_by_model"].items(), key=lambda kv: kv[1]):
        print(f"  {name}: {mae:.3f}")
    if result["saved_path"]:
        print(f"\nSaved trained ensemble -> {result['saved_path']}")


def _cmd_backtest(args: argparse.Namespace) -> None:
    conn = db.connect(args.db_path)
    print(f"Backtesting {args.season} GW{args.start_gameweek}-{args.end_gameweek} ({args.model})...")
    results = backtest_service.run_backtest(conn, args.season, args.start_gameweek, args.end_gameweek, args.model)
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


def _cmd_results(args: argparse.Namespace) -> None:
    """Ingestion step (fetch + write raw snapshot + parse, never transform beyond that —
    Architecture §4.1): pulls actual per-player outcomes for one live gameweek and stores
    them in player_gw_stats, same as archive/understat ingestion does for their sources.
    `evaluate` reads from the DB, not the network, so this has to run first for a live
    season (archive-bootstrapped seasons already have player_gw_stats populated)."""
    conn = db.connect(args.db_path)
    client = FPLClient()
    now = datetime.now(timezone.utc)

    live = client.event_live(args.gameweek)
    save_raw("fpl_api", f"event-{args.gameweek}-live", live, when=now)
    n = db.insert_event_live_stats(conn, live["elements"], season=args.season, gameweek=args.gameweek)
    conn.close()

    print(f"Ingested results for {args.season} GW{args.gameweek}: {n} players.")


def _cmd_evaluate(args: argparse.Namespace) -> None:
    conn = db.connect(args.db_path)
    try:
        result = evaluate_service.run_evaluate(conn, args.season, args.gameweek, args.model)
    except evaluate_service.EvaluateNotReady as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        conn.close()

    print(f"Evaluation for {args.season} GW{args.gameweek}:")
    for model_version, model_result in result["by_model"].items():
        overall = model_result["overall"]
        print(f"\n  {model_version}: MAE {overall['mae']:.3f}   RMSE {overall['rmse']:.3f}   (n={overall['n']})")
        print(pd.DataFrame(model_result["by_position"]).to_string(index=False))
        if model_result["minutes_calibration"] is not None:
            print("  Minutes calibration:")
            print(pd.DataFrame(model_result["minutes_calibration"]).to_string(index=False))
    print(f"\nAppended to {accuracy_log.ACCURACY_LOG_PATH}")


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

    squad_cmd = subparsers.add_parser(
        "squad", help="Pull owned squad, selling prices, free transfers, chip status for FPL_TEAM_ID (M6, FR1)."
    )
    squad_cmd.add_argument("--season", required=True, help="e.g. 2026-27")
    squad_cmd.set_defaults(func=_cmd_squad)

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
    recommend_cmd.add_argument("--model", choices=MODEL_CHOICES, default="poisson")
    recommend_cmd.set_defaults(func=_cmd_recommend)

    plan_cmd = subparsers.add_parser(
        "plan", help="Rolling-horizon transfer + lineup plan against the owned squad (M6, FR3/4/8). Requires `squad` first."
    )
    plan_cmd.add_argument("--season", required=True, help="e.g. 2026-27")
    plan_cmd.add_argument("--gameweek", type=int, required=True)
    plan_cmd.add_argument("--as-of", default=None, help="ISO date; defaults to today.")
    plan_cmd.add_argument("--model", choices=MODEL_CHOICES, default="poisson")
    plan_cmd.add_argument(
        "--preset", choices=sorted(risk.load_presets()), default=None,
        help=f"Risk/secondary-weighting preset (PRD §6a.3). Defaults to config's risk.default_preset ({risk.default_preset_name()!r})."
    )
    plan_cmd.add_argument("--risk", type=float, default=None, help="Override the resolved preset's risk scalar directly.")
    plan_cmd.set_defaults(func=_cmd_plan)

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
    backtest_cmd.add_argument("--model", choices=MODEL_CHOICES, default="poisson")
    backtest_cmd.set_defaults(func=_cmd_backtest)

    results_cmd = subparsers.add_parser(
        "results", help="Fetch actual per-player results for a live gameweek and store them (needed before `evaluate`)."
    )
    results_cmd.add_argument("--season", required=True, help="e.g. 2026-27")
    results_cmd.add_argument("--gameweek", type=int, required=True)
    results_cmd.set_defaults(func=_cmd_results)

    evaluate_cmd = subparsers.add_parser(
        "evaluate", help="Compare logged predictions against actual results for a gameweek (Architecture §4.8, PRD M5)."
    )
    evaluate_cmd.add_argument("--season", required=True, help="e.g. 2024-25")
    evaluate_cmd.add_argument("--gameweek", type=int, required=True)
    evaluate_cmd.add_argument(
        "--model", choices=MODEL_CHOICES, default=None, help="Filter to one model; default compares all logged models."
    )
    evaluate_cmd.set_defaults(func=_cmd_evaluate)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
