"""E[points | plays] model, driven by underlying quality and matchup (Architecture §4.4)."""

from __future__ import annotations

import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from fpl_optimizer.models.base import feature_matrix, usable_columns
from fpl_optimizer.models.minutes_model import STARTER_MINUTES_THRESHOLD

# Below this many cameo-minute training rows, a dedicated regressor is more likely to
# overfit than to generalize — fall back to a scaling heuristic instead of trusting a fit
# on too little data.
MIN_CAMEO_TRAINING_ROWS = 30
CAMEO_MINUTES_ASSUMPTION = 25  # a typical substitute appearance, for the fallback heuristic
FULL_GAME_MINUTES = 90


class PointsModel:
    """E[points | started] (minutes >= 60) via a regressor trained on that subset.
    E[points | cameo] (1-59 minutes) via a second regressor on its own subset, or — when
    there's too little cameo data to fit reliably — a documented scaling heuristic off the
    started-game prediction. This is v1: a stated simplification, not a hidden one, in the
    same spirit as the Poisson baseline's own appearance-points simplification.
    """

    def __init__(self, **hgb_kwargs):
        self._start_model = HistGradientBoostingRegressor(**hgb_kwargs)
        self._cameo_model = HistGradientBoostingRegressor(**hgb_kwargs)
        self._start_feature_columns: list[str] = []
        self._cameo_feature_columns: list[str] = []
        self._cameo_fitted = False
        self._fitted = False

    def fit(self, features: pd.DataFrame, targets: pd.DataFrame) -> None:
        x = feature_matrix(features)
        is_start = targets["minutes"] >= STARTER_MINUTES_THRESHOLD
        is_cameo = targets["minutes"].between(1, STARTER_MINUTES_THRESHOLD - 1)

        # Column validity (usable_columns) is computed per training subset, not once on the
        # full x — a column can be well-populated overall but entirely NaN within just the
        # started-games or just the cameo-games slice, and each regressor only ever sees
        # its own slice.
        self._start_feature_columns = usable_columns(x[is_start])
        self._start_model.fit(x.loc[is_start, self._start_feature_columns], targets.loc[is_start, "total_points"])

        self._cameo_fitted = bool(is_cameo.sum() >= MIN_CAMEO_TRAINING_ROWS)
        if self._cameo_fitted:
            self._cameo_feature_columns = usable_columns(x[is_cameo])
            self._cameo_model.fit(x.loc[is_cameo, self._cameo_feature_columns], targets.loc[is_cameo, "total_points"])

        self._fitted = True

    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        if not self._fitted:
            raise RuntimeError("PointsModel.fit() must be called before predict()")
        x = feature_matrix(features)
        e_points_start = pd.Series(self._start_model.predict(x[self._start_feature_columns]), index=x.index)

        if self._cameo_fitted:
            e_points_cameo = pd.Series(self._cameo_model.predict(x[self._cameo_feature_columns]), index=x.index)
        else:
            # Scale the full-game prediction by typical-cameo/full-game minutes, then drop
            # one appearance point (2 pts for >=60 min vs 1 pt for any lesser appearance).
            e_points_cameo = (e_points_start * (CAMEO_MINUTES_ASSUMPTION / FULL_GAME_MINUTES) - 1).clip(lower=0)

        return pd.DataFrame({
            "player_id": features["player_id"],
            "e_points_start": e_points_start,
            "e_points_cameo": e_points_cameo,
        })
