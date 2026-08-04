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
