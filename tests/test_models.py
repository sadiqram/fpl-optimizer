"""Model-layer tests: Predictor protocol compliance, NaN handling, and the cameo-data
fallback threshold (points_model.py's documented simplification) — using synthetic data,
not live DB state, so these run fast and don't depend on what's been ingested.
"""

import numpy as np
import pandas as pd
import pytest

from fpl_optimizer.models.ensemble import EnsemblePredictor
from fpl_optimizer.models.minutes_model import MinutesModel
from fpl_optimizer.models.points_model import MIN_CAMEO_TRAINING_ROWS, PointsModel


def _synthetic_features_and_targets(n_rows: int, cameo_fraction: float = 0.3, seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    player_id = np.arange(1, n_rows + 1)
    points_mean_5 = rng.uniform(0, 8, n_rows)

    features = pd.DataFrame({
        "player_id": player_id,
        "season": "2025-26",
        "gameweek": rng.integers(2, 20, n_rows),
        "status": "a",
        "now_cost": rng.integers(40, 130, n_rows),
        "element_type": rng.integers(1, 5, n_rows),
        "team_id": rng.integers(1, 21, n_rows),
        "points_mean_5": points_mean_5,
        "points_std_5": rng.uniform(0.5, 3.0, n_rows),
        "points_std_10": rng.uniform(0.5, 3.0, n_rows),
        "start_rate_5": rng.uniform(0, 1, n_rows),
        "xg_mean_5": rng.uniform(0, 1, n_rows),
    })
    # Some missing history, same as the real features layer produces for new/rotated players.
    features.loc[features.sample(frac=0.1, random_state=seed).index, "points_mean_5"] = np.nan

    is_cameo = rng.uniform(0, 1, n_rows) < cameo_fraction
    minutes = np.where(is_cameo, rng.integers(1, 60, n_rows), rng.choice([0, 90], n_rows, p=[0.15, 0.85]))
    total_points = np.where(minutes >= 60, rng.integers(1, 15, n_rows), np.where(minutes > 0, rng.integers(0, 4, n_rows), 0))
    targets = pd.DataFrame({"player_id": player_id, "gameweek": features["gameweek"], "minutes": minutes, "total_points": total_points})

    return features, targets


def test_minutes_model_predicts_valid_probabilities():
    features, targets = _synthetic_features_and_targets(200)
    model = MinutesModel(max_iter=20)
    model.fit(features, targets)
    predictions = model.predict(features)

    assert set(predictions.columns) == {"player_id", "p_start", "p_cameo"}
    assert predictions["p_start"].between(0, 1).all()
    assert predictions["p_cameo"].between(0, 1).all()


def test_minutes_model_predict_before_fit_raises():
    features, _ = _synthetic_features_and_targets(10)
    with pytest.raises(RuntimeError):
        MinutesModel().predict(features)


def test_points_model_uses_real_cameo_model_when_data_is_plentiful():
    features, targets = _synthetic_features_and_targets(300, cameo_fraction=0.4)
    assert targets["minutes"].between(1, 59).sum() >= MIN_CAMEO_TRAINING_ROWS  # sanity-check the fixture itself

    model = PointsModel(max_iter=20)
    model.fit(features, targets)
    assert model._cameo_fitted is True


def test_points_model_falls_back_to_heuristic_when_cameo_data_is_sparse():
    features, targets = _synthetic_features_and_targets(20, cameo_fraction=0.05)
    assert targets["minutes"].between(1, 59).sum() < MIN_CAMEO_TRAINING_ROWS  # sanity-check the fixture itself

    model = PointsModel(max_iter=20)
    model.fit(features, targets)
    assert model._cameo_fitted is False

    predictions = model.predict(features)
    assert (predictions["e_points_cameo"] >= 0).all()  # heuristic is clipped, never negative


def test_ensemble_predictor_matches_protocol_shape():
    features, targets = _synthetic_features_and_targets(200)
    predictor = EnsemblePredictor(minutes_model=MinutesModel(max_iter=20), points_model=PointsModel(max_iter=20))
    predictor.fit(features, targets)
    predictions = predictor.predict(features)

    assert set(predictions.columns) == {"player_id", "gameweek", "expected_points", "p_start", "std_dev"}
    assert len(predictions) == len(features)
    assert predictions["expected_points"].notna().all()
    assert predictions["std_dev"].notna().all()
    assert predictions["p_start"].between(0, 1).all()
