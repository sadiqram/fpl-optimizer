"""Transfer optimization (M6, FR3): given an owned squad and a pool of predictions, decide
which players (if any) to sell and buy.

Reframed as a budget-reallocation MILP rather than a bespoke mechanism (see the M6 plan's
"Key design decisions"): every player gets a `price` — their *selling* price if owned, else
market `now_cost` — against a total budget of `bank + sum(selling price over the whole owned
squad)`. That makes this structurally the same constrained-selection problem
`optimize/squad.py::build_squad` already solves (same `optimize/constraints.py` rules), with
one addition: an auxiliary `hits` variable penalizes transfers beyond the free ones, so the
solver lands on 0 transfers by itself whenever nothing clears the hit threshold — "hold" is
what the objective picks, not a special-cased branch (FR3).
"""

from __future__ import annotations

import pandas as pd
import pulp

from fpl_optimizer.optimize import constraints


def optimize_transfers(
    pool: pd.DataFrame,
    owned_squad: pd.DataFrame,
    bank: int,
    free_transfers: int,
    hit_cost: int = constraints.HIT_COST,
) -> dict:
    """`pool`: player_id, element_type, team_id, now_cost, expected_points — the full
    prediction universe for the target gameweek (same shape `recommend_gameweek` already
    produces; M6c's rolling horizon feeds this the decay-weighted, horizon-summed points).
    `owned_squad`: player_id, selling_price — exactly the 15 currently-owned players.
    Every owned player must have a row in `pool`; one missing a prediction is implicitly
    sold (dropped, not fabricated — NFR2, same convention `recommend_gameweek` already uses
    for missing price/position/prediction data).

    Returns {new_squad, transfers_in, transfers_out, hits_taken, hit_cost,
    expected_points_gain} — expected_points_gain is net of the hit cost, versus simply
    holding the current squad, so a caller can present "hold" when it's <= 0.
    """
    owned_ids = set(owned_squad["player_id"])
    total_budget = bank + int(owned_squad["selling_price"].sum())

    priced = pool.merge(owned_squad[["player_id", "selling_price"]], on="player_id", how="left")
    priced["price"] = priced["selling_price"].fillna(priced["now_cost"])

    prob = pulp.LpProblem("fpl_transfers", pulp.LpMaximize)
    rows = priced.to_dict("records")
    pick = {r["player_id"]: pulp.LpVariable(f"pick_{r['player_id']}", cat="Binary") for r in rows}
    # Continuous, not Integer: transfers/hits are already forced to integers by pick[] being
    # binary, and an LP relaxation here solves faster with no correctness cost — the same
    # trick as any "hits >= shortfall, hits >= 0" penalty term.
    hits = pulp.LpVariable("hits", lowBound=0)

    prob += pulp.lpSum(pick[r["player_id"]] * r["expected_points"] for r in rows) - hit_cost * hits

    prob += pulp.lpSum(pick[r["player_id"]] for r in rows) == constraints.SQUAD_SIZE
    prob += pulp.lpSum(pick[r["player_id"]] * r["price"] for r in rows) <= total_budget

    for team_id in priced["team_id"].unique():
        team_rows = [r for r in rows if r["team_id"] == team_id]
        prob += pulp.lpSum(pick[r["player_id"]] for r in team_rows) <= constraints.MAX_PER_TEAM

    for element_type, (min_count, max_count) in constraints.SQUAD_POSITION_COUNTS.items():
        pos_rows = [r for r in rows if r["element_type"] == element_type]
        prob += pulp.lpSum(pick[r["player_id"]] for r in pos_rows) >= min_count
        prob += pulp.lpSum(pick[r["player_id"]] for r in pos_rows) <= max_count

    owned_kept = pulp.lpSum(pick[pid] for pid in owned_ids if pid in pick)
    players_sold = len(owned_ids) - owned_kept
    prob += hits >= players_sold - free_transfers

    status = prob.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError(f"Transfer optimization did not find an optimal solution: {pulp.LpStatus[status]}")

    selected_ids = {r["player_id"] for r in rows if pick[r["player_id"]].value() == 1}
    new_squad = priced[priced.player_id.isin(selected_ids)].reset_index(drop=True)
    transfers_out = sorted(owned_ids - selected_ids)
    transfers_in = sorted(selected_ids - owned_ids)

    # Recomputed directly from the result rather than trusting the LP-relaxed `hits`
    # variable's solved float value — it's the same number by construction, but this is
    # the version a caller should actually trust (no floating-point solver noise).
    hits_taken = max(0, len(transfers_out) - free_transfers)
    hit_cost_total = hit_cost * hits_taken

    current_points = priced[priced.player_id.isin(owned_ids)]["expected_points"].sum()
    expected_points_gain = float(new_squad["expected_points"].sum() - current_points - hit_cost_total)

    return {
        "new_squad": new_squad,
        "transfers_in": transfers_in,
        "transfers_out": transfers_out,
        "hits_taken": hits_taken,
        "hit_cost": hit_cost_total,
        "expected_points_gain": expected_points_gain,
    }
