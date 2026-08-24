from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from fpl_optimizer.api import auth, schemas
from fpl_optimizer.api.deps import get_conn, get_current_user
from fpl_optimizer.storage import db

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=schemas.TokenResponse)
def register(body: schemas.RegisterRequest, conn: sqlite3.Connection = Depends(get_conn)):
    if db.get_user_by_email(conn, body.email) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with that email already exists.")
    try:
        password_hash = auth.hash_password(body.password)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    user_id = db.create_user(conn, email=body.email, password_hash=password_hash, created_at=datetime.now(timezone.utc).isoformat())
    return schemas.TokenResponse(access_token=auth.create_access_token(user_id))


@router.post("/login", response_model=schemas.TokenResponse)
def login(body: schemas.LoginRequest, conn: sqlite3.Connection = Depends(get_conn)):
    user = db.get_user_by_email(conn, body.email)
    if user is None or not auth.verify_password(body.password, user["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password.")
    return schemas.TokenResponse(access_token=auth.create_access_token(user["id"]))


@router.get("/me")
def me(user: sqlite3.Row = Depends(get_current_user)):
    return {"id": user["id"], "email": user["email"], "fpl_team_id": user["fpl_team_id"]}
