from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from fpl_optimizer.api import schemas
from fpl_optimizer.api.deps import get_conn, get_current_user
from fpl_optimizer.evaluation import accuracy_log
from fpl_optimizer.services import evaluate_service

router = APIRouter(tags=["evaluate"])


@router.post("/evaluate")
def evaluate(
    body: schemas.EvaluateRequest,
    _user: sqlite3.Row = Depends(get_current_user),  # global predictions/outcomes, not per-tenant
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        return evaluate_service.run_evaluate(conn, body.season, body.gameweek, body.model)
    except evaluate_service.EvaluateNotReady as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/accuracy-log")
def get_accuracy_log(_user: sqlite3.Row = Depends(get_current_user)):
    return accuracy_log.read_accuracy_log().to_dict("records")
