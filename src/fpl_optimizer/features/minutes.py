"""Minutes family: start rate, sub patterns, injury flag, chance-of-playing % — largely
categorical, and the single biggest source of catastrophic error (Architecture §4.4):
a predicted haul from a player who doesn't start is a total loss.
"""

from __future__ import annotations

import pandas as pd

from fpl_optimizer.storage import db

STARTER_MINUTES_THRESHOLD = 60


def build(conn, as_of_date: str, season: str, gameweek: int, window: int = 5) -> pd.DataFrame:
    """One row per active player (Architecture §4.3: pure function, (as_of_date, gameweek)
    -> DataFrame, no DB writes).

    - Rolling minutes/start-rate come from player_gw_stats strictly before `gameweek`
      (db.get_player_gw_stats_before enforces that boundary at the data-access layer).
    - chance_of_playing/status come from the point-in-time snapshot as-of `as_of_date`.

    Missing history (new season, no appearances in the window) yields NaN, not 0 — 0 would
    silently claim "definitely didn't play" instead of "no data yet".
    """
    active_players = db.get_active_players(conn, season)
    out = pd.DataFrame({"player_id": active_players}).set_index("player_id")

    # Columns are declared up front and always present, even with zero history (e.g. a
    # brand-new season) — a column silently disappearing instead of being all-NaN would be
    # a schema-stability trap for anything downstream expecting a fixed column set.
    gw_cols = ["appearances_5", "minutes_avg_5", "start_rate_5"]
    gw_rows = db.get_player_gw_stats_before(conn, season, gameweek)
    gw_df = pd.DataFrame([dict(r) for r in gw_rows]) if gw_rows else pd.DataFrame(columns=["player_id", "gameweek", "minutes"])
    recent = gw_df[gw_df["gameweek"] >= gameweek - window]
    if not recent.empty:
        agg = recent.groupby("player_id").agg(
            appearances_5=("gameweek", "count"),
            minutes_avg_5=("minutes", "mean"),
            starts_5=("minutes", lambda s: int((s >= STARTER_MINUTES_THRESHOLD).sum())),
        )
        agg["start_rate_5"] = agg["starts_5"] / agg["appearances_5"]
        out = out.join(agg[gw_cols])
    else:
        out = out.reindex(columns=out.columns.tolist() + gw_cols)

    snap_cols = ["status", "chance_of_playing_this_round", "chance_of_playing_next_round", "now_cost"]
    snapshot_rows = db.get_player_snapshots_as_of(conn, as_of_date)
    if snapshot_rows:
        snap_df = pd.DataFrame([dict(r) for r in snapshot_rows]).set_index("player_id")
        out = out.join(snap_df.reindex(columns=snap_cols))
    else:
        out = out.reindex(columns=out.columns.tolist() + snap_cols)

    return out.reset_index()
