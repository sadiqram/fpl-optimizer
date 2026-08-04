"""Bootstraps pre-launch history from third-party archives (vaastav, olbauday) — one-time/occasional, not daily cadence. See Architecture §4.1 and PRD §6a.4."""

from __future__ import annotations

import csv
import io
import unicodedata
from pathlib import Path

import requests

from fpl_optimizer.storage import db

VAASTAV_RAW_BASE = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"
DEFAULT_RAW_DIR = Path("data/raw/archive")


def _get_text(url: str, timeout: float = 15.0) -> str | None:
    resp = requests.get(url, timeout=timeout)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.text


def _save_raw_csv(source: str, season: str, filename: str, text: str, raw_dir: Path = DEFAULT_RAW_DIR) -> Path:
    path = raw_dir / source / season / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def fetch_players_and_teams(season: str) -> dict:
    base = f"{VAASTAV_RAW_BASE}/{season}"
    players_text = _get_text(f"{base}/players_raw.csv")
    teams_text = _get_text(f"{base}/teams.csv")
    if players_text is None or teams_text is None:
        raise ValueError(f"Season {season!r} not found in the vaastav archive")
    _save_raw_csv("vaastav", season, "players_raw.csv", players_text)
    _save_raw_csv("vaastav", season, "teams.csv", teams_text)
    return {
        "players": list(csv.DictReader(io.StringIO(players_text))),
        "teams": list(csv.DictReader(io.StringIO(teams_text))),
    }


def fetch_fixtures(season: str) -> list[dict]:
    text = _get_text(f"{VAASTAV_RAW_BASE}/{season}/fixtures.csv")
    if text is None:
        return []
    _save_raw_csv("vaastav", season, "fixtures.csv", text)
    return list(csv.DictReader(io.StringIO(text)))


def fetch_gameweeks(season: str, max_gw: int = 38) -> dict[int, list[dict]]:
    """Fetches gws/gw{N}.csv for N=1..max_gw, stopping at the first missing gameweek
    (an in-progress or future season simply has fewer files)."""
    base = f"{VAASTAV_RAW_BASE}/{season}/gws"
    result = {}
    for gw in range(1, max_gw + 1):
        text = _get_text(f"{base}/gw{gw}.csv")
        if text is None:
            break
        _save_raw_csv("vaastav", season, f"gws/gw{gw}.csv", text)
        result[gw] = list(csv.DictReader(io.StringIO(text)))
    return result


def fetch_understat_player_totals(season: str) -> list[dict] | None:
    """Season-aggregate Understat data (understat_player.csv). Returns None if this season
    has no understat/ directory yet — verified 2025-26 doesn't have one as of this writing,
    so Understat coverage lags the FPL-side data by roughly a season."""
    text = _get_text(f"{VAASTAV_RAW_BASE}/{season}/understat/understat_player.csv")
    if text is None:
        return None
    _save_raw_csv("vaastav", season, "understat/understat_player.csv", text)
    return list(csv.DictReader(io.StringIO(text)))


