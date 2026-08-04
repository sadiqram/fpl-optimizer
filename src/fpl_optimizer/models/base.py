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
