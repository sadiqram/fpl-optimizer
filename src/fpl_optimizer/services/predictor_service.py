"""Predictor loading, shared by the CLI and the API. Extracted from `cli._load_predictor`
so the API can turn a missing-model error into a 400 instead of `_load_predictor`'s
`SystemExit` — raising SystemExit from inside an ASGI route handler would take the whole
uvicorn worker down with it, not just fail the one request.
"""

from __future__ import annotations

from pathlib import Path

import joblib
import pandas as pd

from fpl_optimizer.models.baseline import NaivePredictor, PoissonPredictor

PREDICTORS = {"naive": NaivePredictor, "poisson": PoissonPredictor}
MODEL_CHOICES = [*sorted(PREDICTORS), "gbm"]
DEFAULT_MODELS_DIR = Path("data/artifacts/models")


def load_predictor(model: str, season: str):
    """Baselines are stateless (fit() is a documented no-op); gbm loads a joblib model
    already trained and saved by `train --save`/`POST /train {save: true}` — this never
    trains inline, training is a separate lifecycle stage (strategy.horizon's own
    docstring). Raises FileNotFoundError if a gbm model hasn't been trained yet."""
    if model == "gbm":
        model_path = DEFAULT_MODELS_DIR / f"gbm_ensemble_{season}.joblib"
        if not model_path.exists():
            raise FileNotFoundError(
                f"No saved GBM model for {season} at {model_path} — train and save one first."
            )
        return joblib.load(model_path)
    predictor = PREDICTORS[model]()
    predictor.fit(pd.DataFrame())
    return predictor