def _normalize_name(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower().strip()


def match_understat_ids(fpl_players: list[dict], understat_players: list[dict]) -> list[dict]:
    """Replicates vaastav's own id-matching logic (exact match on 'first_name second_name'
    vs Understat's 'player_name') with accent normalization added.

    The id_dict.csv file DATA_DICTIONARY.md documents as shipping this mapping does not
    actually exist in the repo — verified 404 across six seasons. It's a local output of
    vaastav's own understat.py script, never committed. This rebuilds the same mapping from
    the two source files that ARE shipped (players_raw.csv, understat_player.csv), per PRD
    §6a.4's "check the archive's own mapping first" — there's just an extra step to it.

    Returns matched rows only: {code, understat_id, match_method}. Unmatched players are
    left out for manual review (Architecture §4.2's accepted ~5%), not guessed at.
    """
    understat_by_name = {_normalize_name(u["player_name"]): u["id"] for u in understat_players}
    matches = []
    for p in fpl_players:
        fpl_name = _normalize_name(f"{p['first_name']} {p['second_name']}")
        understat_id = understat_by_name.get(fpl_name)
        if understat_id is not None:
            matches.append({
                "code": int(p["code"]),
                "understat_id": int(understat_id),
                "match_method": "archive_name_match",
            })
    return matches


# element_types not present in the live element_types table but that appear in archive
# seasons. FPL introduced a "Manager" player type (element_type 5) partway through its
# history; players_raw.csv gives only the bare numeric id, not a name, so unknown ids that
# aren't in this table get an explicit placeholder rather than crashing or being guessed at.
_KNOWN_ELEMENT_TYPE_NAMES = {5: ("Manager", "MNG")}


def _ensure_element_types(conn, element_type_ids: set[int]) -> None:
    existing = {row[0] for row in conn.execute("SELECT id FROM element_types")}
    missing = element_type_ids - existing
    if not missing:
        return
    rows = [
        {
            "id": et_id,
            "singular_name": _KNOWN_ELEMENT_TYPE_NAMES.get(et_id, (f"Unknown position {et_id}", ""))[0],
            "singular_name_short": _KNOWN_ELEMENT_TYPE_NAMES.get(et_id, ("", f"UNK{et_id}"))[1],
        }
        for et_id in missing
    ]
    db.upsert_element_types(conn, rows)


def _int_or(value, default=0):
    return int(value) if value not in (None, "") else default


def _float_or_none(value):
    return float(value) if value not in (None, "") else None


def bootstrap_season(conn, season: str) -> dict:
    """Fetches and parses one season from the archive into players/fixtures/player_gw_stats/
    player_id_map. Requires element_types already populated (from a prior `fpl-optimizer
    ingest` run) — players_raw.csv gives element_type as a bare id, not a name."""
    if conn.execute("SELECT COUNT(*) FROM element_types").fetchone()[0] == 0:
        raise RuntimeError(
            "element_types is empty — run `fpl-optimizer ingest` at least once before "
            "bootstrapping an archive season, so position names are resolvable."
        )

    data = fetch_players_and_teams(season)

    teams_rows = [{"id": int(t["id"]), "name": t["name"], "short_name": t["short_name"]} for t in data["teams"]]
    db.upsert_teams(conn, teams_rows)

    _ensure_element_types(conn, {int(p["element_type"]) for p in data["players"]})

    players_rows = [
        {
            "id": int(p["id"]),
            "code": int(p["code"]),
            "first_name": p["first_name"],
            "second_name": p["second_name"],
            "web_name": p["web_name"],
            "team": int(p["team"]),
            "element_type": int(p["element_type"]),
        }
        for p in data["players"]
    ]
    db.upsert_players(conn, players_rows, updated_at=f"archive:vaastav:{season}", is_live=False)
    element_to_code = {int(p["id"]): int(p["code"]) for p in data["players"]}

    fixtures_raw = fetch_fixtures(season)
    fixtures_rows = [
        {
            "id": int(f["id"]),
            "event": _int_or(f.get("event"), default=None),
            "kickoff_time": f.get("kickoff_time"),
            "team_h": int(f["team_h"]),
            "team_a": int(f["team_a"]),
            "team_h_difficulty": _int_or(f.get("team_h_difficulty"), default=None),
            "team_a_difficulty": _int_or(f.get("team_a_difficulty"), default=None),
            "finished": f.get("finished") == "True",
            "team_h_score": _int_or(f.get("team_h_score"), default=None),
            "team_a_score": _int_or(f.get("team_a_score"), default=None),
        }
        for f in fixtures_raw
    ]
    if fixtures_rows:
        db.insert_fixtures(conn, fixtures_rows, season=season)

    gameweeks = fetch_gameweeks(season)
    gw_stat_rows = 0
    for gw, rows in gameweeks.items():
        parsed = []
        for r in rows:
            code = element_to_code.get(int(r["element"]))
            if code is None:
                continue  # every gw row's element should be in this season's players_raw
            parsed.append({
                "player_id": code,
                "gameweek": gw,
                "minutes": _int_or(r.get("minutes")),
                "total_points": _int_or(r.get("total_points")),
                "goals_scored": _int_or(r.get("goals_scored")),
                "assists": _int_or(r.get("assists")),
                "clean_sheets": _int_or(r.get("clean_sheets")),
                "goals_conceded": _int_or(r.get("goals_conceded")),
                "bonus": _int_or(r.get("bonus")),
                "bps": _int_or(r.get("bps")),
                "expected_goals": _float_or_none(r.get("expected_goals")),
                "expected_assists": _float_or_none(r.get("expected_assists")),
            })
        if parsed:
            db.insert_player_gw_stats(conn, parsed, source="archive:vaastav", season=season)
            gw_stat_rows += len(parsed)

    understat_totals = fetch_understat_player_totals(season)
    id_map_matches = 0
    if understat_totals:
        matches = match_understat_ids(data["players"], understat_totals)
        db.upsert_player_id_map(conn, matches)
        id_map_matches = len(matches)

    return {
        "season": season,
        "teams": len(teams_rows),
        "players": len(players_rows),
        "fixtures": len(fixtures_rows),
        "gameweeks_fetched": len(gameweeks),
        "gw_stat_rows": gw_stat_rows,
        "understat_id_matches": id_map_matches,
        "understat_available": understat_totals is not None,
    }
