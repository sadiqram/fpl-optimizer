"""Fixture family: opponent strength, home/away, congestion — plus Team context: team
xG/xGA trend, expected clean-sheet probability (Architecture §4.3). Both operate at the
team/fixture level, computed directly from the fixtures table rather than a separately
populated team_stats table — fixtures already has team_h_score/team_a_score, so no
intermediate aggregation table is needed for this.
"""

from __future__ import annotations

import pandas as pd

from fpl_optimizer.storage import db

TEAM_FORM_WINDOW = 5


def _team_rolling_stats(conn, season: str, gameweek: int, window: int = TEAM_FORM_WINDOW) -> pd.DataFrame:
    """Per team: rolling goals-for/against and clean-sheet rate over its last `window`
    played fixtures strictly before `gameweek` (db.get_fixtures_before enforces that)."""
    past = [dict(f) for f in db.get_fixtures_before(conn, season, gameweek) if f["finished"]]
    if not past:
        # dtype=... explicit, not just columns=[...] (which defaults to object dtype): an
        # object-dtype all-NaN column survives .fillna() as object, and numpy ufuncs like
        # np.exp() raise on an object array rather than broadcasting (PoissonPredictor hits
        # this on goals_against_avg for a season with no fixture history yet, e.g. GW1).
        return pd.DataFrame({
            "team_id": pd.Series(dtype="int64"),
            "goals_for_avg": pd.Series(dtype="float64"),
            "goals_against_avg": pd.Series(dtype="float64"),
            "clean_sheet_rate": pd.Series(dtype="float64"),
        })

    rows = []
    for f in past:
        rows.append({"team_id": f["team_h"], "gameweek": f["event"], "for": f["team_h_score"], "against": f["team_a_score"]})
        rows.append({"team_id": f["team_a"], "gameweek": f["event"], "for": f["team_a_score"], "against": f["team_h_score"]})
    long_df = pd.DataFrame(rows)

    def _recent(group):
        recent = group.sort_values("gameweek").tail(window)
        return pd.Series({
            "goals_for_avg": recent["for"].mean(),
            "goals_against_avg": recent["against"].mean(),
            "clean_sheet_rate": (recent["against"] == 0).mean(),
        })

    return long_df.groupby("team_id").apply(_recent, include_groups=False).reset_index()


def build(conn, season: str, gameweek: int) -> pd.DataFrame:
    """One row per active player: their team's upcoming fixture(s) for `gameweek`
    (opponent difficulty, home/away, DGW/blank via num_fixtures), plus rolling team
    attacking/defensive form from fixtures strictly before `gameweek`.

    Fixture *schedules* are known well in advance and are not a leakage risk the way
    outcomes are — only past scorelines are boundary-checked here (via
    db.get_fixtures_before), same principle as form.py.
    """
    active_players = db.get_active_players(conn, season)
    out = pd.DataFrame({"player_id": active_players}).set_index("player_id")
    if not active_players:
        return out.reset_index()

    placeholders = ",".join(["?"] * len(active_players))
    player_rows = conn.execute(
        f"SELECT id AS player_id, team_id, element_type FROM players WHERE id IN ({placeholders})",
        active_players,
    ).fetchall()
    out = out.join(pd.DataFrame([dict(r) for r in player_rows]).set_index("player_id"))

    # Columns below are always present, even for a fully blank gameweek or a season with no
    # fixture history yet (e.g. GW1) — all-NaN rather than silently absent, so downstream
    # consumers can rely on a fixed schema (same reasoning as form.py, minutes.py).
    this_gw_fixtures = [dict(f) for f in db.get_fixtures_for_gameweek(conn, season, gameweek)]
    fixture_rows = [
        {"team_id": f["team_h"], "is_home": 1, "opponent_difficulty": f["team_h_difficulty"]}
        for f in this_gw_fixtures
    ] + [
        {"team_id": f["team_a"], "is_home": 0, "opponent_difficulty": f["team_a_difficulty"]}
        for f in this_gw_fixtures
    ]
    fixture_df = pd.DataFrame(fixture_rows, columns=["team_id", "is_home", "opponent_difficulty"])
    team_gw = fixture_df.groupby("team_id").agg(
        num_fixtures=("team_id", "count"),
        opponent_difficulty_avg=("opponent_difficulty", "mean"),
        is_home_share=("is_home", "mean"),
    )
    out = out.join(team_gw, on="team_id")

    team_form = _team_rolling_stats(conn, season, gameweek).set_index("team_id")
    out = out.join(team_form, on="team_id")

    return out.reset_index()
