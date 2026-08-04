"""Assembles multi-gameweek (features, targets) pairs for fitting a Predictor.

Not part of Architecture's original file list — added because Predictor.fit(features,
targets) needs a caller to build those DataFrames across many historical gameweeks first,
and that's model-training support, not something a single feature builder or the DB layer
should own.
"""

from __future__ import annotations

import pandas as pd

from fpl_optimizer.features import build as feature_build
from fpl_optimizer.storage import db


def _season_as_of_date(conn, season: str) -> str:
    """Archive seasons carry exactly one price/status snapshot, dated at that season's
    latest known fixture (archive_loader.bootstrap_season) — every historical training row
    resolves to that same snapshot regardless of which gameweek it's for. This mirrors that
    logic directly rather than guessing a date and hoping it lands after the snapshot."""
    row = conn.execute("SELECT MAX(kickoff_time) FROM fixtures WHERE season = ?", (season,)).fetchone()
    return row[0][:10] if row and row[0] else f"{season.split('-')[0]}-08-01"


def build_training_set(conn, season: str, gameweeks: range) -> tuple[pd.DataFrame, pd.DataFrame]:
    """One row per (player, gameweek) in `gameweeks` with both a leakage-safe feature row
    (as of that gameweek — never sees that gameweek's own outcome) and the actual outcome
    as the label. Rows with no recorded outcome that gameweek (didn't play, or the gameweek
    hasn't happened) are dropped — there's nothing to train on without a real label.
    """
    as_of_date = _season_as_of_date(conn, season)
    target_cols = ["minutes", "total_points"]

    combined_frames = []
    for gameweek in gameweeks:
        features = feature_build.assemble_features(conn, as_of_date, season, gameweek)
        outcome_rows = db.get_player_gw_stats_for_gameweek(conn, season, gameweek)
        if not outcome_rows:
            continue
        outcomes = pd.DataFrame([dict(r) for r in outcome_rows])[["player_id"] + target_cols]

        # A single inner merge per gameweek — features and targets come from the same
        # frame from here on, so they can never drift out of row-alignment with each other
        # the way two independently filtered/concatenated lists could.
        combined_frames.append(features.merge(outcomes, on="player_id", how="inner"))

    if not combined_frames:
        return pd.DataFrame(), pd.DataFrame()

    combined = pd.concat(combined_frames, ignore_index=True)
    targets_df = combined[["player_id", "gameweek"] + target_cols]
    features_df = combined.drop(columns=target_cols)
    return features_df, targets_df
