"""Owned-squad state (M6, FR1): purchase prices, free-transfer banking, chip availability.

DB-driven, not live-API-driven (Architecture P2) — these are pure functions over already-
fetched dicts/DB rows. The live fetch lives in cli._cmd_squad; a backtest-side season
simulator can supply the same shapes from archive data without either side changing.
"""

from __future__ import annotations

from fpl_optimizer.optimize import constraints
from fpl_optimizer.storage import db

ALL_CHIPS = ["wildcard", "freehit", "bboost", "3xc"]


def resolve_purchase_price(conn, user_id: int, player_id: int, season: str) -> int | None:
    """Most recent squad_transfers row bringing this player into `user_id`'s squad (a player
    sold and re-bought later has a new purchase price, hence "most recent" not "first");
    falls back to the earliest own_snapshot price for someone held since the season-opening
    15, who never appears in squad_transfers at all (schema.sql's own comment on that
    table). None if neither source has anything — caller decides how to degrade (NFR2)."""
    transfers_in = db.get_squad_transfers_in(conn, user_id, player_id)
    if transfers_in:
        return transfers_in[0]["element_in_cost"]
    return db.get_earliest_own_snapshot_price(conn, player_id)


def next_free_transfers(available: int, transfers_made: int, cap: int = constraints.FREE_TRANSFER_CAP) -> int:
    """One step of FPL's banking rule: `available` FTs going into a gameweek, `transfers_made`
    used that week -> FTs available for the *next* gameweek. Never call this for a gameweek
    a wildcard/free hit was played — those don't consume or grow the banked count at all
    (see compute_free_transfers), so they're skipped from the simulation entirely rather than
    treated as "0 transfers used" here. Shared by compute_free_transfers (live, from
    entry_history) and evaluation/season_simulation.py (synthetic, backtest-driven) so the
    two can't drift on this rule."""
    used = min(transfers_made, available)
    return min(cap, available - used + 1)


def compute_free_transfers(
    gw_history: list[dict], chips_played: list[dict], cap: int = constraints.FREE_TRANSFER_CAP
) -> int:
    """Simulates FPL's banking rule forward from the season's start through the most recent
    played gameweek in `gw_history`, returning how many are available for the NEXT one.

    gw_history: entry_history()['current'] rows, each with 'event' and 'event_transfers'.
    chips_played: entry_history()['chips'] rows, each with 'name' and 'event'.

    GW1 never contributes (picking your initial 15 isn't a "transfer") — the baseline of 1
    free transfer is what's available going into GW2, so the simulation starts there.
    """
    chip_events = {c["event"] for c in chips_played if c["name"] in ("wildcard", "freehit")}
    available = 1
    for row in sorted((r for r in gw_history if r["event"] >= 2), key=lambda r: r["event"]):
        if row["event"] in chip_events:
            continue
        available = next_free_transfers(available, row["event_transfers"], cap)
    return available


def resolve_chips_available(chips_played: list[dict]) -> list[str]:
    """v1 simplification, documented rather than hidden: tracks used-vs-not per chip name,
    not the real rule that each chip is actually available once per season *half* (so a
    used Wildcard becomes available again at the winter reset). Undercounts availability in
    the second half of the season — acceptable for a first cut; PRD §11 already frames exact
    chip mechanics as something to refine later, not something to get perfectly right now."""
    used = {c["name"] for c in chips_played}
    return [c for c in ALL_CHIPS if c not in used]
