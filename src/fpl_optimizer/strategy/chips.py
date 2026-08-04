"""Chip timing via scenario comparison, not an optimizer variable (M6e; Architecture §4.6,
FR5). Chips are one-shot, discrete, and rare — folding them into the transfer MILP would add
combinatorial complexity for something a handful of scenario runs answers directly, and
scenario output ("Bench Boost is worth +9 this week") is far easier for a human to
sanity-check than a solver decision would be (NFR3).

Per-chip shortcuts rather than one generic "run the horizon twice" path — Bench Boost and
Triple Captain only affect *this* week's scoring and are read directly off the normal plan
already computed, no second solve needed; only Wildcard and Free Hit actually change which
squad you'd own, so those get a second `optimize_transfers` call.
"""

from __future__ import annotations

import pandas as pd

from fpl_optimizer.optimize import constraints
from fpl_optimizer.strategy import transfers


def evaluate_chip_scenarios(
    plan_result: dict, owned_squad: pd.DataFrame, bank: int, chips_available: list[str]
) -> dict[str, dict]:
    """`plan_result`: a `strategy.horizon.plan_horizon` return value — the normal (no chip)
    plan every scenario here is compared against. `chips_available`: which chips to
    evaluate (`team_state.chips_available`) — a used chip is never scenario-compared.

    Returns {chip_name: {"delta": float, "reasoning": str}}, sorted by delta descending —
    the best opportunity first. `delta` is the expected-points gain from playing that chip
    *this week* instead of the normal plan; a negative delta means it isn't worth playing
    yet.
    """
    week1 = plan_result["weekly_predictions"][0]["predictions"]
    normal_squad = plan_result["transfer_result"]["new_squad"]
    normal_squad_ids = set(normal_squad["player_id"])

    scenarios: dict[str, dict] = {}

    if "bboost" in chips_available:
        bench_points = float(week1[week1.player_id.isin(plan_result["lineup"]["bench"])]["expected_points"].sum())
        scenarios["bboost"] = {
            "delta": bench_points,
            "reasoning": f"Bench Boost would add your bench's {bench_points:.1f} expected points this week.",
        }

    if "3xc" in chips_available:
        captain_id = plan_result["lineup"]["captain"]
        captain_points = float(week1.loc[week1.player_id == captain_id, "expected_points"].iloc[0])
        scenarios["3xc"] = {
            "delta": captain_points,
            "reasoning": f"Triple Captain would add one extra multiplier on your captain ({captain_points:.1f} xPts).",
        }

    if "wildcard" in chips_available:
        # free_transfers=SQUAD_SIZE guarantees zero hits regardless of how much the squad
        # turns over — this is what makes optimize_transfers() *be* a wildcard rebuild
        # rather than needing separate solver code, and (unlike a plain fresh build_squad
        # call) it still respects real selling prices via bank + owned_squad, not a flat
        # budget. Uses the same risk-adjusted, horizon-weighted pool the normal plan did
        # (plan_result["pool"]), since Wildcard's effect persists across the whole window,
        # not just this week.
        wildcard = transfers.optimize_transfers(plan_result["pool"], owned_squad, bank, free_transfers=constraints.SQUAD_SIZE)
        normal_points = float(normal_squad["expected_points"].sum())
        wildcard_points = float(wildcard["new_squad"]["expected_points"].sum())
        scenarios["wildcard"] = {
            "delta": wildcard_points - normal_points,
            "reasoning": (
                f"A full Wildcard rebuild scores {wildcard_points:.1f} vs. {normal_points:.1f} "
                "for the current transfer plan, over the horizon."
            ),
        }

    if "freehit" in chips_available:
        # Free Hit reverts after this gameweek, so only this week's *raw* (undecayed) points
        # matter — week1 itself, not the horizon-weighted pool Wildcard uses.
        freehit = transfers.optimize_transfers(week1, owned_squad, bank, free_transfers=constraints.SQUAD_SIZE)
        freehit_points = float(freehit["new_squad"]["expected_points"].sum())
        normal_week1_points = float(week1[week1.player_id.isin(normal_squad_ids)]["expected_points"].sum())
        scenarios["freehit"] = {
            "delta": freehit_points - normal_week1_points,
            "reasoning": (
                f"A Free Hit squad scores {freehit_points:.1f} this week vs. "
                f"{normal_week1_points:.1f} for your normal squad."
            ),
        }

    return dict(sorted(scenarios.items(), key=lambda kv: kv[1]["delta"], reverse=True))
