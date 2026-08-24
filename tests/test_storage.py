"""Storage layer: schema init, upserts, and the as-of query boundary (Architecture §4.2-4.3)."""

import json
import sqlite3

import pytest

from fpl_optimizer.storage import db


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    db.init_db(connection)
    yield connection
    connection.close()


def _seed_one_player(conn):
    db.upsert_teams(conn, [{"id": 1, "name": "Arsenal", "short_name": "ARS"}])
    db.upsert_element_types(conn, [{"id": 3, "singular_name_short": "MID", "singular_name": "Midfielder"}])
    db.upsert_players(
        conn,
        [{"id": 1, "code": 123, "first_name": "Bukayo", "second_name": "Saka", "web_name": "Saka", "team": 1, "element_type": 3}],
        updated_at="2026-08-01T00:00:00Z",
    )


def test_upsert_players_is_idempotent(conn):
    _seed_one_player(conn)
    _seed_one_player(conn)
    assert conn.execute("SELECT COUNT(*) FROM players").fetchone()[0] == 1


def test_player_snapshot_roundtrip(conn):
    _seed_one_player(conn)
    db.insert_player_snapshots(
        conn,
        [{"code": 123, "now_cost": 100, "selected_by_percent": "45.2", "status": "a",
          "chance_of_playing_this_round": None, "chance_of_playing_next_round": None, "news": ""}],
        snapshot_date="2026-08-01",
        fetched_at="2026-08-01T12:00:00Z",
    )
    row = db.get_player_snapshots_as_of(conn, "2026-08-01")[0]
    assert row["now_cost"] == 100
    assert row["selected_by_percent"] == pytest.approx(45.2)


def test_as_of_excludes_future_snapshots(conn):
    """The core leakage guard: a query as-of an early date must not see a later snapshot."""
    _seed_one_player(conn)
    db.insert_player_snapshots(
        conn,
        [{"code": 123, "now_cost": 100, "selected_by_percent": None, "status": "a",
          "chance_of_playing_this_round": None, "chance_of_playing_next_round": None, "news": ""}],
        snapshot_date="2026-08-01",
        fetched_at="2026-08-01T12:00:00Z",
    )
    db.insert_player_snapshots(
        conn,
        [{"code": 123, "now_cost": 999, "selected_by_percent": None, "status": "i",
          "chance_of_playing_this_round": 0, "chance_of_playing_next_round": 0, "news": "Injured"}],
        snapshot_date="2026-08-10",
        fetched_at="2026-08-10T12:00:00Z",
    )

    as_of_early = db.get_player_snapshots_as_of(conn, "2026-08-05")
    assert len(as_of_early) == 1
    assert as_of_early[0]["now_cost"] == 100
    assert as_of_early[0]["status"] == "a"

    as_of_late = db.get_player_snapshots_as_of(conn, "2026-08-10")
    assert as_of_late[0]["now_cost"] == 999


def test_as_of_date_is_required(conn):
    with pytest.raises(TypeError):
        db.get_player_snapshots_as_of(conn)


def test_players_keyed_by_code_not_reused_season_element_id(conn):
    """Regression guard: FPL's element id is reassigned every season (verified empirically —
    Salah was element id 191/233/308/328 across four seasons on the same code). Two distinct
    players who happen to share an element_id across different ingests must stay distinct."""
    db.upsert_teams(conn, [{"id": 1, "name": "Arsenal", "short_name": "ARS"}])
    db.upsert_element_types(conn, [{"id": 3, "singular_name_short": "MID", "singular_name": "Midfielder"}])
    db.upsert_players(
        conn,
        [{"id": 191, "code": 118748, "first_name": "Mohamed", "second_name": "Salah", "web_name": "Salah", "team": 1, "element_type": 3}],
        updated_at="2019-08-01T00:00:00Z",
    )
    db.upsert_players(
        conn,
        [{"id": 191, "code": 999999, "first_name": "Some", "second_name": "OtherPlayer", "web_name": "Other", "team": 1, "element_type": 3}],
        updated_at="2026-08-01T00:00:00Z",
    )
    rows = conn.execute("SELECT id, second_name FROM players ORDER BY id").fetchall()
    assert len(rows) == 2
    assert {r["second_name"] for r in rows} == {"Salah", "OtherPlayer"}


def test_insert_predictions_upserts_same_run(conn):
    _seed_one_player(conn)
    db.insert_predictions(
        conn, [{"player_id": 123, "expected_points": 5.0, "p_start": 0.9, "std_dev": 2.0}],
        model_version="poisson", season="2025-26", gameweek=3, run_date="2025-09-01",
    )
    db.insert_predictions(
        conn, [{"player_id": 123, "expected_points": 6.5, "p_start": 0.95, "std_dev": 2.1}],
        model_version="poisson", season="2025-26", gameweek=3, run_date="2025-09-01",
    )
    rows = conn.execute("SELECT * FROM predictions").fetchall()
    assert len(rows) == 1  # same (model_version, season, player_id, gameweek, run_date) -> update, not duplicate
    assert rows[0]["expected_points"] == pytest.approx(6.5)


