"""Connection management and repository query functions. As-of parameters are required, never optional (Architecture §4.3)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Every caller gets a schema-current connection: init_db's CREATE TABLE/etc. are all
    IF NOT EXISTS, so applying it here on every connect is free once the schema already
    matches, and self-heals the case that used to crash any command but `ingest` — a DB
    predating a schema change (e.g. this file, before `season` was added to `predictions`)
    would 500 the first time `recommend`/`evaluate`/anything else touched the new table,
    since only `_cmd_ingest` used to call init_db (docs/error_log.md, M5 entry)."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    init_db(conn)
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


def get_player_gw_stats_for_gameweek(conn: sqlite3.Connection, season: str, gameweek: int) -> list[sqlite3.Row]:
    """Outcome rows AT exactly `gameweek` — the training *label* when fitting a model, not
    a feature. Using ground truth as a regression target is correct and standard; the
    leakage boundary (get_player_gw_stats_before) is about what a model may see as *input*
    at prediction time, which is a different question from what we train it to predict."""
    cursor = conn.execute(
        "SELECT * FROM player_gw_stats WHERE season = ? AND gameweek = ?",
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


def get_element_id_to_player_id_map(conn: sqlite3.Connection) -> dict[int, int]:
    """The live season's current element_id -> stable player code mapping (same identity
    gotcha as everywhere else, error_log.md #2) — shared by any ingestion path that has to
    translate a season-specific FPL element id (event/live/, entry/picks/, entry/transfers/)
    back to our stable player_id."""
    return {
        row["element_id"]: row["id"]
        for row in conn.execute("SELECT id, element_id FROM players WHERE element_id IS NOT NULL")
    }


def insert_event_live_stats(conn: sqlite3.Connection, elements: list[dict], season: str, gameweek: int) -> int:
    """`elements`: the raw `event/{id}/live/` response's "elements" list —
    [{"id": <this season's element id>, "stats": {...}}]. Maps each element id to the
    stable player code via players.element_id and writes into player_gw_stats with
    source='fpl_api'. Elements whose id isn't a currently-known live player are skipped
    rather than raising (NFR2). Returns the number of rows written."""
    element_to_player = get_element_id_to_player_id_map(conn)

    def _num(value):
        # Same "None" spelled as a literal string pitfall as archive CSVs (error_log.md #10).
        return float(value) if value not in (None, "", "None", "NA", "nan") else None

    rows = []
    for element in elements:
        player_id = element_to_player.get(element["id"])
        if player_id is None:
            continue
        stats = element["stats"]
        rows.append({
            "player_id": player_id,
            "gameweek": gameweek,
            "minutes": stats.get("minutes"),
            "total_points": stats.get("total_points"),
            "goals_scored": stats.get("goals_scored"),
            "assists": stats.get("assists"),
            "clean_sheets": stats.get("clean_sheets"),
            "goals_conceded": stats.get("goals_conceded"),
            "bonus": stats.get("bonus"),
            "bps": stats.get("bps"),
            "expected_goals": _num(stats.get("expected_goals")),
            "expected_assists": _num(stats.get("expected_assists")),
        })

    insert_player_gw_stats(conn, rows, source="fpl_api", season=season)
    return len(rows)


def insert_predictions(
    conn: sqlite3.Connection, rows: list[dict], model_version: str, season: str, gameweek: int, run_date: str
) -> None:
    """rows: {player_id, expected_points, p_start, std_dev}. Every prediction ever made is
    kept (Architecture §4.4 "model versioning") — season is part of the key for the same
    reason as player_gw_stats: gameweek numbers reset every season. Re-running `recommend`
    for the same (model_version, season, player, gameweek, run_date) updates in place rather
    than duplicating, so reruns on the same day don't pile up rows."""
    payload = [
        {**row, "model_version": model_version, "season": season, "gameweek": gameweek, "run_date": run_date}
        for row in rows
    ]
    conn.executemany(
        """
        INSERT INTO predictions (model_version, season, player_id, gameweek, run_date, expected_points, p_start, std_dev)
        VALUES (:model_version, :season, :player_id, :gameweek, :run_date, :expected_points, :p_start, :std_dev)
        ON CONFLICT(model_version, season, player_id, gameweek, run_date) DO UPDATE SET
            expected_points = excluded.expected_points,
            p_start = excluded.p_start,
            std_dev = excluded.std_dev
        """,
        payload,
    )
    conn.commit()


def get_latest_predictions(
    conn: sqlite3.Connection, season: str, gameweek: int, model_version: str | None = None
) -> list[sqlite3.Row]:
    """One row per (model_version, player_id): the most recently logged prediction (by
    run_date) for that gameweek — if `recommend` was run more than once before a deadline,
    this is the prediction that was actually live closest to it, not an earlier draft.
    `model_version=None` returns every logged model's latest predictions, so `evaluate` can
    compare several models for the same gameweek in one report."""
    params: list = [season, gameweek]
    model_filter = ""
    if model_version is not None:
        model_filter = "AND model_version = ?"
        params.append(model_version)

    cursor = conn.execute(
        f"""
        SELECT p.* FROM predictions p
        JOIN (
            SELECT model_version, player_id, MAX(run_date) AS run_date
            FROM predictions
            WHERE season = ? AND gameweek = ? {model_filter}
            GROUP BY model_version, player_id
        ) latest
            ON p.model_version = latest.model_version
            AND p.player_id = latest.player_id
            AND p.run_date = latest.run_date
        WHERE p.season = ? AND p.gameweek = ?
        """,
        [*params, season, gameweek],
    )
    return cursor.fetchall()


def insert_recommendation(
    conn: sqlite3.Connection, run_id: str, created_at: str, season: str, gameweek: int, payload: dict
) -> None:
    """Persists the full recommendation output + rationale as JSON (Architecture §4.2,
    P3: every intermediate artifact is persisted). One row per run — reruns get a fresh
    run_id rather than overwriting, so the history of what was actually recommended, and
    when, is never lost."""
    conn.execute(
        "INSERT INTO recommendations (run_id, created_at, season, gameweek, payload) VALUES (?, ?, ?, ?, ?)",
        (run_id, created_at, season, gameweek, json.dumps(payload)),
    )
    conn.commit()


def insert_squad_transfers(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """rows: raw `entry/{id}/transfers/` items — element_in, element_in_cost, element_out,
    element_out_cost, event, time. Doesn't include the season-opening 15 (schema.sql's own
    comment on this table) — those are resolved from the earliest own_snapshot instead, see
    squad_state.resolve_purchase_price. Upsert on (element_in, element_out, time) — FPL never
    returns the same transfer twice, but re-running `squad` on the same history must not
    duplicate rows."""
    conn.executemany(
        """
        INSERT INTO squad_transfers (event, element_in, element_in_cost, element_out, element_out_cost, time)
        VALUES (:event, :element_in, :element_in_cost, :element_out, :element_out_cost, :time)
        ON CONFLICT(element_in, element_out, time) DO UPDATE SET
            event = excluded.event,
            element_in_cost = excluded.element_in_cost,
            element_out_cost = excluded.element_out_cost
        """,
        rows,
    )
    conn.commit()


def get_squad_transfers_in(conn: sqlite3.Connection, player_id: int) -> list[sqlite3.Row]:
    """Every transfer that brought `player_id` into the squad, most recent first — used to
    resolve their purchase price (the most recent element_in_cost, since a player sold and
    re-bought later has a new purchase price)."""
    cursor = conn.execute(
        "SELECT * FROM squad_transfers WHERE element_in = ? ORDER BY time DESC", (player_id,)
    )
    return cursor.fetchall()


def get_earliest_own_snapshot_price(conn: sqlite3.Connection, player_id: int) -> int | None:
    """A player's price at the earliest own_snapshot on record — the fallback purchase price
    for someone held since the season-opening 15, who never appears in squad_transfers
    (schema.sql's comment on that table; Architecture "M1 urgency" note is why this is only
    approximate for a squad assembled before daily snapshotting started)."""
    row = conn.execute(
        """
        SELECT now_cost FROM player_snapshots
        WHERE player_id = ? AND source = 'own_snapshot' AND now_cost IS NOT NULL
        ORDER BY snapshot_date ASC LIMIT 1
        """,
        (player_id,),
    ).fetchone()
    return row["now_cost"] if row else None


def insert_owned_squad(conn: sqlite3.Connection, rows: list[dict], season: str, gameweek: int, recorded_at: str) -> None:
    """rows: {player_id, is_starting, is_captain, is_vice_captain, purchase_price}. One
    snapshot per (season, gameweek) — re-running `squad` for the same locked gameweek
    updates in place rather than duplicating."""
    payload = [
        {**row, "season": season, "gameweek": gameweek, "recorded_at": recorded_at} for row in rows
    ]
    conn.executemany(
        """
        INSERT INTO owned_squad (season, gameweek, player_id, is_starting, is_captain, is_vice_captain, purchase_price, recorded_at)
        VALUES (:season, :gameweek, :player_id, :is_starting, :is_captain, :is_vice_captain, :purchase_price, :recorded_at)
        ON CONFLICT(season, gameweek, player_id) DO UPDATE SET
            is_starting = excluded.is_starting,
            is_captain = excluded.is_captain,
            is_vice_captain = excluded.is_vice_captain,
            purchase_price = excluded.purchase_price,
            recorded_at = excluded.recorded_at
        """,
        payload,
    )
    conn.commit()


def get_owned_squad(conn: sqlite3.Connection, season: str, gameweek: int) -> list[sqlite3.Row]:
    cursor = conn.execute(
        "SELECT * FROM owned_squad WHERE season = ? AND gameweek = ?", (season, gameweek)
    )
    return cursor.fetchall()


def insert_team_state(
    conn: sqlite3.Connection, season: str, gameweek: int, bank: int | None,
    free_transfers: int, chips_available: list[str], recorded_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO team_state (season, gameweek, bank, free_transfers, chips_available, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(season, gameweek) DO UPDATE SET
            bank = excluded.bank,
            free_transfers = excluded.free_transfers,
            chips_available = excluded.chips_available,
            recorded_at = excluded.recorded_at
        """,
        (season, gameweek, bank, free_transfers, json.dumps(chips_available), recorded_at),
    )
    conn.commit()


def get_team_state(conn: sqlite3.Connection, season: str, gameweek: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM team_state WHERE season = ? AND gameweek = ?", (season, gameweek)
    ).fetchone()
