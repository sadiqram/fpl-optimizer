"""Combines minutes_model and points_model into E[points] (Architecture §4.4)."""

from __future__ import annotations

import pandas as pd

from fpl_optimizer.models.minutes_model import MinutesModel
from fpl_optimizer.models.points_model import PointsModel

DEFAULT_STD_DEV = 2.5


class EnsemblePredictor:
    """E[points] = P(plays >= 60) x E[points | plays] + P(plays 1-59) x E[points | cameo]
    — Architecture §4.4's own formula, implemented as the Predictor protocol.
    """

    def __init__(self, minutes_model: MinutesModel | None = None, points_model: PointsModel | None = None):
        self._minutes_model = minutes_model or MinutesModel()
        self._points_model = points_model or PointsModel()

    def fit(self, features: pd.DataFrame, targets: pd.DataFrame) -> None:
        self._minutes_model.fit(features, targets)
        self._points_model.fit(features, targets)

    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        minutes_pred = self._minutes_model.predict(features)
        points_pred = self._points_model.predict(features)

        expected_points = (
            minutes_pred["p_start"] * points_pred["e_points_start"]
            + minutes_pred["p_cameo"] * points_pred["e_points_cameo"]
        )

        # No per-prediction uncertainty comes free from gradient-boosted point estimates.
        # Reusing the player's own recent scoring volatility (points_std_5, falling back
        # across windows) as a proxy is the currently-accepted approach pending a genuinely
        # distributional model (PRD §11 "Variance source, not variance existence") — the
        # same fallback pattern the baselines already use, for the same reason.
        std_dev = features["points_std_5"].fillna(features["points_std_10"]).fillna(DEFAULT_STD_DEV)

        return pd.DataFrame({
            "player_id": features["player_id"],
            "gameweek": features["gameweek"],
            "expected_points": expected_points,
            "p_start": minutes_pred["p_start"],
            "std_dev": std_dev,
        })
