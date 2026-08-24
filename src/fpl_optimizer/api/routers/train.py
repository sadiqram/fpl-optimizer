from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from fpl_optimizer.api import schemas
from fpl_optimizer.api.deps import get_conn, require_admin
from fpl_optimizer.services import train_service

router = APIRouter(prefix="/train", tags=["train"])


@router.post("")
def train(
    body: schemas.TrainRequest,
    _user: sqlite3.Row = Depends(require_admin),  # global model artifact, not per-tenant — admin-only
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        return train_service.run_train(
            conn, body.season, body.train_start, body.train_end, body.test_start, body.test_end, body.save,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
