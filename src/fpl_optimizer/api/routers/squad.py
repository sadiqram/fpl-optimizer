from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from fpl_optimizer.api import schemas
from fpl_optimizer.api.deps import get_conn, get_current_user, require_fpl_team_id
from fpl_optimizer.services import squad_service
from fpl_optimizer.storage import db

router = APIRouter(prefix="/squad", tags=["squad"])


@router.post("/sync")
def sync(
    body: schemas.SyncSquadRequest,
    user: sqlite3.Row = Depends(get_current_user),
    team_id: int = Depends(require_fpl_team_id),
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        return squad_service.sync_squad(conn, user_id=user["id"], team_id=team_id, season=body.season)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/{season}/{gameweek}")
def get_squad(
    season: str, gameweek: int,
    user: sqlite3.Row = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    owned = [dict(r) for r in db.get_owned_squad(conn, user["id"], season, gameweek)]
    team_state = db.get_team_state(conn, user["id"], season, gameweek + 1)
    return {"owned_squad": owned, "team_state": dict(team_state) if team_state else None}
