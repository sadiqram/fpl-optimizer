"""Season-long transfer simulation (M6c) — plays a full archived season with a real,
continuous owned squad (transfers, hits, banked free transfers carried forward week to
week), unlike `backtest.backtest_season`'s independent per-gameweek recommendations.

This is both M6's main real-data verification vehicle (per the M6 plan: build the
algorithmic core against the 2024-25 archive, exactly like the backtest harness already
does) and what finally supplies the continuity `evaluation/metrics.py`'s
`squad_selection_regret` docstring describes as missing — "needs an owned squad's
continuity and purchase price across gameweeks... until the Strategy layer's transfer logic
is built." Computing the fuller regret number itself (recommended-transfer-path vs.
hindsight-optimal-path) is a natural next step on top of what this returns, not built here —
this function's job is the season-long simulation, not a new metric.
"""

from __future__ import annotations

import pandas as pd

from fpl_optimizer.clock import FixedClock
from fpl_optimizer.models.base import Predictor
from fpl_optimizer.models.training_data import season_as_of_date
from fpl_optimizer.optimize import squad as squad_optimize
from fpl_optimizer.storage import db
from fpl_optimizer.strategy import horizon, squad_state


def simulate_season_with_transfers(
    conn,
    season: str,
    start_gameweek: int,
    end_gameweek: int,
    predictor: Predictor,
    opening_squad: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Starts from `opening_squad` (player_id, selling_price) — defaults to a fresh
    `squad.build_squad` pick at `start_gameweek`, i.e. "what a from-scratch pick would have
    looked like", purchased at that week's own prices (selling_price == now_cost, no
    rise/fall yet) — then calls `strategy.horizon.plan_horizon` gameweek by gameweek,
    carrying the resulting squad/bank/free-transfers forward as real state between
    iterations, same one-step banking rule `strategy.squad_state.next_free_transfers` uses
    for the live path. `predictor` must already be fitted; same leakage responsibility
    `backtest_season` places on its caller.

    Chip usage isn't modelled here (M6e territory, orthogonal to verifying transfer
    mechanics) — chips stay "available" throughout and are never spent.

    Returns one row per gameweek: gameweek, transfers_in, transfers_out, hits_taken,
    hit_cost, squad_actual_points (the owned squad's starting XI's real points that week),
    bank, free_transfers_available. Gameweeks with no recorded outcome are skipped (can't be
    scored), same convention as `backtest_season` — but squad state still carries forward
    through them, since a blank/unresolved gameweek in the archive shouldn't halt the
    simulation.
    """
    as_of_date = season_as_of_date(conn, season)
    the_clock = FixedClock(as_of_date)

    if opening_squad is None:
        _, merged = horizon.predict_gameweek(conn, as_of_date, season, start_gameweek, predictor)
        usable = merged.dropna(subset=horizon.USABLE_PREDICTION_COLUMNS)
        fresh = squad_optimize.build_squad(usable)
        opening_squad = fresh[["player_id", "now_cost"]].rename(columns={"now_cost": "selling_price"})

    owned_squad = opening_squad
    bank = 0
    free_transfers = 1
    rows = []

    for gameweek in range(start_gameweek, end_gameweek + 1):
        result = horizon.plan_horizon(conn, the_clock, season, gameweek, predictor, owned_squad, bank, free_transfers)
        transfer_result = result["transfer_result"]
        new_squad = transfer_result["new_squad"]

        total_budget = bank + int(owned_squad["selling_price"].sum())
        new_bank = total_budget - int(new_squad["price"].sum())
        new_free_transfers = squad_state.next_free_transfers(free_transfers, len(transfer_result["transfers_out"]))

        actual_rows = db.get_player_gw_stats_for_gameweek(conn, season, gameweek)
        if actual_rows:
            actuals = pd.DataFrame([dict(r) for r in actual_rows])
            starting_xi_ids = set(result["lineup"]["starting_xi"])
            squad_points = actuals[actuals.player_id.isin(starting_xi_ids)]["total_points"].sum()
            rows.append({
                "gameweek": gameweek,
                "transfers_in": transfer_result["transfers_in"],
                "transfers_out": transfer_result["transfers_out"],
                "hits_taken": transfer_result["hits_taken"],
                "hit_cost": transfer_result["hit_cost"],
                "squad_actual_points": float(squad_points),
                "bank": bank,
                "free_transfers_available": free_transfers,
            })

        # Carry state forward regardless of whether this gameweek could be scored — bought
        # this week, so selling_price == now_cost (no rise/fall yet), same as opening_squad.
        owned_squad = new_squad[["player_id", "price"]].rename(columns={"price": "selling_price"})
        bank = new_bank
        free_transfers = new_free_transfers

    return pd.DataFrame(rows)
