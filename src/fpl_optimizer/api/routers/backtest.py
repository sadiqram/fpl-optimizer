from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from fpl_optimizer.api import schemas
from fpl_optimizer.api.deps import get_conn, get_current_user
from fpl_optimizer.services import backtest_service

router = APIRouter(prefix="/backtest", tags=["backtest"])


@router.post("")
def run_backtest(
    body: schemas.BacktestRequest,
    _user: sqlite3.Row = Depends(get_current_user),  # replays archived data, not per-tenant
    conn: sqlite3.Connection = Depends(get_conn),
):
    results = backtest_service.run_backtest(conn, body.season, body.start_gameweek, body.end_gameweek, body.model)
    return results.to_dict("records")
