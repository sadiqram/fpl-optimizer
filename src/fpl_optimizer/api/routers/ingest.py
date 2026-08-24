from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from fpl_optimizer.api.deps import get_conn, get_current_user
from fpl_optimizer.services import ingest_service

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("")
def trigger_ingest(
    _user: sqlite3.Row = Depends(get_current_user),  # any authenticated account may trigger a refresh — global data, harmless to re-run
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Manual trigger for the same global refresh `scheduler.py` runs daily — a 'Sync now'
    button, and the fallback for whenever the scheduled run hasn't landed yet."""
    return ingest_service.run_ingest(conn)
