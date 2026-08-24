"""Extracted from `cli._cmd_recommend`. Fresh single-gameweek squad pick (FR2-4) — still the
right tool for a wildcard/fresh-start recommendation or backtesting independent per-gameweek
picks (Architecture §4.6), as opposed to `plan_service`'s rolling-horizon transfer planning
against an existing owned squad.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

from fpl_optimizer.storage import db
from fpl_optimizer.strategy import horizon


def run_recommend(conn: sqlite3.Connection, the_clock, user_id: int, season: str, gameweek: int, model: str, predictor) -> dict:
    result = horizon.recommend_gameweek(conn, the_clock, season, gameweek, predictor)
    usable = result["predictions"].dropna(subset=["element_type", "team_id", "now_cost", "expected_points"])
    squad_df, picked = result["squad"], result["lineup"]

    names = {r["id"]: r["web_name"] for r in conn.execute("SELECT id, web_name FROM players").fetchall()}

    def player_summary(player_id: int) -> dict:
        pts = usable.loc[usable.player_id == player_id, "expected_points"].iloc[0]
        return {"player_id": player_id, "name": names.get(player_id, f"#{player_id}"), "expected_points": float(pts)}

    # Log every prediction and every recommendation made (FR6, Architecture P3) — this is
    # the data `evaluate` later joins against real outcomes.
    prediction_rows = usable[["player_id", "expected_points", "p_start", "std_dev"]].to_dict("records")
    db.insert_predictions(
        conn, prediction_rows, model_version=model, season=season,
        gameweek=gameweek, run_date=result["as_of_date"],
    )
    run_id = uuid.uuid4().hex
    payload = {
        "season": season,
        "gameweek": gameweek,
        "model": model,
        "as_of_date": result["as_of_date"],
        "squad_cost": float(squad_df["now_cost"].sum()),
        "squad_expected_points": float(squad_df["expected_points"].sum()),
        "starting_xi": [player_summary(pid) for pid in picked["starting_xi"]],
        "bench": [player_summary(pid) for pid in picked["bench"]],
        "captain": player_summary(picked["captain"]),
        "vice_captain": player_summary(picked["vice_captain"]),
    }
    db.insert_recommendation(
        conn, run_id, user_id=user_id, created_at=datetime.now(timezone.utc).isoformat(),
        season=season, gameweek=gameweek, payload=payload,
    )
    return {
        "run_id": run_id,
        "dropped_players": len(result["predictions"]) - len(usable),
        "predictions_logged": len(prediction_rows),
        **payload,
    }
