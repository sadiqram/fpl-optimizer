from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from fpl_optimizer.api import schemas
from fpl_optimizer.api.deps import get_conn, get_current_user
from fpl_optimizer.services import train_service

router = APIRouter(prefix="/train", tags=["train"])


@router.post("")
def train(
    body: schemas.TrainRequest,
    _user: sqlite3.Row = Depends(get_current_user),  # global model artifact, not per-tenant
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        return train_service.run_train(
            conn, body.season, body.train_start, body.train_end, body.test_start, body.test_end, body.save,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
