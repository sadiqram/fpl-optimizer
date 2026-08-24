"""FastAPI dependencies: per-request DB connection and JWT-authenticated user.

DB_PATH defaults to the same relative path the CLI uses (`cli.DEFAULT_DB_PATH`) so local dev
against the same SQLite file just works; the deployed environment overrides it via `DB_PATH`
to point at the Fly.io volume mount (Architecture §4.2/§4.7 — same file, no format change).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Iterator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from fpl_optimizer.api import auth
from fpl_optimizer.storage import db

DB_PATH = Path(os.environ.get("DB_PATH", "data/db/fpl.sqlite"))

_bearer_scheme = HTTPBearer(auto_error=False)


def get_conn() -> Iterator[sqlite3.Connection]:
    conn = db.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    conn: sqlite3.Connection = Depends(get_conn),
) -> sqlite3.Row:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    if credentials is None:
        raise unauthorized
    user_id = auth.decode_token(credentials.credentials)
    if user_id is None:
        raise unauthorized
    user = db.get_user_by_id(conn, user_id)
    if user is None:
        raise unauthorized
    return user


def require_fpl_team_id(user: sqlite3.Row = Depends(get_current_user)) -> int:
    """Squad/recommend/plan all need a connected FPL team — this is the shared 400 for
    'you haven't finished onboarding yet' rather than every router re-checking it."""
    if user["fpl_team_id"] is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No FPL team connected — set one in Settings first.")
    return user["fpl_team_id"]


def require_admin(user: sqlite3.Row = Depends(get_current_user)) -> sqlite3.Row:
    """Gates operator-only endpoints (e.g. /train, which overwrites the single shared model
    artifact every tenant's /recommendations and /plans load) behind an email allowlist —
    mirrors the CLI's FPL_ADMIN_EMAIL account-resolution pattern (cli.py), but as an
    authorization check rather than an identity one, since the web app already knows who's
    calling from the JWT."""
    admin_emails = {e.strip() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}
    if user["email"] not in admin_emails:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    return user
