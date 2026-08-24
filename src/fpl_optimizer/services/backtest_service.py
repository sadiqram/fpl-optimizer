"""Extracted from `cli._cmd_backtest`. Global/shared, same reasoning as train_service."""

from __future__ import annotations

import sqlite3

import pandas as pd

from fpl_optimizer.evaluation import backtest
from fpl_optimizer.models import training_data
from fpl_optimizer.models.baseline import NaivePredictor, PoissonPredictor
from fpl_optimizer.models.ensemble import EnsemblePredictor

PREDICTORS = {"naive": NaivePredictor, "poisson": PoissonPredictor}


def run_backtest(conn: sqlite3.Connection, season: str, start_gameweek: int, end_gameweek: int, model: str) -> pd.DataFrame:
    if model == "gbm":
        # Train only on gameweeks strictly before the backtest window — training on data
        # that overlaps it would leak the backtest's own future into itself.
        train_features, train_targets = training_data.build_training_set(conn, season, range(2, start_gameweek))
        predictor = EnsemblePredictor()
        predictor.fit(train_features, train_targets)
    else:
        predictor = PREDICTORS[model]()
        predictor.fit(pd.DataFrame())  # stateless baseline; fit() is a documented no-op

    return backtest.backtest_season(conn, season, start_gameweek, end_gameweek, predictor)
