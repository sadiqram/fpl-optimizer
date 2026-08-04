"""Naive + Poisson baselines. Permanent fixture, not scaffolding — every model is compared
against these (Architecture §4.4): without a live baseline to beat, "the model is good" is
unfalsifiable.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_STD_DEV = 2.5  # fallback when a player has too little history to compute one

# FPL scoring by element_type id (1=GKP, 2=DEF, 3=MID, 4=FWD). Assists are 3 for every position.
GOAL_POINTS = {1: 6, 2: 6, 3: 5, 4: 4}
CLEAN_SHEET_POINTS = {1: 4, 2: 4, 3: 1, 4: 0}
ASSIST_POINTS = 3


def _first_available(features: pd.DataFrame, *columns: str) -> pd.Series:
    """First non-null value across `columns`, in order — the window-fallback pattern
    (5-GW form if we have it, else 10-GW, else 3-GW) used by both baselines below."""
    result = features[columns[0]].copy()
    for col in columns[1:]:
        result = result.fillna(features[col])
    return result


class NaivePredictor:
    """expected_points = recent scoring average. Falls back across windows as history
    allows (5 -> 10 -> 3 GWs), then to 0 for a player with no history at all — never
    silently drops the row."""

    def fit(self, features: pd.DataFrame, targets: pd.DataFrame | None = None) -> None:
        pass  # rule-based: nothing learned from training data

    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        expected_points = _first_available(features, "points_mean_5", "points_mean_10", "points_mean_3").fillna(0.0)
        p_start = features["start_rate_5"].fillna(0.5)
        std_dev = _first_available(features, "points_std_5", "points_std_10", "points_std_3").fillna(DEFAULT_STD_DEV)
        return pd.DataFrame({
            "player_id": features["player_id"],
            "gameweek": features["gameweek"],
            "expected_points": expected_points,
            "p_start": p_start,
            "std_dev": std_dev,
        })


class PoissonPredictor:
    """Models goals/assists/clean-sheets as Poisson-rate processes from recent underlying
    numbers (xG, xA, team goals-against), scored by real FPL rules per position — a
    genuinely different model from NaivePredictor's "take an average", not a second
    flavor of the same thing (Architecture §4.4: baselines must be comparable
    apples-to-apples, which requires them to actually differ in approach).

    Known simplification, stated rather than hidden: appearance + goal + assist +
    clean-sheet points only. Bonus, saves, cards, and defensive-contribution points are not
    modelled — this is a baseline to beat, not the eventual model.
    """

    def fit(self, features: pd.DataFrame, targets: pd.DataFrame | None = None) -> None:
        pass

    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        goal_pts = features["element_type"].map(GOAL_POINTS).fillna(5)
        cs_pts = features["element_type"].map(CLEAN_SHEET_POINTS).fillna(0)

        xg = _first_available(features, "xg_mean_5", "xg_mean_10").fillna(0.0)
        xa = _first_available(features, "xa_mean_5", "xa_mean_10").fillna(0.0)
        p_start = features["start_rate_5"].fillna(0.5)

        # Poisson P(X=0) = e^-lambda: probability the opponent's expected goals land on
        # zero, i.e. this player's team keeps a clean sheet. 1.5 is a rough league-average
        # goals-against fallback for a team with no rolling history yet.
        goals_against_avg = features["goals_against_avg"].fillna(1.5)
        p_clean_sheet = np.exp(-goals_against_avg)

        # Appearance points: 2 if nailed-on starter, 0 if they never play, scaled linearly
        # in between by start rate — a simplification (real appearance points are step-
        # shaped: 0/1/2), not a full minutes distribution model.
        appearance_points = 2 * p_start

        expected_points = appearance_points + xg * goal_pts + xa * ASSIST_POINTS + p_clean_sheet * cs_pts

        std_dev = _first_available(features, "points_std_5", "points_std_10", "points_std_3").fillna(DEFAULT_STD_DEV)
        return pd.DataFrame({
            "player_id": features["player_id"],
            "gameweek": features["gameweek"],
            "expected_points": expected_points,
            "p_start": p_start,
            "std_dev": std_dev,
        })
