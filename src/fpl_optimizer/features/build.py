"""Assembles feature families into per (player, gameweek) rows; materializes to
data/artifacts/features/{as_of}/ as Parquet.

Also owns the feature-trust registry (outcome-derived vs. state-at-deadline), read by the
evaluation layer to label backtest conclusions trusted vs. provisional (Architecture §4.2,
PRD §6a.4).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from fpl_optimizer.features import fixtures as fixtures_features
from fpl_optimizer.features import form as form_features
from fpl_optimizer.features import minutes as minutes_features

DEFAULT_ARTIFACTS_DIR = Path("data/artifacts/features")

# Feature column -> trust class (PRD §6a.4). Read by evaluation/metrics.py to label which
# backtest conclusions are trusted now vs. provisional until enough own snapshots
# accumulate to measure archive-vs-snapshot divergence on the volatile fields.
FEATURE_TRUST = {
    # form.py, and fixtures.py's team-form half: derived from completed-match outcomes —
    # facts that don't change retroactively, trusted from any source.
    "points_mean": "outcome_derived",
    "points_decayed": "outcome_derived",
    "minutes_mean": "outcome_derived",
    "xgi_mean": "outcome_derived",
    "goals_for_avg": "outcome_derived",
    "goals_against_avg": "outcome_derived",
    "clean_sheet_rate": "outcome_derived",
    "appearances_5": "outcome_derived",
    "start_rate_5": "outcome_derived",
    "minutes_avg_5": "outcome_derived",
    # minutes.py's snapshot half: revised, point-in-time state — archive-sourced values are
    # only approximate (Architecture §4.2).
    "status": "state_at_deadline",
    "chance_of_playing_this_round": "state_at_deadline",
    "chance_of_playing_next_round": "state_at_deadline",
}


def assemble_features(conn, as_of_date: str, season: str, gameweek: int) -> pd.DataFrame:
    """(as_of_date, gameweek) -> DataFrame, one row per active player (Architecture §4.3).
    Pure function: reads via the as-of-safe db helpers below, never writes to the DB."""
    minutes_df = minutes_features.build(conn, as_of_date, season, gameweek)
    form_df = form_features.build(conn, season, gameweek)
    fixtures_df = fixtures_features.build(conn, season, gameweek)

    merged = minutes_df.merge(form_df, on="player_id", how="outer").merge(fixtures_df, on="player_id", how="outer")
    merged.insert(1, "season", season)
    merged.insert(2, "gameweek", gameweek)
    return merged


def materialize_features(df: pd.DataFrame, as_of_date: str, artifacts_dir: Path = DEFAULT_ARTIFACTS_DIR) -> Path:
    """Feature building is the slowest step in the pipeline and reruns constantly during
    model iteration (Architecture §4.3) — caching to Parquet turns a multi-minute loop into
    seconds. Parquet over CSV for type preservation and columnar reads."""
    out_dir = artifacts_dir / as_of_date
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "features.parquet"
    df.to_parquet(path, index=False)
    return path
