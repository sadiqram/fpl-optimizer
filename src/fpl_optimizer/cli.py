"""CLI entry point (Architecture §4.7)."""

from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

import joblib
import pandas as pd

from fpl_optimizer import clock
from fpl_optimizer.evaluation import backtest, metrics
from fpl_optimizer.features import build as feature_build
from fpl_optimizer.ingestion import archive_loader, understat
from fpl_optimizer.ingestion.fpl_api import FPLClient
from fpl_optimizer.ingestion.snapshots import save_raw
from fpl_optimizer.models import training_data
from fpl_optimizer.models.baseline import NaivePredictor, PoissonPredictor
from fpl_optimizer.models.ensemble import EnsemblePredictor
from fpl_optimizer.optimize import constraints
from fpl_optimizer.storage import db
from fpl_optimizer.strategy import chips, horizon, risk, squad_state

PREDICTORS = {"naive": NaivePredictor, "poisson": PoissonPredictor}
MODEL_CHOICES = [*sorted(PREDICTORS), "gbm"]
CHIP_DISPLAY_NAMES = {"wildcard": "Wildcard", "freehit": "Free Hit", "bboost": "Bench Boost", "3xc": "Triple Captain"}

DEFAULT_DB_PATH = Path("data/db/fpl.sqlite")
DEFAULT_MODELS_DIR = Path("data/artifacts/models")
ACCURACY_LOG_PATH = Path("data/artifacts/evaluation/accuracy_log.csv")


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

    conn = db.connect(args.db_path)  # connect() already ensures the schema is current
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


def _cmd_squad(args: argparse.Namespace) -> None:
    """Owned-squad state ingestion (M6, FR1): current squad, purchase prices, free
    transfers, chip status — the state `plan` needs and has never existed anywhere in the
    app until now (squad_transfers has sat empty in the schema since M1)."""
    load_dotenv()
    team_id = os.environ.get("FPL_TEAM_ID", "").strip()
    if not team_id:
        raise SystemExit("FPL_TEAM_ID not set in .env — required for `squad`.")
    team_id = int(team_id)

    client = FPLClient()
    now = datetime.now(timezone.utc)

    history = client.entry_history(team_id)
    save_raw("fpl_api", f"entry-{team_id}-history", history, when=now)

    if not history["current"]:
        # NFR2: the season hasn't started yet (no gameweek has locked for this team) — there
        # is no "current squad" to report. Not an error, just nothing to do yet.
        raise SystemExit(f"No locked gameweeks yet for team {team_id} — the season hasn't started.")

    last_locked_gw = max(row["event"] for row in history["current"])
    last_locked_row = next(row for row in history["current"] if row["event"] == last_locked_gw)

    transfers = client.entry_transfers(team_id)
    save_raw("fpl_api", f"entry-{team_id}-transfers", transfers, when=now)

    picks = client.entry_picks(team_id, last_locked_gw)
    save_raw("fpl_api", f"entry-{team_id}-picks-gw{last_locked_gw}", picks, when=now)

    conn = db.connect(args.db_path)

    db.insert_squad_transfers(conn, [
        {
            "event": t["event"], "element_in": t["element_in"], "element_in_cost": t["element_in_cost"],
            "element_out": t["element_out"], "element_out_cost": t["element_out_cost"], "time": t["time"],
        }
        for t in transfers
    ])

    element_to_player = db.get_element_id_to_player_id_map(conn)
    owned_rows = []
    for pick in picks["picks"]:
        player_id = element_to_player.get(pick["element"])
        if player_id is None:
            continue  # NFR2: an unrecognized element id shouldn't crash the whole ingest
        owned_rows.append({
            "player_id": player_id,
            "is_starting": int(pick["position"] <= 11),
            "is_captain": int(pick["is_captain"]),
            "is_vice_captain": int(pick["is_vice_captain"]),
            "purchase_price": squad_state.resolve_purchase_price(conn, player_id, args.season),
        })
    db.insert_owned_squad(conn, owned_rows, season=args.season, gameweek=last_locked_gw, recorded_at=now.isoformat())

    free_transfers = squad_state.compute_free_transfers(history["current"], history["chips"])
    chips_available = squad_state.resolve_chips_available(history["chips"])
    db.insert_team_state(
        conn, season=args.season, gameweek=last_locked_gw + 1, bank=last_locked_row.get("bank"),
        free_transfers=free_transfers, chips_available=chips_available, recorded_at=now.isoformat(),
    )
    conn.close()

    print(f"Squad state for team {team_id}, {args.season}, as of GW{last_locked_gw}:")
    print(f"  {len(owned_rows)} players, bank {(last_locked_row.get('bank') or 0) / 10:.1f}m")
    print(f"  Free transfers available for GW{last_locked_gw + 1}: {free_transfers}")
    print(f"  Chips available: {', '.join(chips_available) or 'none'}")


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
    """Baselines are stateless (fit() is a documented no-op); gbm loads a joblib model
    already trained and saved by `train --save` — recommend never trains inline, training
    is a separate lifecycle stage (strategy.horizon's own docstring)."""
    if args.model == "gbm":
        model_path = DEFAULT_MODELS_DIR / f"gbm_ensemble_{args.season}.joblib"
        if not model_path.exists():
            raise SystemExit(
                f"No saved GBM model for {args.season} at {model_path} — "
                f"run `fpl-optimizer train --season {args.season} ... --save` first."
            )
        return joblib.load(model_path)
    predictor = PREDICTORS[args.model]()
    predictor.fit(pd.DataFrame())
    return predictor


