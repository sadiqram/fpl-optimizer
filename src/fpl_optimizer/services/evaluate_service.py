"""Extracted from `cli._cmd_evaluate`. Global/shared — predictions and outcomes aren't
per-tenant, only the recommendations built from them are."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pandas as pd

from fpl_optimizer.evaluation import accuracy_log, metrics
from fpl_optimizer.storage import db


class EvaluateNotReady(ValueError):
    pass


def run_evaluate(conn: sqlite3.Connection, season: str, gameweek: int, model: str | None) -> dict:
    predictions = db.get_latest_predictions(conn, season, gameweek, model_version=model)
    actual_rows = db.get_player_gw_stats_for_gameweek(conn, season, gameweek)
    element_types = {r["id"]: r["element_type"] for r in conn.execute("SELECT id, element_type FROM players")}

    if not predictions:
        raise EvaluateNotReady(f"No logged predictions for {season} GW{gameweek} — run `recommend` for it first.")
    if not actual_rows:
        raise EvaluateNotReady(f"No recorded results for {season} GW{gameweek} — run `results` for it first.")

    predictions_df = pd.DataFrame([dict(r) for r in predictions])
    predictions_df["element_type"] = predictions_df["player_id"].map(element_types)
    actuals_df = pd.DataFrame([dict(r) for r in actual_rows])

    by_model = {}
    log_rows = []
    for model_version, group in predictions_df.groupby("model_version"):
        overall = metrics.overall_mae_rmse(group, actuals_df)
        by_position = metrics.mae_rmse_by_position(group, actuals_df)
        minutes_cal = metrics.minutes_calibration(group, actuals_df) if group["p_start"].notna().any() else None
        by_model[model_version] = {
            "overall": overall,
            "by_position": by_position.to_dict("records"),
            "minutes_calibration": minutes_cal.to_dict("records") if minutes_cal is not None else None,
        }
        log_rows.append({
            "season": season, "gameweek": gameweek, "model_version": model_version,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "n": overall["n"], "mae": overall["mae"], "rmse": overall["rmse"],
        })

    accuracy_log.append_accuracy_log(log_rows)
    return {"season": season, "gameweek": gameweek, "by_model": by_model}
