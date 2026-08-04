"""Scoring functions shared by the backtest harness and (eventually) live tracking —
Architecture §4.8 lists these metrics once, for both mechanisms; this is where the actual
calculations live so neither reimplements them.

Live tracking itself (join logged predictions to real outcomes after each real gameweek
resolves, append to a running accuracy log) isn't built yet — that's PRD M5 (weekly live
run). These are pure functions over already-fetched DataFrames; they don't touch the DB.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def mae_rmse_by_position(predictions: pd.DataFrame, actuals: pd.DataFrame) -> pd.DataFrame:
    """predictions: player_id, element_type, expected_points. actuals: player_id,
    total_points. One row per element_type: mae, rmse, n.
    """
    merged = predictions.merge(actuals[["player_id", "total_points"]], on="player_id")
    error = merged["expected_points"] - merged["total_points"]
    grouped = error.groupby(merged["element_type"])
    return pd.DataFrame({
        "mae": grouped.apply(lambda e: e.abs().mean()),
        "rmse": grouped.apply(lambda e: np.sqrt((e ** 2).mean())),
        "n": grouped.count(),
    }).reset_index()


def minutes_calibration(predictions: pd.DataFrame, actuals: pd.DataFrame, n_bins: int = 5) -> pd.DataFrame:
    """predictions: player_id, p_start. actuals: player_id, minutes. Buckets predicted
    P(start) into `n_bins` and compares each bucket's mean predicted probability against
    that bucket's actual start rate — a well-calibrated model has these track close to 1:1.
    """
    merged = predictions.merge(actuals[["player_id", "minutes"]], on="player_id")
    merged["actual_started"] = (merged["minutes"] >= 60).astype(int)
    merged["bucket"] = pd.cut(merged["p_start"], bins=n_bins, include_lowest=True)
    grouped = merged.groupby("bucket", observed=True)
    return pd.DataFrame({
        "predicted_p_start_mean": grouped["p_start"].mean(),
        "actual_start_rate": grouped["actual_started"].mean(),
        "n": grouped.size(),
    }).reset_index()


def points_calibration(predictions: pd.DataFrame, actuals: pd.DataFrame, n_bins: int = 5) -> pd.DataFrame:
    """predictions: player_id, expected_points. actuals: player_id, total_points. Same idea
    as minutes_calibration but for the points prediction: buckets predicted expected_points
    into `n_bins` (by rank, via qcut, so bins stay populated even with a skewed
    distribution) and compares each bucket's mean prediction against its mean actual.
    """
    merged = predictions.merge(actuals[["player_id", "total_points"]], on="player_id")
    merged["bucket"] = pd.qcut(merged["expected_points"], q=n_bins, duplicates="drop")
    grouped = merged.groupby("bucket", observed=True)
    return pd.DataFrame({
        "predicted_mean": grouped["expected_points"].mean(),
        "actual_mean": grouped["total_points"].mean(),
        "n": grouped.size(),
    }).reset_index()


def squad_selection_regret(recommended_squad_actual_points: float, hindsight_squad_actual_points: float) -> float:
    """Points left on the table by picking the model's recommended squad instead of the
    best possible squad under the same budget/formation constraints, chosen with perfect
    hindsight of actual results. Both figures must be *actual* points scored, not predicted.

    Scoped to squad selection, not transfers: Architecture §4.8 describes this metric as
    "points from recommended transfer vs. best possible in hindsight vs. holding", which
    needs an owned squad's continuity and purchase price to mean anything — that state
    doesn't exist until the Strategy layer's transfer logic is built (PRD M6). This is the
    buildable slice of the same idea for now: not "was the transfer right", but "was the
    squad right".
    """
    return hindsight_squad_actual_points - recommended_squad_actual_points
