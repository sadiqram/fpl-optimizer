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


def upsert_players(conn: sqlite3.Connection, players: list[dict], updated_at: str) -> None:
    rows = [
        {
            "id": p["id"],
            "code": p["code"],
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
        INSERT INTO players (id, code, first_name, second_name, web_name, team_id, element_type, updated_at)
        VALUES (:id, :code, :first_name, :second_name, :web_name, :team_id, :element_type, :updated_at)
        ON CONFLICT(id) DO UPDATE SET
            code = excluded.code,
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
        return float(value) if value not in (None, "") else None

    rows = [
        {
            "player_id": p["id"],
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


def insert_fixtures(conn: sqlite3.Connection, fixtures: list[dict]) -> None:
    rows = [
        {
            "id": f["id"],
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
            id, event, kickoff_time, team_h, team_a, team_h_difficulty, team_a_difficulty,
            finished, team_h_score, team_a_score
        ) VALUES (
            :id, :event, :kickoff_time, :team_h, :team_a, :team_h_difficulty, :team_a_difficulty,
            :finished, :team_h_score, :team_a_score
        )
        ON CONFLICT(id) DO UPDATE SET
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
