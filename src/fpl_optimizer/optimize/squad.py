"""MILP: best XV given predictions + budget (Architecture §4.5).

Fresh-squad/wildcard scope — maximizes expected points over any legal 15 subject to
budget/formation/team-limit constraints. Transfer-in/out logic against an existing owned
squad (selling price, hit thresholds) is the strategy layer's job, not this module's.
"""

from __future__ import annotations

import pandas as pd
import pulp

from fpl_optimizer.optimize import constraints


def build_squad(players: pd.DataFrame, budget: int = constraints.BUDGET) -> pd.DataFrame:
    """`players` must have columns: player_id, element_type, team_id, now_cost,
    expected_points. Returns the selected 15-row subset, unordered.

    MILP over greedy/genetic: the squad problem is naturally linear, and this size solves
    in well under a second — there's no reason to accept an approximate answer when an
    exact one is free (Architecture §4.5).
    """
    prob = pulp.LpProblem("fpl_squad", pulp.LpMaximize)
    rows = players.to_dict("records")
    pick = {r["player_id"]: pulp.LpVariable(f"pick_{r['player_id']}", cat="Binary") for r in rows}

    prob += pulp.lpSum(pick[r["player_id"]] * r["expected_points"] for r in rows)

    prob += pulp.lpSum(pick[r["player_id"]] for r in rows) == constraints.SQUAD_SIZE
    prob += pulp.lpSum(pick[r["player_id"]] * r["now_cost"] for r in rows) <= budget

    for team_id in players["team_id"].unique():
        team_rows = [r for r in rows if r["team_id"] == team_id]
        prob += pulp.lpSum(pick[r["player_id"]] for r in team_rows) <= constraints.MAX_PER_TEAM

    for element_type, (min_count, max_count) in constraints.SQUAD_POSITION_COUNTS.items():
        pos_rows = [r for r in rows if r["element_type"] == element_type]
        prob += pulp.lpSum(pick[r["player_id"]] for r in pos_rows) >= min_count
        prob += pulp.lpSum(pick[r["player_id"]] for r in pos_rows) <= max_count

    status = prob.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError(f"Squad optimization did not find an optimal solution: {pulp.LpStatus[status]}")

    selected_ids = {r["player_id"] for r in rows if pick[r["player_id"]].value() == 1}
    return players[players["player_id"].isin(selected_ids)].reset_index(drop=True)
