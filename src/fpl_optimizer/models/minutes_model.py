"""P(plays >= 60min) / P(plays 1-59min) model (Architecture §4.4).

Minutes are driven by team news, rotation, and injury — largely categorical — and are the
single biggest source of catastrophic error: a predicted 6-point haul from a player who
doesn't start is a total loss. That's the whole justification for splitting this out from
points prediction rather than learning one model that has to implicitly cover both.
"""

from __future__ import annotations

import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

from fpl_optimizer.models.base import feature_matrix, usable_columns

STARTER_MINUTES_THRESHOLD = 60


def _positive_class_proba(model: HistGradientBoostingClassifier, x: pd.DataFrame) -> pd.Series:
    """predict_proba's column order follows model.classes_, which only contains the labels
    actually seen during fit — on a small/lopsided training set, class 1 may never appear.
    Falls back to 0 rather than letting a bare [:, 1] index into the wrong column or crash."""
    proba = model.predict_proba(x)
    classes = list(model.classes_)
    if 1 not in classes:
        return pd.Series(0.0, index=x.index)
    return pd.Series(proba[:, classes.index(1)], index=x.index)


class MinutesModel:
    """P(minutes >= 60) and P(1 <= minutes <= 59) as two independent binary classifiers.

    HistGradientBoosting* natively handles NaN features — no imputation step needed for
    players with partial history, which is exactly what the features layer produces by
    design for missing data (Architecture §4.3) rather than a fabricated 0.
    """

    def __init__(self, **hgb_kwargs):
        self._start_model = HistGradientBoostingClassifier(**hgb_kwargs)
        self._cameo_model = HistGradientBoostingClassifier(**hgb_kwargs)
        self._feature_columns: list[str] = []
        self._fitted = False

    def fit(self, features: pd.DataFrame, targets: pd.DataFrame) -> None:
        self._feature_columns = usable_columns(feature_matrix(features))
        x = feature_matrix(features)[self._feature_columns]
        is_start = (targets["minutes"] >= STARTER_MINUTES_THRESHOLD).astype(int)
        is_cameo = targets["minutes"].between(1, STARTER_MINUTES_THRESHOLD - 1).astype(int)
        self._start_model.fit(x, is_start)
        self._cameo_model.fit(x, is_cameo)
        self._fitted = True

    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        if not self._fitted:
            raise RuntimeError("MinutesModel.fit() must be called before predict()")
        x = feature_matrix(features)[self._feature_columns]
        return pd.DataFrame({
            "player_id": features["player_id"],
            "p_start": _positive_class_proba(self._start_model, x),
            "p_cameo": _positive_class_proba(self._cameo_model, x),
        })
