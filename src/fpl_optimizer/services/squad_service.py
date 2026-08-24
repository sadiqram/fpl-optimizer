"""Owned-squad state ingestion (M6 FR1, extended for multi-tenancy in M8) — extracted from
`cli._cmd_squad`. Takes an explicit `team_id`/`user_id` pair instead of reading FPL_TEAM_ID
from the environment, since each account now connects its own team (schema.sql `users.fpl_team_id`).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from fpl_optimizer.ingestion.fpl_api import FPLClient
from fpl_optimizer.ingestion.snapshots import save_raw
from fpl_optimizer.storage import db
from fpl_optimizer.strategy import squad_state


def sync_squad(conn: sqlite3.Connection, user_id: int, team_id: int, season: str) -> dict:
    client = FPLClient()
    now = datetime.now(timezone.utc)

    history = client.entry_history(team_id)
    save_raw("fpl_api", f"entry-{team_id}-history", history, when=now)

    if not history["current"]:
        # NFR2: the season hasn't started yet (no gameweek has locked for this team) — there
        # is no "current squad" to report. Not an error, just nothing to do yet.
        raise ValueError(f"No locked gameweeks yet for team {team_id} — the season hasn't started.")

    last_locked_gw = max(row["event"] for row in history["current"])
    last_locked_row = next(row for row in history["current"] if row["event"] == last_locked_gw)

    transfers = client.entry_transfers(team_id)
    save_raw("fpl_api", f"entry-{team_id}-transfers", transfers, when=now)

    picks = client.entry_picks(team_id, last_locked_gw)
    save_raw("fpl_api", f"entry-{team_id}-picks-gw{last_locked_gw}", picks, when=now)

    db.insert_squad_transfers(conn, [
        {
            "event": t["event"], "element_in": t["element_in"], "element_in_cost": t["element_in_cost"],
            "element_out": t["element_out"], "element_out_cost": t["element_out_cost"], "time": t["time"],
        }
        for t in transfers
    ], user_id=user_id)

    element_to_player = db.get_element_id_to_player_id_map(conn)
    owned_rows = []
    for pick in picks["picks"]:
        player_id = element_to_player.get(pick["element"])
        if player_id is None:
            continue  # NFR2: an unrecognized element id shouldn't crash the whole sync
        owned_rows.append({
            "player_id": player_id,
            "is_starting": int(pick["position"] <= 11),
            "is_captain": int(pick["is_captain"]),
            "is_vice_captain": int(pick["is_vice_captain"]),
            "purchase_price": squad_state.resolve_purchase_price(conn, user_id, player_id, season),
        })
    db.insert_owned_squad(conn, owned_rows, user_id=user_id, season=season, gameweek=last_locked_gw, recorded_at=now.isoformat())

    free_transfers = squad_state.compute_free_transfers(history["current"], history["chips"])
    chips_available = squad_state.resolve_chips_available(history["chips"])
    bank = last_locked_row.get("bank")
    db.insert_team_state(
        conn, user_id=user_id, season=season, gameweek=last_locked_gw + 1, bank=bank,
        free_transfers=free_transfers, chips_available=chips_available, recorded_at=now.isoformat(),
    )

    return {
        "season": season,
        "gameweek": last_locked_gw,
        "next_gameweek": last_locked_gw + 1,
        "player_count": len(owned_rows),
        "bank": bank,
        "free_transfers": free_transfers,
        "chips_available": chips_available,
    }
