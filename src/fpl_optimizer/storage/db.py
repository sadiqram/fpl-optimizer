"""Connection management and repository query functions. As-of parameters are required, never optional (Architecture §4.3)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def connect(db_path: Path | str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()


def upsert_teams(conn: sqlite3.Connection, teams: list[dict]) -> None:
    conn.executemany(
        """
        INSERT INTO teams (id, name, short_name) VALUES (:id, :name, :short_name)
        ON CONFLICT(id) DO UPDATE SET name = excluded.name, short_name = excluded.short_name
        """,
        teams,
    )
    conn.commit()


def upsert_element_types(conn: sqlite3.Connection, element_types: list[dict]) -> None:
    conn.executemany(
        """
        INSERT INTO element_types (id, singular_name_short, singular_name)
        VALUES (:id, :singular_name_short, :singular_name)
        ON CONFLICT(id) DO UPDATE SET
            singular_name_short = excluded.singular_name_short,
            singular_name = excluded.singular_name
        """,
        element_types,
    )
    conn.commit()


def upsert_players(conn: sqlite3.Connection, players: list[dict], updated_at: str, is_live: bool = True) -> None:
    """`players` dicts carry FPL's usual shape: id (season-specific element id), code
    (cross-season stable id), first_name, second_name, web_name, team, element_type.
    Rows are keyed on `code` — see schema.sql for why.

    is_live must be False for archive-sourced rows: a past season's `id` is that season's
    element id, not the current one, and must never be written to element_id — doing so
    would let a historical bootstrap silently clobber the live id needed for API calls.
    Only a live caller's `id` is trustworthy as "the current element_id".
    """
    rows = [
        {
            "id": p["code"],
            "element_id": p.get("id") if is_live else None,
            "first_name": p["first_name"],
            "second_name": p["second_name"],
            "web_name": p["web_name"],
            "team_id": p["team"],
            "element_type": p["element_type"],
            "updated_at": updated_at,
        }
        for p in players
    ]
    conn.executemany(
        """
        INSERT INTO players (id, element_id, first_name, second_name, web_name, team_id, element_type, updated_at)
        VALUES (:id, :element_id, :first_name, :second_name, :web_name, :team_id, :element_type, :updated_at)
        ON CONFLICT(id) DO UPDATE SET
            element_id = COALESCE(excluded.element_id, players.element_id),
            first_name = excluded.first_name,
            second_name = excluded.second_name,
            web_name = excluded.web_name,
            team_id = excluded.team_id,
            element_type = excluded.element_type,
            updated_at = excluded.updated_at
        """,
        rows,
    )
    conn.commit()


def insert_player_snapshots(
    conn: sqlite3.Connection,
    players: list[dict],
    snapshot_date: str,
    fetched_at: str,
    source: str = "own_snapshot",
) -> None:
    def _pct(value):
        # Archive CSV rows can carry the literal string "None" for nulls, not just "".
        return float(value) if value not in (None, "", "None", "NA", "nan") else None

    rows = [
        {
            "player_id": p["code"],
            "snapshot_date": snapshot_date,
            "fetched_at": fetched_at,
            "source": source,
            "now_cost": p.get("now_cost"),
            "selected_by_percent": _pct(p.get("selected_by_percent")),
            "status": p.get("status"),
            "chance_of_playing_this_round": p.get("chance_of_playing_this_round"),
            "chance_of_playing_next_round": p.get("chance_of_playing_next_round"),
            "news": p.get("news"),
        }
        for p in players
    ]
    conn.executemany(
        """
        INSERT INTO player_snapshots (
            player_id, snapshot_date, fetched_at, source, now_cost, selected_by_percent,
            status, chance_of_playing_this_round, chance_of_playing_next_round, news
        ) VALUES (
            :player_id, :snapshot_date, :fetched_at, :source, :now_cost, :selected_by_percent,
            :status, :chance_of_playing_this_round, :chance_of_playing_next_round, :news
        )
        ON CONFLICT(player_id, snapshot_date, source) DO UPDATE SET
            fetched_at = excluded.fetched_at,
            now_cost = excluded.now_cost,
            selected_by_percent = excluded.selected_by_percent,
            status = excluded.status,
            chance_of_playing_this_round = excluded.chance_of_playing_this_round,
            chance_of_playing_next_round = excluded.chance_of_playing_next_round,
            news = excluded.news
        """,
        rows,
    )
    conn.commit()


def insert_fixtures(conn: sqlite3.Connection, fixtures: list[dict], season: str) -> None:
    """season is required — fixture id resets to 1 every season, so (season, id) is the
    real key (schema.sql)."""
    rows = [
        {
            "id": f["id"],
            "season": season,
            "event": f.get("event"),
            "kickoff_time": f.get("kickoff_time"),
            "team_h": f["team_h"],
            "team_a": f["team_a"],
            "team_h_difficulty": f.get("team_h_difficulty"),
            "team_a_difficulty": f.get("team_a_difficulty"),
            "finished": int(bool(f.get("finished", False))),
            "team_h_score": f.get("team_h_score"),
            "team_a_score": f.get("team_a_score"),
        }
        for f in fixtures
    ]
    conn.executemany(
        """
        INSERT INTO fixtures (
            id, season, event, kickoff_time, team_h, team_a, team_h_difficulty, team_a_difficulty,
            finished, team_h_score, team_a_score
        ) VALUES (
            :id, :season, :event, :kickoff_time, :team_h, :team_a, :team_h_difficulty, :team_a_difficulty,
            :finished, :team_h_score, :team_a_score
        )
        ON CONFLICT(season, id) DO UPDATE SET
            event = excluded.event,
            kickoff_time = excluded.kickoff_time,
            team_h_difficulty = excluded.team_h_difficulty,
            team_a_difficulty = excluded.team_a_difficulty,
            finished = excluded.finished,
            team_h_score = excluded.team_h_score,
            team_a_score = excluded.team_a_score
        """,
        rows,
    )
    conn.commit()


def insert_player_gw_stats(conn: sqlite3.Connection, rows: list[dict], source: str, season: str) -> None:
    """Outcome facts, immutable once written. `rows` items: player_id (code), gameweek,
    minutes, total_points, goals_scored, assists, clean_sheets, goals_conceded, bonus, bps,
    expected_goals, expected_assists. Trusted from any source (PRD §6a.4) — these don't
    change retroactively, unlike player_snapshots. season is required: gameweek numbers
    reset every season, so (player_id, gameweek) alone is not unique across seasons."""
    payload = [{**row, "source": source, "season": season} for row in rows]
    conn.executemany(
        """
        INSERT INTO player_gw_stats (
            player_id, season, gameweek, source, minutes, total_points, goals_scored, assists,
            clean_sheets, goals_conceded, bonus, bps, expected_goals, expected_assists
        ) VALUES (
            :player_id, :season, :gameweek, :source, :minutes, :total_points, :goals_scored, :assists,
            :clean_sheets, :goals_conceded, :bonus, :bps, :expected_goals, :expected_assists
        )
        ON CONFLICT(player_id, season, gameweek, source) DO UPDATE SET
            minutes = excluded.minutes,
            total_points = excluded.total_points,
            goals_scored = excluded.goals_scored,
            assists = excluded.assists,
            clean_sheets = excluded.clean_sheets,
            goals_conceded = excluded.goals_conceded,
            bonus = excluded.bonus,
            bps = excluded.bps,
            expected_goals = excluded.expected_goals,
            expected_assists = excluded.expected_assists
        """,
        payload,
    )
    conn.commit()


def insert_understat_player_gw(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """rows: player_id (code), season, gameweek, xg, xa, shots, key_passes."""
    conn.executemany(
        """
        INSERT INTO understat_player_gw (player_id, season, gameweek, xg, xa, shots, key_passes)
        VALUES (:player_id, :season, :gameweek, :xg, :xa, :shots, :key_passes)
        ON CONFLICT(player_id, season, gameweek) DO UPDATE SET
            xg = excluded.xg,
            xa = excluded.xa,
            shots = excluded.shots,
            key_passes = excluded.key_passes
        """,
        rows,
    )
    conn.commit()


def upsert_player_id_map(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """rows: {code, understat_id, match_method, notes (optional)}."""
    payload = [
        {
            "fpl_id": r["code"],
            "understat_id": r["understat_id"],
            "match_method": r["match_method"],
            "notes": r.get("notes"),
        }
        for r in rows
    ]
    conn.executemany(
        """
        INSERT INTO player_id_map (fpl_id, understat_id, match_method, notes)
        VALUES (:fpl_id, :understat_id, :match_method, :notes)
        ON CONFLICT(fpl_id) DO UPDATE SET
            understat_id = excluded.understat_id,
            match_method = excluded.match_method,
            notes = excluded.notes
        """,
        payload,
    )
    conn.commit()


def get_player_gw_stats_before(conn: sqlite3.Connection, season: str, gameweek: int) -> list[sqlite3.Row]:
    """Outcome rows for `season` strictly before `gameweek`. This is the leakage boundary
    for the features layer (Architecture §4.3): features built to decide `gameweek` may
    only see gameweeks that had already finished. season and gameweek are both required —
    gameweek numbers reset every season, so 'before gameweek N' is meaningless without one."""
    cursor = conn.execute(
        "SELECT * FROM player_gw_stats WHERE season = ? AND gameweek < ? ORDER BY gameweek",
        (season, gameweek),
    )
    return cursor.fetchall()


def get_understat_player_gw_before(conn: sqlite3.Connection, season: str, gameweek: int) -> list[sqlite3.Row]:
    cursor = conn.execute(
        "SELECT * FROM understat_player_gw WHERE season = ? AND gameweek < ? ORDER BY gameweek",
        (season, gameweek),
    )
    return cursor.fetchall()


def get_fixtures_for_gameweek(conn: sqlite3.Connection, season: str, gameweek: int) -> list[sqlite3.Row]:
    """Fixtures ARE known in advance (the schedule is published well before deadlines) —
    unlike outcomes, this is not a leakage risk."""
    cursor = conn.execute(
        "SELECT * FROM fixtures WHERE season = ? AND event = ?",
        (season, gameweek),
    )
    return cursor.fetchall()


def get_fixtures_before(conn: sqlite3.Connection, season: str, gameweek: int) -> list[sqlite3.Row]:
    cursor = conn.execute(
        "SELECT * FROM fixtures WHERE season = ? AND event < ? AND event IS NOT NULL ORDER BY event",
        (season, gameweek),
    )
    return cursor.fetchall()


def get_active_players(conn: sqlite3.Connection, season: str) -> list[int]:
    """Player codes considered 'in scope' for `season`: whoever has an outcome row that
    season, or — for a season with no gameweeks played yet (e.g. preseason) — whoever has
    a live snapshot. Falls back rather than returning empty so early-season feature builds
    degrade gracefully (NFR2) instead of silently producing nothing."""
    rows = conn.execute(
        "SELECT DISTINCT player_id FROM player_gw_stats WHERE season = ?", (season,)
    ).fetchall()
    if rows:
        return [r["player_id"] for r in rows]
    rows = conn.execute(
        "SELECT DISTINCT player_id FROM player_snapshots WHERE source = 'own_snapshot'"
    ).fetchall()
    return [r["player_id"] for r in rows]


def get_player_snapshots_as_of(conn: sqlite3.Connection, as_of_date: str) -> list[sqlite3.Row]:
    """Latest snapshot per player at or before as_of_date.

    as_of_date is a required argument, not an optional filter — this is the data-access
    boundary that makes leakage require actively working around the API rather than
    merely forgetting a filter (Architecture §4.3).
    """
    cursor = conn.execute(
        """
        SELECT ps.*
        FROM player_snapshots ps
        JOIN (
            SELECT player_id, MAX(snapshot_date) AS max_date
            FROM player_snapshots
            WHERE snapshot_date <= ?
            GROUP BY player_id
        ) latest ON ps.player_id = latest.player_id AND ps.snapshot_date = latest.max_date
        """,
        (as_of_date,),
    )
    return cursor.fetchall()
