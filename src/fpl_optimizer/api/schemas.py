"""Pydantic request bodies. Responses are returned as the plain dicts the service layer
already produces (services/*.py) rather than re-declared here — FastAPI JSON-encodes them
directly, and duplicating the shape in a response_model would just be a second place for it
to drift from what the service actually returns.
"""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UpdateFplTeamIdRequest(BaseModel):
    fpl_team_id: int


class SyncSquadRequest(BaseModel):
    season: str


class RecommendRequest(BaseModel):
    season: str
    gameweek: int
    model: str = "poisson"
    as_of: str | None = None


class PlanRequest(BaseModel):
    season: str
    gameweek: int
    model: str = "poisson"
    as_of: str | None = None
    preset: str | None = None
    risk: float | None = None


class TrainRequest(BaseModel):
    season: str
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    save: bool = False


class BacktestRequest(BaseModel):
    season: str
    start_gameweek: int
    end_gameweek: int
    model: str = "poisson"


class EvaluateRequest(BaseModel):
    season: str
    gameweek: int
    model: str | None = None
