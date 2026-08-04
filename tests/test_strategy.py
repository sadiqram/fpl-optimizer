"""Strategy layer (M6): owned-squad state, transfer optimization, rolling horizon."""

import sqlite3

import pandas as pd
import pytest

from fpl_optimizer.optimize import squad
from fpl_optimizer.storage import db
from fpl_optimizer.strategy import squad_state, transfers


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


def _synthetic_pool(n_per_position=10, n_teams=6) -> pd.DataFrame:
    """Same shape/spirit as test_optimize.py's helper of the same name — a cheap, plentiful
    pool spanning several teams with a smooth expected_points gradient, so the solver has
    real choices rather than being forced by scarcity alone."""
    rows = []
    player_id = 1
    for element_type, count in [(1, n_per_position), (2, n_per_position), (3, n_per_position), (4, n_per_position)]:
        for i in range(count):
            rows.append({
                "player_id": player_id,
                "element_type": element_type,
                "team_id": (player_id % n_teams) + 1,
                "now_cost": 40 + (i * 5),
                "expected_points": 2.0 + (i * 0.3),
            })
            player_id += 1
    return pd.DataFrame(rows)


def _owned_from(pool: pd.DataFrame, squad_df: pd.DataFrame) -> pd.DataFrame:
    """Owned squad with selling_price == now_cost (no price movement since purchase) —
    isolates the tests below to transfer *decisions*, not selling-price arithmetic, except
    where a test deliberately perturbs it."""
    owned = squad_df[["player_id"]].merge(pool[["player_id", "now_cost"]], on="player_id")
    return owned.rename(columns={"now_cost": "selling_price"})


def test_optimize_transfers_holds_when_nothing_has_changed():
    """The owned squad is already this pool's optimum — re-solving with the identical pool
    and identical prices must reproduce it exactly: 0 transfers, not a same-value swap."""
    pool = _synthetic_pool()
    initial_squad = squad.build_squad(pool)
    owned = _owned_from(pool, initial_squad)

    result = transfers.optimize_transfers(pool, owned, bank=0, free_transfers=1)
    assert result["transfers_in"] == []
    assert result["transfers_out"] == []
    assert result["hits_taken"] == 0
    assert result["expected_points_gain"] == pytest.approx(0.0)


def test_optimize_transfers_takes_a_clear_free_upgrade():
    pool = _synthetic_pool()
    initial_squad = squad.build_squad(pool)
    owned = _owned_from(pool, initial_squad)

    # A player outside the squad becomes a huge upgrade, priced the same as the worst
    # owned player of the same position -> an affordable, obviously-worth-it swap.
    worst_owned = initial_squad.sort_values("expected_points").iloc[0]
    candidate_id = pool[
        (~pool.player_id.isin(owned.player_id)) & (pool.element_type == worst_owned["element_type"])
    ].iloc[0]["player_id"]
    pool = pool.copy()
    pool.loc[pool.player_id == candidate_id, "expected_points"] = worst_owned["expected_points"] + 20
    pool.loc[pool.player_id == candidate_id, "now_cost"] = worst_owned["now_cost"]

    result = transfers.optimize_transfers(pool, owned, bank=0, free_transfers=1)
    assert result["transfers_in"] == [candidate_id]
    assert int(worst_owned["player_id"]) in result["transfers_out"]
    assert result["hits_taken"] == 0
    assert result["expected_points_gain"] > 15


def test_optimize_transfers_takes_a_hit_only_when_the_gain_clears_it():
    pool = _synthetic_pool()
    initial_squad = squad.build_squad(pool)
    owned = _owned_from(pool, initial_squad)

    worst_owned = initial_squad.sort_values("expected_points").iloc[0]
    candidate_id = pool[
        (~pool.player_id.isin(owned.player_id)) & (pool.element_type == worst_owned["element_type"])
    ].iloc[0]["player_id"]
    pool = pool.copy()
    pool.loc[pool.player_id == candidate_id, "now_cost"] = worst_owned["now_cost"]

    # +2 points doesn't clear a 4-point hit with 0 free transfers -> hold.
    pool.loc[pool.player_id == candidate_id, "expected_points"] = worst_owned["expected_points"] + 2
    held = transfers.optimize_transfers(pool, owned, bank=0, free_transfers=0)
    assert held["transfers_in"] == []
    assert held["hits_taken"] == 0

    # +10 points clears it -> the hit is worth taking.
    pool.loc[pool.player_id == candidate_id, "expected_points"] = worst_owned["expected_points"] + 10
    upgraded = transfers.optimize_transfers(pool, owned, bank=0, free_transfers=0)
    assert upgraded["transfers_in"] == [candidate_id]
    assert upgraded["hits_taken"] == 1
    assert upgraded["hit_cost"] == 4
    assert upgraded["expected_points_gain"] > 0


def test_optimize_transfers_respects_selling_price_not_now_cost():
    """An owned player whose price has *risen* only recovers half the rise on sale — using
    now_cost instead of the true (lower) selling price would let the optimizer believe it
    has more budget than it actually does."""
    pool = _synthetic_pool()
    initial_squad = squad.build_squad(pool)
    owned = _owned_from(pool, initial_squad)

    # This owned player's price has risen sharply since purchase; true selling price is only
    # purchase + half the rise, well below current now_cost.
    target = owned.iloc[0]
    purchase_price = int(pool.loc[pool.player_id == target["player_id"], "now_cost"].iloc[0])
    risen_now_cost = purchase_price + 40
    pool = pool.copy()
    pool.loc[pool.player_id == target["player_id"], "now_cost"] = risen_now_cost
    true_selling_price = purchase_price + (risen_now_cost - purchase_price) // 2
    owned = owned.copy()
    owned.loc[owned.player_id == target["player_id"], "selling_price"] = true_selling_price

    total_budget = 0 + int(owned["selling_price"].sum())
    result = transfers.optimize_transfers(pool, owned, bank=0, free_transfers=1)

    # new_squad already carries the internally-computed `price` column (selling_price for
    # kept-owned players, now_cost for newly-bought ones) — this is the actual constraint
    # the solver enforced, not a re-derivation that could mask a bug in the original one.
    assert result["new_squad"]["price"].sum() <= total_budget
