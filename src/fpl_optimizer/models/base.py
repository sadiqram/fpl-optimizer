"""Predictor protocol: every model — baseline or learned — implements this same contract,
which is what lets them be swapped by config and compared apples-to-apples (Architecture
§4.4)."""

from __future__ import annotations

from typing import Protocol

import pandas as pd


class Predictor(Protocol):
    def fit(self, features: pd.DataFrame, targets: pd.DataFrame | None = None) -> None: ...

    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        """-> columns: player_id, gameweek, expected_points, p_start, std_dev.

        std_dev is not optional — downstream, captaincy and chip decisions care about
        variance, not just the mean (Architecture §4.4): cheap to produce here, expensive
        to retrofit once the optimizer already assumes it exists.
        """
        ...


# Shared by minutes_model.py and points_model.py, which both need to turn an assembled
# features DataFrame into a plain numeric matrix for sklearn. Lives here rather than in
# either module so neither has to import a "private" helper from the other.
NON_FEATURE_COLUMNS = {"player_id", "season", "gameweek", "status"}


def feature_matrix(features: pd.DataFrame) -> pd.DataFrame:
    """Drops identity columns (not signal) and `status` (text category — the
    chance_of_playing_* columns already carry that information numerically). team_id/
    element_type are left as plain integers rather than declared categorical — a
    simplification (Architecture P4: simplicity over scale at this data size)."""
    return features[[c for c in features.columns if c not in NON_FEATURE_COLUMNS]]


def usable_columns(x: pd.DataFrame, min_distinct: int = 2) -> list[str]:
    """Columns with fewer than `min_distinct` non-null distinct values in the *training*
    subset break HistGradientBoosting's binning step outright (a real sklearn behavior —
    verified against actual data, not a hypothetical): an entirely-NaN column raises
    `ValueError: window shape cannot be larger than input array shape`. Happens whenever a
    data source has near-zero coverage for whatever's being trained on (here: Understat
    coverage for an archived season, effectively zero) — will recur for any thin data
    source, not just this one. Drop them before fitting; there's no signal in an empty
    column for the model to learn from anyway."""
    return [c for c in x.columns if x[c].dropna().nunique() >= min_distinct]
