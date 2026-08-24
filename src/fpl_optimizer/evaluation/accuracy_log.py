"""The running accuracy-log CSV (Architecture §4.8) — extracted from `cli._append_accuracy_log`
so the API's `/accuracy-log` endpoint and the CLI's `evaluate` command share one read/write
path instead of the API re-parsing the CSV format independently.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ACCURACY_LOG_PATH = Path("data/artifacts/evaluation/accuracy_log.csv")
_KEY = ["season", "gameweek", "model_version"]


def append_accuracy_log(rows: list[dict], path: Path = ACCURACY_LOG_PATH) -> None:
    """Upserts by (season, gameweek, model_version) — re-evaluating the same one updates its
    row in place rather than duplicating."""
    path.parent.mkdir(parents=True, exist_ok=True)
    new_rows = pd.DataFrame(rows)
    if path.exists():
        existing = pd.read_csv(path)
        existing = existing[~existing.set_index(_KEY).index.isin(new_rows.set_index(_KEY).index)]
        combined = pd.concat([existing, new_rows], ignore_index=True)
    else:
        combined = new_rows
    combined.sort_values(_KEY).to_csv(path, index=False)


def read_accuracy_log(path: Path = ACCURACY_LOG_PATH) -> pd.DataFrame:
    """Empty (correctly-columned) DataFrame if nothing's been logged yet, rather than
    raising — mirrors the project's NFR2 degrade-gracefully convention for a page that has
    nothing to show yet, not an error state."""
    if not path.exists():
        return pd.DataFrame(columns=[*_KEY, "evaluated_at", "n", "mae", "rmse"])
    return pd.read_csv(path)
