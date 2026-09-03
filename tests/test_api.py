"""API layer (M8): auth, tenant scoping, and one integration test per router. Uses a real
temp-file SQLite DB (not :memory:) so the overridden `get_conn` dependency can open a fresh
connection per request exactly like production does — TestClient dispatches sync routes to a
threadpool, and a single sqlite3.Connection isn't safe to share across those threads.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fpl_optimizer.api import deps
from fpl_optimizer.api.main import app
from fpl_optimizer.storage import db

os.environ.setdefault("JWT_SECRET", "test-secret-do-not-use-in-prod")


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.sqlite"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    db.init_db(conn)

    # Same shape as test_strategy.py's season_conn fixture — real position/team variety so
    # squad.build_squad and plan_horizon have genuine choices to make.
    db.upsert_teams(conn, [{"id": t, "name": f"Team{t}", "short_name": f"T{t}"} for t in range(1, 7)])
    db.upsert_element_types(conn, [
        {"id": 1, "singular_name_short": "GKP", "singular_name": "Goalkeeper"},
        {"id": 2, "singular_name_short": "DEF", "singular_name": "Defender"},
        {"id": 3, "singular_name_short": "MID", "singular_name": "Midfielder"},
        {"id": 4, "singular_name_short": "FWD", "singular_name": "Forward"},
    ])
    players = [
        {"id": i, "code": i, "first_name": f"P{i}", "second_name": f"P{i}", "web_name": f"P{i}",
         "team": (i % 6) + 1, "element_type": [1, 2, 3, 4][i % 4]}
        for i in range(1, 41)
    ]
    db.upsert_players(conn, players, updated_at="2025-08-01T00:00:00Z")
    db.insert_player_snapshots(conn, [
        {"code": p["id"], "now_cost": 45 + (p["id"] % 10) * 5, "selected_by_percent": "10.0", "status": "a",
         "chance_of_playing_this_round": None, "chance_of_playing_next_round": None, "news": ""}
        for p in players
    ], snapshot_date="2025-08-01", fetched_at="2025-08-01T00:00:00Z")
    db.insert_fixtures(conn, [
        {"id": 1, "event": 1, "kickoff_time": "2025-08-16T14:00:00Z", "team_h": 1, "team_a": 2,
         "team_h_difficulty": 3, "team_a_difficulty": 3, "finished": True, "team_h_score": 1, "team_a_score": 1},
        {"id": 2, "event": 2, "kickoff_time": "2025-08-23T14:00:00Z", "team_h": 3, "team_a": 4,
         "team_h_difficulty": 3, "team_a_difficulty": 3, "finished": True, "team_h_score": 2, "team_a_score": 0},
    ], season="2025-26")
    for gw in (1, 2):
        db.insert_player_gw_stats(conn, [
            {"player_id": p["id"], "gameweek": gw, "minutes": 90, "total_points": (p["id"] * gw) % 8,
             "goals_scored": 0, "assists": 0, "clean_sheets": 0, "goals_conceded": 1, "bonus": 0, "bps": 10,
             "expected_goals": 0.1, "expected_assists": 0.0} for p in players
        ], source="fpl_api", season="2025-26")
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def client(db_path: Path):
    def override_get_conn():
        connection = db.connect(db_path)
        try:
            yield connection
        finally:
            connection.close()

    app.dependency_overrides[deps.get_conn] = override_get_conn
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _register(client: TestClient, email: str = "a@example.com", password: str = "password123") -> dict:
    r = client.post("/auth/register", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def _auth_headers(client: TestClient, **kwargs) -> dict:
    token = _register(client, **kwargs)["access_token"]
    return {"Authorization": f"Bearer {token}"}


# --- auth ---------------------------------------------------------------------------------

def test_register_then_me(client: TestClient):
    headers = _auth_headers(client)
    r = client.get("/auth/me", headers=headers)
    assert r.status_code == 200
    assert r.json()["email"] == "a@example.com"
    assert r.json()["fpl_team_id"] is None


def test_register_duplicate_email_conflicts(client: TestClient):
    _register(client)
    r = client.post("/auth/register", json={"email": "a@example.com", "password": "password123"})
    assert r.status_code == 409


def test_login_wrong_password_unauthorized(client: TestClient):
    _register(client)
    r = client.post("/auth/login", json={"email": "a@example.com", "password": "nope"})
    assert r.status_code == 401


def test_login_correct_password(client: TestClient):
    _register(client)
    r = client.post("/auth/login", json={"email": "a@example.com", "password": "password123"})
    assert r.status_code == 200
    assert "access_token" in r.json()


def test_unauthenticated_request_rejected(client: TestClient):
    assert client.get("/auth/me").status_code == 401
    assert client.get("/players").status_code == 401


# --- users ----------------------------------------------------------------------------------

def test_update_fpl_team_id(client: TestClient):
    headers = _auth_headers(client)
    r = client.patch("/users/me", json={"fpl_team_id": 2786467}, headers=headers)
    assert r.status_code == 200
    assert r.json()["fpl_team_id"] == 2786467
    assert client.get("/auth/me", headers=headers).json()["fpl_team_id"] == 2786467


# --- players/teams ----------------------------------------------------------------------

def test_list_players_and_teams(client: TestClient):
    headers = _auth_headers(client)
    players = client.get("/players", headers=headers).json()
    teams = client.get("/teams", headers=headers).json()
    assert len(players) == 40
    assert len(teams) == 6
    assert {"id", "web_name", "position", "team_short_name"} <= players[0].keys()


# --- squad ------------------------------------------------------------------------------

def test_squad_sync_requires_connected_team(client: TestClient):
    headers = _auth_headers(client)
    r = client.post("/squad/sync", json={"season": "2025-26"}, headers=headers)
    assert r.status_code == 400
    assert "No FPL team connected" in r.json()["detail"]


def test_get_squad_returns_player_names_clubs_and_values(client: TestClient, db_path: Path):
    """Regression guard: the squad page renders this endpoint's response as the actual
    15-player squad (name/club/value), not just the sync summary counts."""
    headers = _auth_headers(client)
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    _seed_owned_squad(db_path, user_id)

    r = client.get("/squad/2025-26/2", headers=headers)
    assert r.status_code == 200, r.text
    owned = r.json()["owned_squad"]
    assert len(owned) == 15
    assert {"web_name", "position", "team_short_name", "team_name", "current_price"} <= owned[0].keys()
    assert all(row["web_name"] == f"P{row['player_id']}" for row in owned)
    assert all(row["current_price"] == 45 + (row["player_id"] % 10) * 5 for row in owned)


# --- recommend --------------------------------------------------------------------------

def test_recommend_roundtrip_and_tenant_isolation(client: TestClient):
    headers_a = _auth_headers(client, email="a@example.com")
    r = client.post(
        "/recommendations", headers=headers_a,
        json={"season": "2025-26", "gameweek": 3, "model": "naive", "as_of": "2025-08-25"},
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["season"] == "2025-26"
    assert len(payload["starting_xi"]) == 11
    assert len(payload["bench"]) == 4
    run_id = payload["run_id"]

    assert len(client.get("/recommendations", headers=headers_a).json()) == 1
    assert client.get(f"/recommendations/{run_id}", headers=headers_a).status_code == 200

    # A different account must not be able to see or fetch it (multi-tenant isolation, M8).
    headers_b = _auth_headers(client, email="b@example.com")
    assert client.get("/recommendations", headers=headers_b).json() == []
    assert client.get(f"/recommendations/{run_id}", headers=headers_b).status_code == 404


def test_recommend_rejects_unknown_model(client: TestClient):
    headers = _auth_headers(client)
    r = client.post(
        "/recommendations", headers=headers,
        json={"season": "2025-26", "gameweek": 3, "model": "not-a-model"},
    )
    assert r.status_code == 422


# --- plan -------------------------------------------------------------------------------

def _seed_owned_squad(db_path: Path, user_id: int) -> None:
    """Bypasses squad_service (no real FPL API call in a test) — writes the same shape
    `sync_squad` would have, directly."""
    conn = db.connect(db_path)
    squad_rows = [
        {"player_id": i, "is_starting": 1, "is_captain": int(i == 1), "is_vice_captain": int(i == 2), "purchase_price": 45 + (i % 10) * 5}
        for i in range(1, 16)
    ]
    db.insert_owned_squad(conn, squad_rows, user_id=user_id, season="2025-26", gameweek=2, recorded_at="2025-08-24T00:00:00Z")
    db.insert_team_state(conn, user_id=user_id, season="2025-26", gameweek=3, bank=0, free_transfers=1, chips_available=["wildcard", "bboost", "3xc", "freehit"], recorded_at="2025-08-24T00:00:00Z")
    conn.close()


def test_plan_roundtrip(client: TestClient, db_path: Path):
    headers = _auth_headers(client)
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    _seed_owned_squad(db_path, user_id)

    r = client.post(
        "/plans", headers=headers,
        json={"season": "2025-26", "gameweek": 3, "model": "naive", "as_of": "2025-08-25"},
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["season"] == "2025-26"
    assert len(payload["starting_xi"]) == 11
    run_id = payload["run_id"]
    assert client.get(f"/plans/{run_id}", headers=headers).status_code == 200
    assert len(client.get("/plans", headers=headers).json()) == 1


def test_plan_without_squad_sync_is_400(client: TestClient):
    headers = _auth_headers(client)
    r = client.post("/plans", headers=headers, json={"season": "2025-26", "gameweek": 3, "model": "naive"})
    assert r.status_code == 400


# --- evaluate / accuracy-log --------------------------------------------------------------

def test_evaluate_not_ready_is_400(client: TestClient):
    headers = _auth_headers(client)
    r = client.post("/evaluate", headers=headers, json={"season": "2025-26", "gameweek": 3})
    assert r.status_code == 400


def test_accuracy_log_is_a_list(client: TestClient):
    """Doesn't assert emptiness: `read_accuracy_log`'s default path argument is bound at
    import time (a plain default, not read fresh per call), so it points at this repo's
    real accuracy_log.csv regardless of the per-test temp DB — asserting the response shape
    is the honest thing to check here, not its contents."""
    headers = _auth_headers(client)
    r = client.get("/accuracy-log", headers=headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# --- train ------------------------------------------------------------------------------

def test_train_rejects_non_admin(client: TestClient, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
    headers = _auth_headers(client, email="a@example.com")
    r = client.post(
        "/train", headers=headers,
        json={"season": "2025-26", "train_start": 1, "train_end": 1, "test_start": 2, "test_end": 2, "save": False},
    )
    assert r.status_code == 403


def test_train_allows_admin(client: TestClient, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
    headers = _auth_headers(client, email="admin@example.com")
    r = client.post(
        "/train", headers=headers,
        json={"season": "2025-26", "train_start": 1, "train_end": 1, "test_start": 2, "test_end": 2, "save": False},
    )
    assert r.status_code == 200, r.text
    assert "mae_by_model" in r.json()


# --- backtest ---------------------------------------------------------------------------

def test_backtest_smoke(client: TestClient):
    headers = _auth_headers(client)
    r = client.post(
        "/backtest", headers=headers,
        json={"season": "2025-26", "start_gameweek": 2, "end_gameweek": 2, "model": "naive"},
    )
    assert r.status_code == 200
    assert isinstance(r.json(), list)
