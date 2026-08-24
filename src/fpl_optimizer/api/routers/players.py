from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from fpl_optimizer.api.deps import get_conn, get_current_user
from fpl_optimizer.storage import db

router = APIRouter(tags=["players"])


@router.get("/players")
def list_players(_user: sqlite3.Row = Depends(get_current_user), conn: sqlite3.Connection = Depends(get_conn)):
    return [dict(r) for r in db.get_players(conn)]


@router.get("/teams")
def list_teams(_user: sqlite3.Row = Depends(get_current_user), conn: sqlite3.Connection = Depends(get_conn)):
    return [dict(r) for r in db.get_teams(conn)]