def _cmd_recommend(args: argparse.Namespace) -> None:
    conn = db.connect(args.db_path)
    the_clock = clock.FixedClock(args.as_of) if args.as_of else clock.SystemClock()

    predictor = _load_predictor(args)

    result = horizon.recommend_gameweek(conn, the_clock, args.season, args.gameweek, predictor)
    usable = result["predictions"].dropna(subset=["element_type", "team_id", "now_cost", "expected_points"])
    dropped = len(result["predictions"]) - len(usable)
    squad_df, picked = result["squad"], result["lineup"]

    names = {r["id"]: r["web_name"] for r in conn.execute("SELECT id, web_name FROM players").fetchall()}

    def label(player_id: int) -> str:
        pts = usable.loc[usable.player_id == player_id, "expected_points"].iloc[0]
        tag = " (C)" if player_id == picked["captain"] else " (VC)" if player_id == picked["vice_captain"] else ""
        return f"{names.get(player_id, f'#{player_id}')}{tag} — {pts:.1f} xPts"

    def player_summary(player_id: int) -> dict:
        pts = usable.loc[usable.player_id == player_id, "expected_points"].iloc[0]
        return {"player_id": player_id, "name": names.get(player_id, f"#{player_id}"), "expected_points": float(pts)}

    # Log every prediction and every recommendation made (FR6, Architecture P3) — this is
    # the data `evaluate` later joins against real outcomes.
    prediction_rows = usable[["player_id", "expected_points", "p_start", "std_dev"]].to_dict("records")
    db.insert_predictions(
        conn, prediction_rows, model_version=args.model, season=args.season,
        gameweek=args.gameweek, run_date=result["as_of_date"],
    )
    run_id = uuid.uuid4().hex
    payload = {
        "season": args.season,
        "gameweek": args.gameweek,
        "model": args.model,
        "as_of_date": result["as_of_date"],
        "squad_cost": float(squad_df["now_cost"].sum()),
        "squad_expected_points": float(squad_df["expected_points"].sum()),
        "starting_xi": [player_summary(pid) for pid in picked["starting_xi"]],
        "bench": [player_summary(pid) for pid in picked["bench"]],
        "captain": player_summary(picked["captain"]),
        "vice_captain": player_summary(picked["vice_captain"]),
    }
    db.insert_recommendation(
        conn, run_id, created_at=datetime.now(timezone.utc).isoformat(),
        season=args.season, gameweek=args.gameweek, payload=payload,
    )
    conn.close()

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
    print(f"\n  Logged {len(prediction_rows)} predictions and recommendation {run_id} for later `evaluate`.")


