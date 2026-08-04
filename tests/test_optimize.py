"""Constraint tests — Architecture §5's #1 testing priority: every FPL rule has a test
asserting invalid squads are rejected. Bugs here produce recommendations that are simply
illegal, not just suboptimal.
"""

import pandas as pd
import pytest

from fpl_optimizer.optimize import constraints, lineup, squad


def _synthetic_pool(n_per_position=10, n_teams=6) -> pd.DataFrame:
    """A cheap, plentiful player pool spanning several teams and a wide expected_points
    range, so the solver has real choices to make rather than being forced into one
    solution by scarcity alone."""
    rows = []
    player_id = 1
    for element_type, count in [(1, n_per_position), (2, n_per_position), (3, n_per_position), (4, n_per_position)]:
        for i in range(count):
            rows.append({
                "player_id": player_id,
                "element_type": element_type,
                "team_id": (player_id % n_teams) + 1,
                "now_cost": 40 + (i * 5),  # 4.0m to ~8.5m
                "expected_points": 2.0 + (i * 0.3),
            })
            player_id += 1
    return pd.DataFrame(rows)


def test_squad_has_correct_size_and_position_counts():
    pool = _synthetic_pool()
    result = squad.build_squad(pool)
    assert len(result) == constraints.SQUAD_SIZE
    counts = result["element_type"].value_counts().to_dict()
    for element_type, (min_count, max_count) in constraints.SQUAD_POSITION_COUNTS.items():
        assert min_count <= counts.get(element_type, 0) <= max_count


def test_squad_respects_budget():
    # 800 is comfortably above the true minimum-cost feasible squad (735, verified against
    # this pool's team/position constraints) but well below what an unconstrained-by-budget
    # solve would spend — tight enough to bind, not so tight it's infeasible by construction.
    pool = _synthetic_pool()
    result = squad.build_squad(pool, budget=800)
    assert result["now_cost"].sum() <= 800


def test_squad_respects_max_per_team():
    pool = _synthetic_pool()  # default 6 teams — enough slack for 15 players at max 3/team
    result = squad.build_squad(pool)
    assert (result["team_id"].value_counts() <= constraints.MAX_PER_TEAM).all()


def test_squad_maximizes_expected_points_within_constraints():
    """Sanity check the objective actually does something: a pool with one dominant
    cheap player must include them."""
    pool = _synthetic_pool()
    pool.loc[pool.element_type == 4, "expected_points"] = 1.0
    pool.iloc[-1, pool.columns.get_loc("expected_points")] = 50.0  # one standout forward
    standout_id = pool.iloc[-1]["player_id"]
    result = squad.build_squad(pool)
    assert standout_id in result["player_id"].values


def test_infeasible_budget_raises_rather_than_returning_illegal_squad():
    pool = _synthetic_pool()
    with pytest.raises(RuntimeError):
        squad.build_squad(pool, budget=1)  # far below any legal 15-player combination


def test_lineup_is_valid_formation_with_captain_in_starting_xi():
    pool = _synthetic_pool()
    squad_df = squad.build_squad(pool)
    picked = lineup.build_lineup(squad_df)

    assert len(picked["starting_xi"]) == constraints.XI_SIZE
    assert len(picked["bench"]) == constraints.SQUAD_SIZE - constraints.XI_SIZE

    pos_of = dict(zip(squad_df.player_id, squad_df.element_type))
    xi_counts = pd.Series([pos_of[p] for p in picked["starting_xi"]]).value_counts().to_dict()
    for element_type, min_count in constraints.XI_POSITION_MIN.items():
        assert xi_counts.get(element_type, 0) >= min_count
    for element_type, max_count in constraints.XI_POSITION_MAX.items():
        assert xi_counts.get(element_type, 0) <= max_count
    assert xi_counts.get(1, 0) == 1  # exactly one starting goalkeeper

    assert picked["captain"] in picked["starting_xi"]
    assert picked["vice_captain"] in picked["starting_xi"]
    assert picked["captain"] != picked["vice_captain"]


def test_lineup_picks_highest_scorer_as_captain():
    pool = _synthetic_pool()
    squad_df = squad.build_squad(pool)
    picked = lineup.build_lineup(squad_df)
    best_in_squad = squad_df.sort_values("expected_points", ascending=False).iloc[0]["player_id"]
    # The best overall scorer in the squad is always formation-eligible to start (removing
    # them never fixes an infeasible formation), so they must be the captain.
    assert picked["captain"] == best_in_squad
