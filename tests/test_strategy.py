"""Strategy layer (M6): owned-squad state, transfer optimization, rolling horizon."""

import sqlite3

import pytest

from fpl_optimizer.storage import db
from fpl_optimizer.strategy import squad_state


def test_compute_free_transfers_accrues_one_per_unused_gameweek():
    # GW1 (ignored), GW2-4 with 0 transfers made each -> 1 (baseline) + 3 = 4 available for GW5.
    history = [
        {"event": 1, "event_transfers": 0},
        {"event": 2, "event_transfers": 0},
        {"event": 3, "event_transfers": 0},
        {"event": 4, "event_transfers": 0},
    ]
    assert squad_state.compute_free_transfers(history, chips_played=[]) == 4


def test_compute_free_transfers_caps_at_five():
    history = [{"event": e, "event_transfers": 0} for e in range(1, 10)]
    assert squad_state.compute_free_transfers(history, chips_played=[], cap=5) == 5


def test_compute_free_transfers_consumes_banked_transfers():
    # GW2: 0 made (banked -> 2). GW3: uses both banked -> 0 left, +1 for next week -> 1.
    history = [
        {"event": 1, "event_transfers": 0},
        {"event": 2, "event_transfers": 0},
        {"event": 3, "event_transfers": 2},
    ]
    assert squad_state.compute_free_transfers(history, chips_played=[]) == 1


def test_compute_free_transfers_hit_does_not_go_negative():
    # GW2: available=1, but 3 transfers made (2 were hits) -> used=min(3,1)=1, next=1-1+1=1.
    history = [
        {"event": 1, "event_transfers": 0},
        {"event": 2, "event_transfers": 3},
    ]
    assert squad_state.compute_free_transfers(history, chips_played=[]) == 1


def test_compute_free_transfers_wildcard_week_is_skipped_not_zero_transfers():
    # GW2: wildcard played (any event_transfers value ignored, banked count untouched).
    # GW3: 0 made as normal -> still just baseline (1) + 1 (GW3) = 2, GW2 contributes nothing.
    history = [
        {"event": 1, "event_transfers": 0},
        {"event": 2, "event_transfers": 12},
        {"event": 3, "event_transfers": 0},
    ]
    chips = [{"name": "wildcard", "event": 2}]
    assert squad_state.compute_free_transfers(history, chips_played=chips) == 2


def test_resolve_chips_available_excludes_used_chips():
    chips_played = [{"name": "wildcard", "event": 5}]
    available = squad_state.resolve_chips_available(chips_played)
    assert "wildcard" not in available
    assert set(available) == {"freehit", "bboost", "3xc"}


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    db.init_db(connection)
    db.upsert_teams(connection, [{"id": 1, "name": "Arsenal", "short_name": "ARS"}])
    db.upsert_element_types(connection, [{"id": 3, "singular_name_short": "MID", "singular_name": "Midfielder"}])
    db.upsert_players(connection, [
        {"id": 1, "code": 123, "first_name": "Bukayo", "second_name": "Saka", "web_name": "Saka", "team": 1, "element_type": 3},
        {"id": 2, "code": 456, "first_name": "Martin", "second_name": "Odegaard", "web_name": "Odegaard", "team": 1, "element_type": 3},
    ], updated_at="2026-08-01T00:00:00Z")
    yield connection
    connection.close()


def test_resolve_purchase_price_uses_most_recent_transfer_in(conn):
    db.insert_squad_transfers(conn, [
        {"event": 3, "element_in": 123, "element_in_cost": 80, "element_out": 456, "element_out_cost": 55, "time": "2025-09-01T18:00:00Z"},
        {"event": 8, "element_in": 123, "element_in_cost": 95, "element_out": 456, "element_out_cost": 55, "time": "2025-10-15T18:00:00Z"},
    ])
    # Sold and re-bought later at a different price -> the later purchase is what counts.
    assert squad_state.resolve_purchase_price(conn, player_id=123, season="2025-26") == 95


def test_resolve_purchase_price_falls_back_to_earliest_own_snapshot_for_opening_15(conn):
    db.insert_player_snapshots(conn, [
        {"code": 123, "now_cost": 75, "selected_by_percent": "10.0", "status": "a",
         "chance_of_playing_this_round": None, "chance_of_playing_next_round": None, "news": ""},
    ], snapshot_date="2025-08-01", fetched_at="2025-08-01T00:00:00Z")
    db.insert_player_snapshots(conn, [
        {"code": 123, "now_cost": 82, "selected_by_percent": "10.0", "status": "a",
         "chance_of_playing_this_round": None, "chance_of_playing_next_round": None, "news": ""},
    ], snapshot_date="2025-09-01", fetched_at="2025-09-01T00:00:00Z")
    # Never transferred in -> earliest snapshot (75), not the later one (82).
    assert squad_state.resolve_purchase_price(conn, player_id=123, season="2025-26") == 75


def test_resolve_purchase_price_none_when_neither_source_has_data(conn):
    assert squad_state.resolve_purchase_price(conn, player_id=999, season="2025-26") is None
