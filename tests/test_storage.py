"""Storage layer: schema init, upserts, and the as-of query boundary (Architecture §4.2-4.3)."""

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
        [{"id": 1, "now_cost": 100, "selected_by_percent": "45.2", "status": "a",
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
        [{"id": 1, "now_cost": 100, "selected_by_percent": None, "status": "a",
          "chance_of_playing_this_round": None, "chance_of_playing_next_round": None, "news": ""}],
        snapshot_date="2026-08-01",
        fetched_at="2026-08-01T12:00:00Z",
    )
    db.insert_player_snapshots(
        conn,
        [{"id": 1, "now_cost": 999, "selected_by_percent": None, "status": "i",
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