def _cmd_plan(args: argparse.Namespace) -> None:
    """Rolling-horizon transfer + lineup planning against a real owned squad (M6, FR3/4/8).
    Distinct from `recommend`, which picks a fresh 15 from scratch (still useful for
    wildcards and backtesting) — `plan` requires `squad` to have been run first, same
    pattern `evaluate` already uses for `results`."""
    conn = db.connect(args.db_path)
    the_clock = clock.FixedClock(args.as_of) if args.as_of else clock.SystemClock()

    team_state_row = db.get_team_state(conn, args.season, args.gameweek)
    owned_rows = db.get_owned_squad(conn, args.season, args.gameweek - 1)
    if team_state_row is None or not owned_rows:
        conn.close()
        raise SystemExit(
            f"No squad state for {args.season} GW{args.gameweek} — run `fpl-optimizer squad` first."
        )

    current_prices = {r["player_id"]: r["now_cost"] for r in db.get_player_snapshots_as_of(conn, the_clock.today())}
    owned_squad = pd.DataFrame([
        {
            "player_id": r["player_id"],
            # Selling price is recomputed from the latest known price, not frozen at
            # `squad` ingest time — price moves between when you last checked and now.
            # An unresolved purchase_price (NFR2) falls back to current price (break-even).
            "selling_price": (
                constraints.selling_price(r["purchase_price"], current_prices[r["player_id"]])
                if r["purchase_price"] is not None and r["player_id"] in current_prices
                else current_prices.get(r["player_id"], r["purchase_price"])
            ),
        }
        for r in owned_rows
    ])

    preset = risk.resolve_preset(args.preset or risk.default_preset_name())
    if args.risk is not None:
        preset = {**preset, "risk": args.risk}

    predictor = _load_predictor(args)
    result = horizon.plan_horizon(
        conn, the_clock, args.season, args.gameweek, predictor, owned_squad,
        bank=team_state_row["bank"] or 0, free_transfers=team_state_row["free_transfers"],
        preset=preset,
    )
    transfer_result, picked = result["transfer_result"], result["lineup"]
    week1 = result["weekly_predictions"][0]["predictions"]

    chips_available = json.loads(team_state_row["chips_available"] or "[]")
    chip_scenarios = chips.evaluate_chip_scenarios(
        result, owned_squad, bank=team_state_row["bank"] or 0, chips_available=chips_available
    )

    names = {r["id"]: r["web_name"] for r in conn.execute("SELECT id, web_name FROM players").fetchall()}
    conn.close()

    def label(player_id: int) -> str:
        pts = week1.loc[week1.player_id == player_id, "expected_points"]
        pts_str = f"{pts.iloc[0]:.1f} xPts" if len(pts) else "? xPts"
        tag = " (C)" if player_id == picked["captain"] else " (VC)" if player_id == picked["vice_captain"] else ""
        return f"{names.get(player_id, f'#{player_id}')}{tag} — {pts_str}"

    preset_name = args.preset or risk.default_preset_name()
    print(f"Plan for {args.season} GW{args.gameweek} ({args.model} model, {preset_name} preset, as of {result['as_of_date']})")
    if transfer_result["transfers_in"]:
        print("\n  Transfers:")
        for out_id, in_id in zip(transfer_result["transfers_out"], transfer_result["transfers_in"]):
            print(f"    OUT {names.get(out_id, f'#{out_id}')}  ->  IN {names.get(in_id, f'#{in_id}')}")
        print(f"  Hits taken: {transfer_result['hits_taken']} (-{transfer_result['hit_cost']} pts)")
        print(f"  Net expected points gain (horizon, net of hits): {transfer_result['expected_points_gain']:.1f}")
    else:
        print("\n  Hold — no transfer clears the hit threshold this week.")
    print("\n  Starting XI:")
    for player_id in picked["starting_xi"]:
        print(f"    {label(player_id)}")
    print("  Bench:")
    for player_id in picked["bench"]:
        print(f"    {label(player_id)}")

    if chip_scenarios:
        print("\n  Chip opportunities:")
        for chip_name, scenario in chip_scenarios.items():
            print(f"    {CHIP_DISPLAY_NAMES.get(chip_name, chip_name)}: {scenario['delta']:+.1f} pts — {scenario['reasoning']}")


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


def _append_accuracy_log(rows: list[dict]) -> None:
    """Appends one summary row per model to the running accuracy log (Architecture §4.8) —
    the persisted trend line `evaluate` builds up over a season. Re-evaluating the same
    (season, gameweek, model_version) updates that row in place rather than duplicating.
    """
    ACCURACY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    new_rows = pd.DataFrame(rows)
    key = ["season", "gameweek", "model_version"]
    if ACCURACY_LOG_PATH.exists():
        existing = pd.read_csv(ACCURACY_LOG_PATH)
        existing = existing[~existing.set_index(key).index.isin(new_rows.set_index(key).index)]
        combined = pd.concat([existing, new_rows], ignore_index=True)
    else:
        combined = new_rows
    combined.sort_values(key).to_csv(ACCURACY_LOG_PATH, index=False)
    print(f"\nAppended to {ACCURACY_LOG_PATH}")


def _cmd_evaluate(args: argparse.Namespace) -> None:
    conn = db.connect(args.db_path)
    predictions = db.get_latest_predictions(conn, args.season, args.gameweek, model_version=args.model)
    actual_rows = db.get_player_gw_stats_for_gameweek(conn, args.season, args.gameweek)
    # element_type isn't stored on predictions (it's a slowly-changing player attribute,
    # not part of a prediction) — fetched separately for the by-position breakdown below.
    element_types = {r["id"]: r["element_type"] for r in conn.execute("SELECT id, element_type FROM players")}
    conn.close()

    if not predictions:
        raise SystemExit(
            f"No logged predictions for {args.season} GW{args.gameweek} — run `fpl-optimizer recommend` for it first."
        )
    if not actual_rows:
        raise SystemExit(
            f"No recorded results for {args.season} GW{args.gameweek} — run `fpl-optimizer results` for it first."
        )

    predictions_df = pd.DataFrame([dict(r) for r in predictions])
    predictions_df["element_type"] = predictions_df["player_id"].map(element_types)
    actuals_df = pd.DataFrame([dict(r) for r in actual_rows])

    print(f"Evaluation for {args.season} GW{args.gameweek}:")
    log_rows = []
    for model_version, group in predictions_df.groupby("model_version"):
        overall = metrics.overall_mae_rmse(group, actuals_df)
        print(f"\n  {model_version}: MAE {overall['mae']:.3f}   RMSE {overall['rmse']:.3f}   (n={overall['n']})")
        print(metrics.mae_rmse_by_position(group, actuals_df).to_string(index=False))

        if group["p_start"].notna().any():
            print("  Minutes calibration:")
            print(metrics.minutes_calibration(group, actuals_df).to_string(index=False))

        log_rows.append({
            "season": args.season,
            "gameweek": args.gameweek,
            "model_version": model_version,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "n": overall["n"],
            "mae": overall["mae"],
            "rmse": overall["rmse"],
        })

    _append_accuracy_log(log_rows)


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
