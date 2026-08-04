"""Form family: rolling 3/5/10-GW points, xGI, minutes — decayed weighting (Architecture §4.3)."""

from __future__ import annotations

import pandas as pd

from fpl_optimizer.storage import db

WINDOWS = (3, 5, 10)
DECAY_RATE = 0.85  # per gameweek back from the target; most recent gameweek weighted highest


def _decayed_mean(group: pd.DataFrame, value_col: str, gameweek: int) -> float:
    weights = DECAY_RATE ** (gameweek - group["gameweek"])
    return float((group[value_col] * weights).sum() / weights.sum())


def build(conn, season: str, gameweek: int) -> pd.DataFrame:
    """One row per active player. For each window in WINDOWS: the simple mean and a
    recency-decayed mean of points and minutes, plus xGI (xG + xA) where Understat data
    exists, over gameweeks strictly before `gameweek`
    (db.get_player_gw_stats_before/get_understat_player_gw_before enforce that boundary —
    this function never queries the current or a future gameweek's outcomes).
    """
    active_players = db.get_active_players(conn, season)
    out = pd.DataFrame({"player_id": active_players}).set_index("player_id")

    # Empty-but-correctly-shaped frames when there's no history yet (e.g. a brand-new
    # season) — the per-window columns below must always exist, all-NaN rather than
    # silently absent, so downstream consumers can rely on a fixed schema.
    gw_rows = db.get_player_gw_stats_before(conn, season, gameweek)
    gw_df = pd.DataFrame([dict(r) for r in gw_rows]) if gw_rows else pd.DataFrame(columns=["player_id", "gameweek", "total_points", "minutes"])

    understat_rows = db.get_understat_player_gw_before(conn, season, gameweek)
    xgi_df = pd.DataFrame([dict(r) for r in understat_rows]) if understat_rows else pd.DataFrame(columns=["player_id", "gameweek", "xg", "xa"])
    xgi_df["xgi"] = (xgi_df["xg"] + xgi_df["xa"]) if not xgi_df.empty else pd.Series(dtype=float)

    for window in WINDOWS:
        cutoff = gameweek - window
        recent = gw_df[gw_df["gameweek"] >= cutoff]
        recent_xgi = xgi_df[xgi_df["gameweek"] >= cutoff]

        out[f"points_mean_{window}"] = recent.groupby("player_id")["total_points"].mean()
        # ddof=0: population std over whatever games are in the window, not a sample-std
        # estimate of some larger population — a 2-appearance window shouldn't return NaN.
        out[f"points_std_{window}"] = recent.groupby("player_id")["total_points"].std(ddof=0)
        out[f"points_decayed_{window}"] = (
            recent.groupby("player_id").apply(lambda g: _decayed_mean(g, "total_points", gameweek), include_groups=False)
            if not recent.empty else pd.Series(dtype=float)
        )
        out[f"minutes_mean_{window}"] = recent.groupby("player_id")["minutes"].mean()
        out[f"xgi_mean_{window}"] = recent_xgi.groupby("player_id")["xgi"].mean()
        out[f"xg_mean_{window}"] = recent_xgi.groupby("player_id")["xg"].mean()
        out[f"xa_mean_{window}"] = recent_xgi.groupby("player_id")["xa"].mean()

    return out.reset_index()
