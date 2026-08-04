"""Strategy layer (M6): owned-squad state, transfer optimization, rolling horizon."""

import sqlite3

import pandas as pd
import pytest

from fpl_optimizer.clock import FixedClock
from fpl_optimizer.features import build as feature_build
from fpl_optimizer.models.baseline import NaivePredictor
from fpl_optimizer.optimize import squad
from fpl_optimizer.storage import db
from fpl_optimizer.strategy import horizon, risk, squad_state, transfers


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


@pytest.fixture
def season_conn():
    """A real (season, gameweek) history — squad.build_squad needs genuine position/team
    variety to have real choices, and plan_horizon needs actual fixtures/gw_stats to build
    features from, so a raw synthetic pool (as above) isn't enough here. Same shape as
    test_evaluation.py's `conn` fixture."""
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
    db.insert_player_gw_stats(connection, [
        {"player_id": p["id"], "gameweek": 1, "minutes": 90, "total_points": p["id"] % 8,
         "goals_scored": 0, "assists": 0, "clean_sheets": 0, "goals_conceded": 1, "bonus": 0, "bps": 10,
         "expected_goals": 0.1, "expected_assists": 0.0} for p in players
    ], source="fpl_api", season="2025-26")
    db.insert_player_gw_stats(connection, [
        {"player_id": p["id"], "gameweek": 2, "minutes": 90, "total_points": (p["id"] * 3) % 10,
         "goals_scored": 0, "assists": 0, "clean_sheets": 0, "goals_conceded": 1, "bonus": 0, "bps": 10,
         "expected_goals": 0.1, "expected_assists": 0.0} for p in players
    ], source="fpl_api", season="2025-26")

    yield connection
    connection.close()


def _week3_usable(conn) -> pd.DataFrame:
    predictor = NaivePredictor()
    predictor.fit(pd.DataFrame())
    features_df = feature_build.assemble_features(conn, "2025-08-25", "2025-26", 3)
    predictions = predictor.predict(features_df)
    merged = predictions.merge(
        features_df[["player_id", "element_type", "team_id", "now_cost"]], on="player_id", how="left"
    )
    return merged.dropna(subset=["element_type", "team_id", "now_cost", "expected_points"])


def test_plan_horizon_holds_when_owned_squad_is_already_the_windows_optimum(season_conn):
    usable = _week3_usable(season_conn)
    initial_squad = squad.build_squad(usable)
    owned = initial_squad[["player_id", "now_cost"]].rename(columns={"now_cost": "selling_price"})

    predictor = NaivePredictor()
    predictor.fit(pd.DataFrame())
    result = horizon.plan_horizon(
        season_conn, FixedClock("2025-08-25"), "2025-26", gameweek=3, predictor=predictor,
        owned_squad=owned, bank=0, free_transfers=1, decay=[1.0],
    )

    # Single-week decay ([1.0]) means the horizon score is exactly this week's own
    # prediction — identical to what built `initial_squad` directly, so nothing should move.
    assert result["transfer_result"]["transfers_in"] == []
    assert result["transfer_result"]["transfers_out"] == []
    assert len(result["lineup"]["starting_xi"]) == 11
    assert len(result["lineup"]["bench"]) == 4


def test_plan_horizon_weekly_predictions_span_the_full_decay_window(season_conn):
    usable = _week3_usable(season_conn)
    initial_squad = squad.build_squad(usable)
    owned = initial_squad[["player_id", "now_cost"]].rename(columns={"now_cost": "selling_price"})

    predictor = NaivePredictor()
    predictor.fit(pd.DataFrame())
    result = horizon.plan_horizon(
        season_conn, FixedClock("2025-08-25"), "2025-26", gameweek=3, predictor=predictor,
        owned_squad=owned, bank=0, free_transfers=1, decay=[1.0, 0.5, 0.25],
    )

    assert [wp["gameweek"] for wp in result["weekly_predictions"]] == [3, 4, 5]
    # Week 1 (GW3) must be the actual undecayed prediction the lineup/captain get scored on.
    week1 = result["weekly_predictions"][0]["predictions"].set_index("player_id")["expected_points"]
    direct = usable.set_index("player_id")["expected_points"]
    pd.testing.assert_series_equal(week1.sort_index(), direct.sort_index(), check_names=False)


def test_apply_risk_adjustment_balanced_preset_is_a_no_op():
    pool = pd.DataFrame({
        "player_id": [1, 2], "expected_points": [5.0, 3.0], "std_dev": [2.0, 1.0],
        "selected_by_percent": [50.0, 5.0], "now_cost": [80, 45],
    })
    adjusted = risk.apply_risk_adjustment(pool, risk.BALANCED_PRESET)
    pd.testing.assert_series_equal(adjusted["expected_points"], pool["expected_points"])


def test_apply_risk_adjustment_risk_rewards_or_penalizes_variance():
    pool = pd.DataFrame({
        "player_id": [1], "expected_points": [5.0], "std_dev": [2.0],
        "selected_by_percent": [50.0], "now_cost": [80],
    })
    aggressive = risk.apply_risk_adjustment(pool, {"risk": 0.5, "variance_penalty": 0, "ownership_bonus": 0, "value_bonus": 0})
    safe = risk.apply_risk_adjustment(pool, {"risk": -0.5, "variance_penalty": 0, "ownership_bonus": 0, "value_bonus": 0})
    assert aggressive["expected_points"].iloc[0] == pytest.approx(5.0 + 0.5 * 2.0)
    assert safe["expected_points"].iloc[0] == pytest.approx(5.0 - 0.5 * 2.0)


def test_apply_risk_adjustment_ownership_bonus_favors_low_ownership():
    pool = pd.DataFrame({
        "player_id": [1, 2], "expected_points": [5.0, 5.0], "std_dev": [1.0, 1.0],
        "selected_by_percent": [90.0, 5.0], "now_cost": [80, 80],
    })
    adjusted = risk.apply_risk_adjustment(pool, {"risk": 0, "variance_penalty": 0, "ownership_bonus": 1.0, "value_bonus": 0})
    high_owned, low_owned = adjusted.set_index("player_id")["expected_points"]
    assert low_owned > high_owned  # the 5%-owned player gets more of the bonus than the 90%-owned one


def test_apply_risk_adjustment_degrades_gracefully_without_ownership_data():
    """NFR2: a pool missing selected_by_percent entirely shouldn't crash or NaN out
    expected_points — the ownership term just contributes nothing."""
    pool = pd.DataFrame({"player_id": [1], "expected_points": [5.0], "std_dev": [1.0]})
    adjusted = risk.apply_risk_adjustment(pool, {"risk": 0, "variance_penalty": 0, "ownership_bonus": 1.0, "value_bonus": 0})
    assert adjusted["expected_points"].iloc[0] == pytest.approx(5.0)


def test_resolve_preset_matches_config_and_rejects_unknown_names():
    balanced = risk.resolve_preset("balanced")
    assert balanced == risk.BALANCED_PRESET
    assert risk.default_preset_name() == "balanced"
    with pytest.raises(ValueError):
        risk.resolve_preset("not_a_real_preset")


def test_manual_risk_writer_returns_the_same_value_for_any_gameweek():
    writer = risk.ManualRiskWriter(0.5)
    assert writer.get(1) == 0.5
    assert writer.get(38) == 0.5