def test_get_latest_predictions_picks_most_recent_run_date(conn):
    _seed_one_player(conn)
    db.insert_predictions(
        conn, [{"player_id": 123, "expected_points": 4.0, "p_start": 0.8, "std_dev": 2.0}],
        model_version="poisson", season="2025-26", gameweek=3, run_date="2025-08-28",
    )
    db.insert_predictions(
        conn, [{"player_id": 123, "expected_points": 7.0, "p_start": 0.9, "std_dev": 2.0}],
        model_version="poisson", season="2025-26", gameweek=3, run_date="2025-09-01",
    )
    latest = db.get_latest_predictions(conn, "2025-26", 3)
    assert len(latest) == 1
    assert latest[0]["expected_points"] == pytest.approx(7.0)
    assert latest[0]["run_date"] == "2025-09-01"


def test_get_latest_predictions_filters_by_model_version(conn):
    _seed_one_player(conn)
    db.insert_predictions(
        conn, [{"player_id": 123, "expected_points": 4.0, "p_start": 0.8, "std_dev": 2.0}],
        model_version="naive", season="2025-26", gameweek=3, run_date="2025-09-01",
    )
    db.insert_predictions(
        conn, [{"player_id": 123, "expected_points": 7.0, "p_start": 0.9, "std_dev": 2.0}],
        model_version="poisson", season="2025-26", gameweek=3, run_date="2025-09-01",
    )
    assert len(db.get_latest_predictions(conn, "2025-26", 3)) == 2
    only_naive = db.get_latest_predictions(conn, "2025-26", 3, model_version="naive")
    assert len(only_naive) == 1
    assert only_naive[0]["model_version"] == "naive"


def test_insert_recommendation_roundtrip(conn):
    user_id = db.create_user(conn, email="a@example.com", password_hash="x", created_at="2025-09-01T00:00:00Z")
    payload = {"squad_expected_points": 65.5, "captain": {"player_id": 1}}
    db.insert_recommendation(conn, "run-1", user_id=user_id, created_at="2025-09-01T12:00:00Z", season="2025-26", gameweek=3, payload=payload)
    row = conn.execute("SELECT * FROM recommendations WHERE run_id = 'run-1'").fetchone()
    assert row["season"] == "2025-26"
    assert json.loads(row["payload"]) == payload
    assert db.get_recommendation(conn, "run-1")["run_id"] == "run-1"
    assert [r["run_id"] for r in db.list_recommendations(conn, user_id)] == ["run-1"]


def test_user_crud(conn):
    user_id = db.create_user(conn, email="Test@Example.com", password_hash="hash", created_at="2025-09-01T00:00:00Z")
    assert db.get_user_by_email(conn, "test@example.com")["id"] == user_id
    assert db.get_user_by_id(conn, user_id)["email"] == "test@example.com"
    db.update_user_fpl_team_id(conn, user_id, 2786467)
    assert db.get_user_by_id(conn, user_id)["fpl_team_id"] == 2786467


def test_insert_event_live_stats_maps_element_id_and_skips_unmapped(conn):
    _seed_one_player(conn)  # players.id (code) = 123, element_id = 1 (the "id" field passed to upsert_players)
    elements = [
        {"id": 1, "stats": {
            "minutes": 90, "total_points": 8, "goals_scored": 1, "assists": 0, "clean_sheets": 1,
            "goals_conceded": 0, "bonus": 2, "bps": 30, "expected_goals": "0.45", "expected_assists": "None",
        }},
        {"id": 999999, "stats": {"minutes": 90, "total_points": 3}},  # no matching player -> skipped
    ]
    n = db.insert_event_live_stats(conn, elements, season="2025-26", gameweek=3)
    assert n == 1
    row = conn.execute("SELECT * FROM player_gw_stats WHERE player_id = 123 AND gameweek = 3").fetchone()
    assert row["total_points"] == 8
    assert row["expected_goals"] == pytest.approx(0.45)
    assert row["expected_assists"] is None
    assert row["source"] == "fpl_api"


def test_archive_upsert_never_clobbers_live_element_id(conn):
    """Regression guard: a historical season's element id must never overwrite the current
    live element_id — is_live=False must force element_id to NULL in the write regardless
    of what the archive row's own 'id' column says."""
    db.upsert_teams(conn, [{"id": 1, "name": "Arsenal", "short_name": "ARS"}])
    db.upsert_element_types(conn, [{"id": 3, "singular_name_short": "MID", "singular_name": "Midfielder"}])
    db.upsert_players(
        conn,
        [{"id": 328, "code": 118748, "first_name": "Mohamed", "second_name": "Salah", "web_name": "Salah", "team": 1, "element_type": 3}],
        updated_at="2026-08-01T00:00:00Z",
        is_live=True,
    )
    db.upsert_players(
        conn,
        [{"id": 191, "code": 118748, "first_name": "Mohamed", "second_name": "Salah", "web_name": "Salah", "team": 1, "element_type": 3}],
        updated_at="archive:vaastav:2019-20",
        is_live=False,
    )
    row = conn.execute("SELECT element_id FROM players WHERE id = 118748").fetchone()
    assert row["element_id"] == 328
