-- SQLite schema (Architecture §4.2). Forward-only migrations; no down-migrations.
-- Column names mirror the FPL API where practical, to keep parsing a straight copy.

PRAGMA foreign_keys = ON;

-- Reference data: teams and positions. Slowly-changing, re-synced on every ingest.
CREATE TABLE IF NOT EXISTS teams (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    short_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS element_types (
    id                   INTEGER PRIMARY KEY,
    singular_name_short  TEXT NOT NULL,  -- GKP / DEF / MID / FWD
    singular_name        TEXT NOT NULL
);

-- Slowly-changing identity: name, position, team. NOT where price/injury/ownership live —
-- those are point-in-time and belong in player_snapshots (Architecture §4.2).
CREATE TABLE IF NOT EXISTS players (
    id           INTEGER PRIMARY KEY,  -- FPL element id
    code         INTEGER,              -- FPL's cross-season stable player code
    first_name   TEXT NOT NULL,
    second_name  TEXT NOT NULL,
    web_name     TEXT NOT NULL,
    team_id      INTEGER NOT NULL REFERENCES teams(id),
    element_type INTEGER NOT NULL REFERENCES element_types(id),
    updated_at   TEXT NOT NULL  -- last time this row was refreshed from source
);

-- Point-in-time attributes. Separate from player_gw_stats on purpose: these are revised,
-- outcomes are not. Keyed by observation date so leakage requires actively ignoring as-of,
-- not forgetting a filter (Architecture §4.2, §4.3).
CREATE TABLE IF NOT EXISTS player_snapshots (
    id                            INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id                     INTEGER NOT NULL REFERENCES players(id),
    snapshot_date                 TEXT NOT NULL,  -- ISO date this snapshot is as-of
    fetched_at                    TEXT NOT NULL,  -- actual wall-clock fetch time
    source                        TEXT NOT NULL,  -- 'own_snapshot' | 'archive:<name>'
    now_cost                      INTEGER,        -- price, 0.1m units (FPL convention)
    selected_by_percent           REAL,
    status                        TEXT,           -- a/d/i/s/u availability flag
    chance_of_playing_this_round  INTEGER,
    chance_of_playing_next_round  INTEGER,
    news                          TEXT,
    UNIQUE (player_id, snapshot_date, source)
);

-- Outcomes: immutable facts once a gameweek resolves. Trusted from any source
-- (PRD §6a.4 feature trust partition) since they don't change retroactively.
CREATE TABLE IF NOT EXISTS player_gw_stats (
    player_id        INTEGER NOT NULL REFERENCES players(id),
    gameweek         INTEGER NOT NULL,
    source           TEXT NOT NULL DEFAULT 'fpl_api',
    minutes          INTEGER,
    total_points     INTEGER,
    goals_scored     INTEGER,
    assists          INTEGER,
    clean_sheets     INTEGER,
    goals_conceded   INTEGER,
    bonus            INTEGER,
    bps              INTEGER,
    expected_goals   REAL,
    expected_assists REAL,
    PRIMARY KEY (player_id, gameweek, source)
);

CREATE TABLE IF NOT EXISTS fixtures (
    id                 INTEGER PRIMARY KEY,  -- FPL fixture id
    event              INTEGER,              -- gameweek; NULL for unscheduled blanks
    kickoff_time       TEXT,
    team_h             INTEGER NOT NULL REFERENCES teams(id),
    team_a             INTEGER NOT NULL REFERENCES teams(id),
    team_h_difficulty  INTEGER,
    team_a_difficulty  INTEGER,
    finished           INTEGER NOT NULL DEFAULT 0,
    team_h_score       INTEGER,
    team_a_score       INTEGER
);

-- Aggregate attacking/defensive strength per team x gameweek. Populated once a
-- team-strength feature builder exists (M2+); table defined now so the schema is stable.
CREATE TABLE IF NOT EXISTS team_stats (
    team_id  INTEGER NOT NULL REFERENCES teams(id),
    gameweek INTEGER NOT NULL,
    source   TEXT NOT NULL DEFAULT 'fpl_api',
    xg_for      REAL,
    xg_against  REAL,
    PRIMARY KEY (team_id, gameweek, source)
);

CREATE TABLE IF NOT EXISTS understat_player_gw (
    player_id  INTEGER NOT NULL REFERENCES players(id),  -- mapped via player_id_map
    gameweek   INTEGER NOT NULL,
    xg         REAL,
    xa         REAL,
    shots      INTEGER,
    key_passes INTEGER,
    PRIMARY KEY (player_id, gameweek)
);

-- FPL <-> Understat id mapping. Populated primarily from the archive's own mapping
-- (Architecture §4.2); manual_override marks rows hand-corrected rather than matched.
CREATE TABLE IF NOT EXISTS player_id_map (
    fpl_id          INTEGER PRIMARY KEY REFERENCES players(id),
    understat_id    INTEGER,
    match_method    TEXT,  -- 'archive' | 'manual' | 'auto'
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS predictions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    model_version    TEXT NOT NULL,
    player_id        INTEGER NOT NULL REFERENCES players(id),
    gameweek         INTEGER NOT NULL,
    run_date         TEXT NOT NULL,
    expected_points  REAL NOT NULL,
    p_start          REAL,
    std_dev          REAL,
    UNIQUE (model_version, player_id, gameweek, run_date)
);

CREATE TABLE IF NOT EXISTS recommendations (
    run_id     TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    gameweek   INTEGER NOT NULL,
    payload    TEXT NOT NULL  -- full recommendation output + rationale, as JSON
);

-- Purchase-price tracking for selling-price correctness (PRD §11 "Still open",
-- folded into M1). Mirrors the FPL entry/transfers/ response shape. The season-opening
-- 15 aren't "transfers" and won't appear here — their purchase price comes from the
-- earliest player_snapshots row at/after squad lock, which is why snapshotting from
-- day one matters (Architecture "M1 urgency" note).
CREATE TABLE IF NOT EXISTS squad_transfers (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    event            INTEGER NOT NULL,
    element_in       INTEGER NOT NULL REFERENCES players(id),
    element_in_cost  INTEGER NOT NULL,
    element_out      INTEGER REFERENCES players(id),
    element_out_cost INTEGER,
    time             TEXT NOT NULL,
    UNIQUE (element_in, element_out, time)
);
