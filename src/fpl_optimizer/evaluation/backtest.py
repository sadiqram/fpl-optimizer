"""Replays a historical season by advancing the injected clock gameweek by gameweek,
running the *same* recommendation pipeline live uses, and scoring the result (Architecture
§4.8, P2). Every gameweek here goes through strategy.horizon.recommend_gameweek — the
identical function `fpl-optimizer recommend` calls — so backtest and live can't drift apart.
"""

from __future__ import annotations

import pandas as pd

from fpl_optimizer.clock import FixedClock
from fpl_optimizer.evaluation import metrics
from fpl_optimizer.models.base import Predictor
from fpl_optimizer.models.training_data import season_as_of_date
from fpl_optimizer.optimize import squad as squad_optimize
from fpl_optimizer.storage import db
from fpl_optimizer.strategy import horizon


def _hindsight_squad_points(predictions_and_features: pd.DataFrame, actuals: pd.DataFrame) -> float:
    """Re-solves the same squad-selection problem, but with *actual* points (knowable only
    in hindsight) as the objective instead of predicted ones — the best possible squad that
    gameweek's real results would have allowed, under identical budget/formation/team-limit
    constraints. The yardstick metrics.squad_selection_regret compares against."""
    merged = predictions_and_features.merge(actuals[["player_id", "total_points"]], on="player_id")
    merged = merged.dropna(subset=["element_type", "team_id", "now_cost"])
    hindsight_pool = merged[["player_id", "element_type", "team_id", "now_cost"]].copy()
    hindsight_pool["expected_points"] = merged["total_points"]  # solver's objective column — fed actual points on purpose
    hindsight_squad = squad_optimize.build_squad(hindsight_pool)
    return float(hindsight_squad["expected_points"].sum())


def backtest_season(conn, season: str, start_gameweek: int, end_gameweek: int, predictor: Predictor) -> pd.DataFrame:
    """Replays `season` from `start_gameweek` to `end_gameweek` inclusive.

    `predictor` must already be fitted. Training on data that overlaps the backtest window
    would leak the backtest's own future into itself — the caller is responsible for
    training only on gameweeks strictly before `start_gameweek` (cli.py's `_cmd_backtest`
    does this for the GBM ensemble; the baselines are stateless and don't need it).

    Returns one row per gameweek with data to score: mae, rmse (season-wide; see
    evaluation/metrics.py for the by-position/calibration breakdowns, computed separately
    since they need the raw predictions+actuals this function doesn't return), and
    squad_regret (points left on the table vs. the hindsight-optimal squad — see
    metrics.squad_selection_regret's docstring for why this is scoped to squad selection,
    not transfers). Gameweeks with no recorded outcome yet are silently skipped, not
    zero-filled — there's nothing to score them against.

    Season-level rank isn't computed here: it needs a season-long simulation with an
    owned squad's continuity across gameweeks (transfers, hits, banked FTs), which is the
    Strategy layer's job (PRD M6) — this backtests independent per-gameweek recommendations
    ("what would the optimizer pick fresh, each week"), not a season played start to finish.
    """
    as_of_date = season_as_of_date(conn, season)
    rows = []

    for gameweek in range(start_gameweek, end_gameweek + 1):
        result = horizon.recommend_gameweek(conn, FixedClock(as_of_date), season, gameweek, predictor)

        actual_rows = db.get_player_gw_stats_for_gameweek(conn, season, gameweek)
        if not actual_rows:
            continue
        actuals = pd.DataFrame([dict(r) for r in actual_rows])

        predictions = result["predictions"]
        scored = predictions.merge(actuals[["player_id", "total_points"]], on="player_id", how="inner")
        if scored.empty:
            continue
        error = scored["expected_points"] - scored["total_points"]

        recommended_points = (
            result["squad"][["player_id"]]
            .merge(actuals[["player_id", "total_points"]], on="player_id", how="left")["total_points"]
            .fillna(0)
            .sum()
        )
        hindsight_points = _hindsight_squad_points(predictions, actuals)

        rows.append({
            "gameweek": gameweek,
            "n_predictions_scored": len(scored),
            "mae": error.abs().mean(),
            "rmse": (error ** 2).mean() ** 0.5,
            "recommended_squad_points": recommended_points,
            "hindsight_squad_points": hindsight_points,
            "squad_regret": metrics.squad_selection_regret(recommended_points, hindsight_points),
        })

    return pd.DataFrame(rows)
