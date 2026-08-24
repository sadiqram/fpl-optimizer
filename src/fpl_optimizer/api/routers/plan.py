from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from fpl_optimizer import clock
from fpl_optimizer.api import schemas
from fpl_optimizer.api.deps import get_conn, get_current_user
from fpl_optimizer.services import plan_service, predictor_service
from fpl_optimizer.storage import db
from fpl_optimizer.strategy import risk

router = APIRouter(prefix="/plans", tags=["plan"])


@router.post("")
def create_plan(
    body: schemas.PlanRequest,
    user: sqlite3.Row = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    if body.model not in predictor_service.MODEL_CHOICES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"model must be one of {predictor_service.MODEL_CHOICES}")
    try:
        preset = risk.resolve_preset(body.preset or risk.default_preset_name())
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    if body.risk is not None:
        preset = {**preset, "risk": body.risk}

    the_clock = clock.FixedClock(body.as_of) if body.as_of else clock.SystemClock()
    try:
        predictor = predictor_service.load_predictor(body.model, body.season)
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    try:
        return plan_service.run_plan(
            conn, the_clock, user_id=user["id"], season=body.season, gameweek=body.gameweek,
            model=body.model, predictor=predictor, preset=preset,
        )
    except plan_service.PlanNotReady as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{exc} (sync your squad first.)") from exc


@router.get("")
def list_plans(
    season: str | None = None, gameweek: int | None = None,
    user: sqlite3.Row = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    return [dict(r) for r in db.list_plan_runs(conn, user["id"], season, gameweek)]


@router.get("/{run_id}")
def get_plan(
    run_id: str,
    user: sqlite3.Row = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    row = db.get_plan_run(conn, run_id)
    if row is None or row["user_id"] != user["id"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such plan.")
    return dict(row)
