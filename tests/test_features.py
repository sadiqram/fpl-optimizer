"""Feature-layer leakage tests — Architecture §5's #2 testing priority: assert feature
builders cannot see data after the boundary they're given. This is the failure mode that's
silent and expensive if it slips through (Architecture §4.3).
"""

import sqlite3

import pytest

from fpl_optimizer.features import build, fixtures, form, minutes
from fpl_optimizer.storage import db

SEASON = "2025-26"


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    db.init_db(connection)

    db.upsert_teams(connection, [
        {"id": 1, "name": "Arsenal", "short_name": "ARS"},
        {"id": 2, "name": "Chelsea", "short_name": "CHE"},
    ])
    db.upsert_element_types(connection, [{"id": 4, "singular_name_short": "FWD", "singular_name": "Forward"}])
    db.upsert_players(connection, [
        {"id": 101, "code": 101, "first_name": "Test", "second_name": "Striker", "web_name": "Striker", "team": 1, "element_type": 4},
    ], updated_at="2025-08-01T00:00:00Z")

    # GW1: modest game. GW2: monster haul. A GW3 feature build must see only GW1-2 outcomes.
    db.insert_player_gw_stats(connection, [
        {"player_id": 101, "gameweek": 1, "minutes": 90, "total_points": 2, "goals_scored": 0,
         "assists": 0, "clean_sheets": 0, "goals_conceded": 1, "bonus": 0, "bps": 10,
         "expected_goals": 0.1, "expected_assists": 0.0},
        {"player_id": 101, "gameweek": 2, "minutes": 90, "total_points": 20, "goals_scored": 4,
         "assists": 0, "clean_sheets": 0, "goals_conceded": 1, "bonus": 3, "bps": 90,
         "expected_goals": 3.0, "expected_assists": 0.0},
        {"player_id": 101, "gameweek": 3, "minutes": 90, "total_points": 1, "goals_scored": 0,
         "assists": 0, "clean_sheets": 0, "goals_conceded": 2, "bonus": 0, "bps": 5,
         "expected_goals": 0.0, "expected_assists": 0.0},
    ], source="fpl_api", season=SEASON)

    db.insert_fixtures(connection, [
        {"id": 1, "event": 1, "kickoff_time": "2025-08-16T14:00:00Z", "team_h": 1, "team_a": 2,
         "team_h_difficulty": 3, "team_a_difficulty": 3, "finished": True, "team_h_score": 1, "team_a_score": 1},
        {"id": 2, "event": 2, "kickoff_time": "2025-08-23T14:00:00Z", "team_h": 2, "team_a": 1,
         "team_h_difficulty": 3, "team_a_difficulty": 3, "finished": True, "team_h_score": 1, "team_a_score": 4},
        {"id": 3, "event": 3, "kickoff_time": "2025-08-30T14:00:00Z", "team_h": 1, "team_a": 2,
         "team_h_difficulty": 4, "team_a_difficulty": 2, "finished": True, "team_h_score": 0, "team_a_score": 2},
    ], season=SEASON)

    yield connection
    connection.close()


def test_form_excludes_target_gameweeks_own_outcome(conn):
    """Building form for GW3 must not see GW3's 1-point dud or GW2's 20-point haul as 'this
    gameweek' — only gameweeks strictly before the target are fair game."""
    gw3_features = form.build(conn, SEASON, gameweek=3)
    row = gw3_features[gw3_features.player_id == 101].iloc[0]
    assert row["points_mean_5"] == pytest.approx((2 + 20) / 2)  # GW1 + GW2 only

    gw2_features = form.build(conn, SEASON, gameweek=2)
    row2 = gw2_features[gw2_features.player_id == 101].iloc[0]
    assert row2["points_mean_5"] == pytest.approx(2)  # GW1 only — GW2's own haul must not appear


def test_minutes_excludes_target_gameweeks_own_outcome(conn):
    gw3 = minutes.build(conn, as_of_date="2025-08-29", season=SEASON, gameweek=3)
    row = gw3[gw3.player_id == 101].iloc[0]
    assert row["appearances_5"] == 2  # GW1, GW2 — not GW3


def test_fixtures_team_form_excludes_target_gameweeks_result(conn):
    """Team rolling goals-for must reflect GW1-2 results only when building for GW3, even
    though GW3's result (0-2) is already sitting in the fixtures table."""
    gw3 = fixtures.build(conn, SEASON, gameweek=3)
    row = gw3[gw3.player_id == 101].iloc[0]
    # Arsenal (team 1) scored 1 (GW1, home) and 4 (GW2, away) before GW3 — GW3's 0 must not count.
    assert row["goals_for_avg"] == pytest.approx((1 + 4) / 2)


def test_assemble_features_stable_schema_regardless_of_history(conn):
    """A brand-new season (no gw_stats at all) must still produce every expected column,
    all-NaN rather than silently absent — a missing column is a schema-stability trap for
    anything downstream expecting a fixed shape."""
    empty_conn = sqlite3.connect(":memory:")
    empty_conn.row_factory = sqlite3.Row
    db.init_db(empty_conn)
    db.upsert_teams(empty_conn, [{"id": 1, "name": "Arsenal", "short_name": "ARS"},
                                  {"id": 2, "name": "Chelsea", "short_name": "CHE"}])
    db.upsert_element_types(empty_conn, [{"id": 4, "singular_name_short": "FWD", "singular_name": "Forward"}])
    db.upsert_players(empty_conn, [
        {"id": 101, "code": 101, "first_name": "Test", "second_name": "Striker", "web_name": "Striker", "team": 1, "element_type": 4},
    ], updated_at="2025-08-01T00:00:00Z")
    db.insert_player_snapshots(empty_conn, [
        {"code": 101, "now_cost": 80, "selected_by_percent": "10.0", "status": "a",
         "chance_of_playing_this_round": None, "chance_of_playing_next_round": None, "news": ""},
    ], snapshot_date="2025-08-01", fetched_at="2025-08-01T00:00:00Z")

    with_history_cols = set(form.build(conn, SEASON, gameweek=3).columns)
    no_history_cols = set(form.build(empty_conn, SEASON, gameweek=1).columns)
    assert with_history_cols == no_history_cols

    df = build.assemble_features(empty_conn, as_of_date="2025-08-01", season=SEASON, gameweek=1)
    assert len(df) == 1
    assert df.iloc[0]["points_mean_5"] is None or pd_isna(df.iloc[0]["points_mean_5"])
    empty_conn.close()


def test_fixtures_team_form_all_nan_columns_are_float_dtype_not_object(conn):
    """Regression: a season with no finished fixtures yet (e.g. GW1, pre-season) used to
    return goals_for_avg/goals_against_avg/clean_sheet_rate as object-dtype all-None
    columns rather than float64 all-NaN — `.fillna(...)` doesn't fix the dtype, and
    np.exp() (PoissonPredictor's clean-sheet-probability calc) raises outright on an
    object-dtype array rather than broadcasting."""
    empty_conn = sqlite3.connect(":memory:")
    empty_conn.row_factory = sqlite3.Row
    db.init_db(empty_conn)
    db.upsert_teams(empty_conn, [{"id": 1, "name": "Arsenal", "short_name": "ARS"}])

    team_form = fixtures._team_rolling_stats(empty_conn, SEASON, gameweek=1)
    assert team_form.empty
    for col in ["goals_for_avg", "goals_against_avg", "clean_sheet_rate"]:
        assert team_form[col].dtype == "float64"
    empty_conn.close()


def pd_isna(value) -> bool:
    import pandas as pd
    return pd.isna(value)
