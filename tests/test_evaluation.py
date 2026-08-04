"""Backtest harness tests: same code path as live (Architecture P2), scored against known
synthetic outcomes so the regret/MAE arithmetic itself can be checked by hand.
"""

import sqlite3

import pandas as pd
import pytest

from fpl_optimizer.clock import FixedClock, SystemClock
from fpl_optimizer.evaluation import backtest, metrics
from fpl_optimizer.models.baseline import NaivePredictor
from fpl_optimizer.storage import db


def test_fixed_clock_returns_constant_date():
    the_clock = FixedClock("2024-12-25")
    assert the_clock.today() == "2024-12-25"
    assert the_clock.today() == "2024-12-25"  # calling twice doesn't advance it


def test_system_clock_returns_iso_date_format():
    date_str = SystemClock().today()
    assert len(date_str) == 10 and date_str[4] == "-" and date_str[7] == "-"


def test_mae_rmse_by_position():
    predictions = pd.DataFrame({"player_id": [1, 2, 3], "element_type": [3, 3, 4], "expected_points": [5.0, 3.0, 2.0]})
    actuals = pd.DataFrame({"player_id": [1, 2, 3], "total_points": [7.0, 3.0, 6.0]})
    result = metrics.mae_rmse_by_position(predictions, actuals).set_index("element_type")
    assert result.loc[3, "mae"] == pytest.approx((2.0 + 0.0) / 2)
    assert result.loc[4, "mae"] == pytest.approx(4.0)
    assert result.loc[3, "n"] == 2


def test_overall_mae_rmse():
    predictions = pd.DataFrame({"player_id": [1, 2, 3], "expected_points": [5.0, 3.0, 2.0]})
    actuals = pd.DataFrame({"player_id": [1, 2, 3], "total_points": [7.0, 3.0, 6.0]})
    result = metrics.overall_mae_rmse(predictions, actuals)
    assert result["n"] == 3
    assert result["mae"] == pytest.approx((2.0 + 0.0 + 4.0) / 3)
    assert result["rmse"] == pytest.approx(((2.0**2 + 0.0**2 + 4.0**2) / 3) ** 0.5)


def test_squad_selection_regret():
    assert metrics.squad_selection_regret(recommended_squad_actual_points=50, hindsight_squad_actual_points=65) == 15
    assert metrics.squad_selection_regret(recommended_squad_actual_points=50, hindsight_squad_actual_points=50) == 0


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    db.init_db(connection)

    db.upsert_teams(connection, [{"id": t, "name": f"Team{t}", "short_name": f"T{t}"} for t in range(1, 7)])
    db.upsert_element_types(connection, [
        {"id": 1, "singular_name_short": "GKP", "singular_name": "Goalkeeper"},
        {"id": 2, "singular_name_short": "DEF", "singular_name": "Defender"},
        {"id": 3, "singular_name_short": "MID", "singular_name": "Midfielder"},
        {"id": 4, "singular_name_short": "FWD", "singular_name": "Forward"},
    ])

    players = [
        {"id": i, "code": i, "first_name": f"P{i}", "second_name": f"P{i}", "web_name": f"P{i}",
         "team": (i % 6) + 1, "element_type": [1, 2, 3, 4][i % 4]}
        for i in range(1, 41)
    ]
    db.upsert_players(connection, players, updated_at="2025-08-01T00:00:00Z")
    db.insert_player_snapshots(connection, [
        {"code": p["id"], "now_cost": 45 + (p["id"] % 10) * 5, "selected_by_percent": "10.0", "status": "a",
         "chance_of_playing_this_round": None, "chance_of_playing_next_round": None, "news": ""}
        for p in players
    ], snapshot_date="2025-08-01", fetched_at="2025-08-01T00:00:00Z")

    db.insert_fixtures(connection, [
        {"id": 1, "event": 1, "kickoff_time": "2025-08-16T14:00:00Z", "team_h": 1, "team_a": 2,
         "team_h_difficulty": 3, "team_a_difficulty": 3, "finished": True, "team_h_score": 1, "team_a_score": 1},
        {"id": 2, "event": 2, "kickoff_time": "2025-08-23T14:00:00Z", "team_h": 3, "team_a": 4,
         "team_h_difficulty": 3, "team_a_difficulty": 3, "finished": True, "team_h_score": 2, "team_a_score": 0},
    ], season="2025-26")

    # GW1 and GW2 give every player a differentiated, non-constant score by two different
    # formulas — a backtest of GW2 must reflect GW2's own numbers, not GW1's.
    def _gw_rows(gameweek, points_fn):
        return [{"player_id": p["id"], "gameweek": gameweek, "minutes": 90, "total_points": points_fn(p["id"]),
                  "goals_scored": 0, "assists": 0, "clean_sheets": 0, "goals_conceded": 1, "bonus": 0, "bps": 10,
                  "expected_goals": 0.1, "expected_assists": 0.0} for p in players]

    db.insert_player_gw_stats(connection, _gw_rows(1, lambda i: i % 8), source="fpl_api", season="2025-26")
    db.insert_player_gw_stats(connection, _gw_rows(2, lambda i: (i * 3) % 10), source="fpl_api", season="2025-26")

    yield connection
    connection.close()


def test_backtest_season_scores_the_requested_gameweek(conn):
    predictor = NaivePredictor()
    predictor.fit(pd.DataFrame())
    result = backtest.backtest_season(conn, "2025-26", start_gameweek=2, end_gameweek=2, predictor=predictor)

    assert len(result) == 1
    row = result.iloc[0]
    assert row["gameweek"] == 2
    assert row["n_predictions_scored"] > 0
    # The hindsight squad is chosen by actual results under the same constraints as the
    # recommended one, so by construction it can never score worse.
    assert row["hindsight_squad_points"] >= row["recommended_squad_points"]
    assert row["squad_regret"] >= 0


def test_backtest_season_skips_gameweeks_with_no_recorded_outcome(conn):
    predictor = NaivePredictor()
    predictor.fit(pd.DataFrame())
    result = backtest.backtest_season(conn, "2025-26", start_gameweek=2, end_gameweek=5, predictor=predictor)
    # GW3-5 have no player_gw_stats rows in this fixture — skipped, not zero-filled.
    assert set(result["gameweek"]) == {2}
