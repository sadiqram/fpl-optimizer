"""Starting XI, bench order, captain/vice from a chosen squad (Architecture §4.5)."""

from __future__ import annotations

import pandas as pd
import pulp

from fpl_optimizer.optimize import constraints


def build_lineup(squad: pd.DataFrame) -> dict:
    """`squad` must have columns: player_id, element_type, expected_points (the 15-row
    output of optimize.squad.build_squad, or equivalent).

    Bench order is sorted by descending expected_points as a simple heuristic proxy for
    "most likely to be worth subbing in" — not the fuller probabilistic weighting
    Architecture §4.5 describes as the eventual target (bench points only matter if a
    starter blanks). Documented simplification, not a hidden one; a genuine bench-order
    objective needs p_start propagated through, which is straightforward to add later
    without changing this function's interface.
    """
    prob = pulp.LpProblem("fpl_lineup", pulp.LpMaximize)
    rows = squad.to_dict("records")
    start = {r["player_id"]: pulp.LpVariable(f"start_{r['player_id']}", cat="Binary") for r in rows}

    prob += pulp.lpSum(start[r["player_id"]] * r["expected_points"] for r in rows)
    prob += pulp.lpSum(start[r["player_id"]] for r in rows) == constraints.XI_SIZE

    for element_type in constraints.XI_POSITION_MIN:
        pos_rows = [r for r in rows if r["element_type"] == element_type]
        prob += pulp.lpSum(start[r["player_id"]] for r in pos_rows) >= constraints.XI_POSITION_MIN[element_type]
        prob += pulp.lpSum(start[r["player_id"]] for r in pos_rows) <= constraints.XI_POSITION_MAX[element_type]

    status = prob.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError(f"Lineup optimization did not find an optimal solution: {pulp.LpStatus[status]}")

    starting_ids = {r["player_id"] for r in rows if start[r["player_id"]].value() == 1}
    starting_xi = squad[squad["player_id"].isin(starting_ids)].sort_values("expected_points", ascending=False)
    bench = squad[~squad["player_id"].isin(starting_ids)].sort_values("expected_points", ascending=False)

    return {
        "starting_xi": starting_xi["player_id"].tolist(),
        "bench": bench["player_id"].tolist(),
        # int(): .iloc[0][...] returns a numpy scalar, unlike .tolist() above which already
        # converts to plain Python ints — left as numpy int64 this silently isn't
        # JSON-serializable downstream (cli.py's recommendation payload).
        "captain": int(starting_xi.iloc[0]["player_id"]),
        "vice_captain": int(starting_xi.iloc[1]["player_id"]),
    }
