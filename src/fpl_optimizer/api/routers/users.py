from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from fpl_optimizer.api import schemas
from fpl_optimizer.api.deps import get_conn, get_current_user
from fpl_optimizer.storage import db

router = APIRouter(prefix="/users", tags=["users"])


@router.patch("/me")
def update_me(
    body: schemas.UpdateFplTeamIdRequest,
    user: sqlite3.Row = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Onboarding step: connect a real FPL team to this account (schema.sql `users.fpl_team_id`,
    the tenant key `squad`/`recommend`/`plan` all resolve from instead of a single env var)."""
    db.update_user_fpl_team_id(conn, user["id"], body.fpl_team_id)
    return {"id": user["id"], "email": user["email"], "fpl_team_id": body.fpl_team_id}
