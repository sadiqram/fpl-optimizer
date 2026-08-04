"""Understat live scraper: xG/xA/shots per player-gameweek for the current season.

understat.com no longer embeds player/team JSON in page HTML — the technique vaastav's
own archive scraper depends on (var playersData = JSON.parse('...') in a <script> tag)
returns nothing on the live site as of 2026-08 (verified against the league table page,
a completed-season page, and a player page — none contain it anymore). The site now
loads this client-side via small JSON endpoints, discovered from its own JS bundles:

  GET understat.com/getLeagueData/{league}/{season_start_year}
      -> {teams, players (season aggregates), dates (full match schedule, team names + datetime)}
  GET understat.com/getPlayerData/{understat_player_id}
      -> {player, matches: [per-match rows: goals, shots, xG, xA, time, date, h_team, a_team, ...]}

Both confirmed live and returning real data. This is arguably more robust than the old
technique (a real JSON endpoint, not HTML-embedded-JSON needing hex-decoding), but it is
still an undocumented, unofficial endpoint — expect it to break again eventually
(Architecture §6 "known weak point": isolated to ingestion, so a break doesn't lose history).
"""

from __future__ import annotations

import re
import time

import requests

BASE_URL = "https://understat.com"
HEADERS = {"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest"}

# Understat's full team names vs FPL's short display names, for the ones that don't
# normalize-match automatically. Extend as needed — a miss just means that match's
# gameweek can't be resolved and the row is skipped, not guessed at.
_TEAM_NAME_ALIASES = {
    "manchester city": "Man City",
    "manchester united": "Man Utd",
    "tottenham": "Spurs",
    "nottingham forest": "Nott'm Forest",
    "wolverhampton wanderers": "Wolves",
    "west bromwich albion": "West Brom",
}


class UnderstatClient:
    def __init__(self, session: requests.Session | None = None, timeout: float = 15.0, politeness_delay: float = 0.5):
        self._session = session or requests.Session()
        self._timeout = timeout
        self._politeness_delay = politeness_delay

    def _get_json(self, path: str):
        resp = self._session.get(f"{BASE_URL}/{path}", headers=HEADERS, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    def league_data(self, season_start_year: str, league: str = "EPL") -> dict:
        return self._get_json(f"getLeagueData/{league}/{season_start_year}")

    def player_matches(self, understat_player_id: int) -> list[dict]:
        data = self._get_json(f"getPlayerData/{understat_player_id}")
        time.sleep(self._politeness_delay)  # this is called once per player — many in a row
        return data.get("matches", [])


def season_to_understat_year(season: str) -> str:
    """'2025-26' -> '2025' (Understat's season param is just the start year)."""
    return season.split("-")[0]


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def resolve_team_id(understat_team_name: str, fpl_teams: list[dict]) -> int | None:
    aliased = _TEAM_NAME_ALIASES.get(understat_team_name.strip().lower(), understat_team_name)
    target = _normalize(aliased)
    for t in fpl_teams:
        if _normalize(t["name"]) == target or _normalize(t["short_name"]) == target:
            return t["id"]
    return None


def resolve_gameweek(conn, season: str, match_date: str, h_team_name: str, a_team_name: str, fpl_teams: list[dict]) -> int | None:
    """Finds the gameweek for one Understat match by date + team pair against our own
    fixtures table for that season. Returns None (never a guess) if it can't find exactly
    one confident match — e.g. an unmapped team name, or the season's fixtures aren't
    loaded yet."""
    home_id = resolve_team_id(h_team_name, fpl_teams)
    away_id = resolve_team_id(a_team_name, fpl_teams)
    if home_id is None or away_id is None:
        return None
    row = conn.execute(
        """
        SELECT event FROM fixtures
        WHERE season = ? AND team_h = ? AND team_a = ? AND date(kickoff_time) = date(?)
        """,
        (season, home_id, away_id, match_date),
    ).fetchone()
    return row["event"] if row else None


def ingest_season(conn, season: str, max_players: int | None = None, client: UnderstatClient | None = None) -> dict:
    """Matches Understat players to ours by name (same logic as
    archive_loader.match_understat_ids), then fetches per-player match logs and resolves
    each match's gameweek via our own fixtures table. Requires that season's fixtures to
    already be loaded (live `ingest` for the current season, or `bootstrap_season` for a
    past one) — otherwise every gameweek resolution comes back None and nothing is stored.

    max_players caps how many player match-logs get fetched in this call — each is a
    separate HTTP request with a politeness delay; a full season is 500+ players.
    """
    from fpl_optimizer.ingestion.archive_loader import match_understat_ids
    from fpl_optimizer.storage import db

    client = client or UnderstatClient()
    understat_year = season_to_understat_year(season)
    league = client.league_data(understat_year)

    fpl_players = [dict(r) for r in conn.execute("SELECT id AS code, first_name, second_name FROM players").fetchall()]
    matched = match_understat_ids(fpl_players, league["players"])
    if matched:
        db.upsert_player_id_map(conn, matched)

    fpl_teams = [dict(r) for r in conn.execute("SELECT id, name, short_name FROM teams").fetchall()]

    subset = matched[:max_players] if max_players else matched
    gw_rows_inserted = 0
    unresolved = 0
    for m in subset:
        # getPlayerData returns the player's ENTIRE career, not just this season — vaastav's
        # own per-player archive CSVs have the same shape for the same reason. Filter first.
        season_matches = [pm for pm in client.player_matches(m["understat_id"]) if pm["season"] == understat_year]
        for pm in season_matches:
            gw = resolve_gameweek(conn, season, pm["date"], pm["h_team"], pm["a_team"], fpl_teams)
            if gw is None:
                unresolved += 1
                continue
            db.insert_understat_player_gw(conn, [{
                "player_id": m["code"],
                "season": season,
                "gameweek": gw,
                "xg": float(pm["xG"]),
                "xa": float(pm["xA"]),
                "shots": int(pm["shots"]),
                "key_passes": int(pm["key_passes"]),
            }])
            gw_rows_inserted += 1

    return {
        "season": season,
        "understat_players_matched": len(matched),
        "player_match_logs_fetched": len(subset),
        "gw_rows_inserted": gw_rows_inserted,
        "unresolved_gameweeks": unresolved,
    }
