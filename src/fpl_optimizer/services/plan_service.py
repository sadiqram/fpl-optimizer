"""Extracted from `cli._cmd_plan` (M6 FR3/4/8). Rolling-horizon transfer + lineup planning
against a real owned squad — requires `squad_service.sync_squad` to have been run first for
this user/season. Unlike the CLI version, this also **persists** the result to `plan_runs`
(Architecture §4.6's "current state" note flagged this as a gap: `plan` was never saved
anywhere, only printed).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

import pandas as pd

from fpl_optimizer.optimize import constraints
from fpl_optimizer.storage import db
from fpl_optimizer.strategy import chips, horizon


class PlanNotReady(ValueError):
    """Raised when `squad` state hasn't been synced yet for this (user, season, gameweek)."""


def run_plan(
    conn: sqlite3.Connection, the_clock, user_id: int, season: str, gameweek: int,
    model: str, predictor, preset: dict,
) -> dict:
    team_state_row = db.get_team_state(conn, user_id, season, gameweek)
    owned_rows = db.get_owned_squad(conn, user_id, season, gameweek - 1)
    if team_state_row is None or not owned_rows:
        raise PlanNotReady(f"No squad state for {season} GW{gameweek} — sync your squad first.")

    current_prices = {r["player_id"]: r["now_cost"] for r in db.get_player_snapshots_as_of(conn, the_clock.today())}
    owned_squad = pd.DataFrame([
        {
            "player_id": r["player_id"],
            # Selling price is recomputed from the latest known price, not frozen at squad
            # sync time — price moves between when you last synced and now. An unresolved
            # purchase_price (NFR2) falls back to current price (break-even).
            "selling_price": (
                constraints.selling_price(r["purchase_price"], current_prices[r["player_id"]])
                if r["purchase_price"] is not None and r["player_id"] in current_prices
                else current_prices.get(r["player_id"], r["purchase_price"])
            ),
        }
        for r in owned_rows
    ])

    result = horizon.plan_horizon(
        conn, the_clock, season, gameweek, predictor, owned_squad,
        bank=team_state_row["bank"] or 0, free_transfers=team_state_row["free_transfers"],
        preset=preset,
    )
    transfer_result, picked = result["transfer_result"], result["lineup"]
    week1 = result["weekly_predictions"][0]["predictions"]

    chips_available = json.loads(team_state_row["chips_available"] or "[]")
    chip_scenarios = chips.evaluate_chip_scenarios(
        result, owned_squad, bank=team_state_row["bank"] or 0, chips_available=chips_available
    )

    names = {r["id"]: r["web_name"] for r in conn.execute("SELECT id, web_name FROM players").fetchall()}

    def player_summary(player_id: int) -> dict:
        pts_row = week1.loc[week1.player_id == player_id, "expected_points"]
        return {
            "player_id": player_id,
            "name": names.get(player_id, f"#{player_id}"),
            "expected_points": float(pts_row.iloc[0]) if len(pts_row) else None,
        }

    payload = {
        "season": season,
        "gameweek": gameweek,
        "model": model,
        "as_of_date": result["as_of_date"],
        "preset": preset,
        "transfers_in": [names.get(pid, f"#{pid}") for pid in transfer_result["transfers_in"]],
        "transfers_out": [names.get(pid, f"#{pid}") for pid in transfer_result["transfers_out"]],
        "hits_taken": transfer_result["hits_taken"],
        "hit_cost": transfer_result["hit_cost"],
        "expected_points_gain": float(transfer_result["expected_points_gain"]),
        "starting_xi": [player_summary(pid) for pid in picked["starting_xi"]],
        "bench": [player_summary(pid) for pid in picked["bench"]],
        "captain": player_summary(picked["captain"]),
        "vice_captain": player_summary(picked["vice_captain"]),
        "chip_scenarios": chip_scenarios,
    }

    run_id = uuid.uuid4().hex
    db.insert_plan_run(
        conn, run_id, user_id=user_id, created_at=datetime.now(timezone.utc).isoformat(),
        season=season, gameweek=gameweek, payload=payload,
    )
    return {"run_id": run_id, **payload}
