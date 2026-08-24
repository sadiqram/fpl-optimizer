"""Extracted from `cli._cmd_train`. Global/shared — model artifacts aren't per-tenant."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import joblib

from fpl_optimizer.models import training_data
from fpl_optimizer.models.baseline import NaivePredictor, PoissonPredictor
from fpl_optimizer.models.ensemble import EnsemblePredictor

DEFAULT_MODELS_DIR = Path("data/artifacts/models")


def run_train(
    conn: sqlite3.Connection, season: str, train_start: int, train_end: int,
    test_start: int, test_end: int, save: bool,
) -> dict:
    train_features, train_targets = training_data.build_training_set(conn, season, range(train_start, train_end + 1))
    test_features, test_targets = training_data.build_training_set(conn, season, range(test_start, test_end + 1))

    if train_features.empty or test_features.empty:
        raise ValueError("Not enough data to train/evaluate — check the season and gameweek ranges.")

    ensemble = EnsemblePredictor()
    mae_by_model = {}
    for name, predictor in [("naive", NaivePredictor()), ("poisson", PoissonPredictor()), ("gbm_ensemble", ensemble)]:
        predictor.fit(train_features, train_targets)
        predictions = predictor.predict(test_features)
        merged = predictions.merge(test_targets[["player_id", "gameweek", "total_points"]], on=["player_id", "gameweek"])
        mae_by_model[name] = float((merged["expected_points"] - merged["total_points"]).abs().mean())

    saved_path = None
    if save:
        DEFAULT_MODELS_DIR.mkdir(parents=True, exist_ok=True)
        model_path = DEFAULT_MODELS_DIR / f"gbm_ensemble_{season}.joblib"
        joblib.dump(ensemble, model_path)
        saved_path = str(model_path)

    return {
        "train_rows": len(train_features),
        "test_rows": len(test_features),
        "mae_by_model": mae_by_model,
        "saved_path": saved_path,
    }
