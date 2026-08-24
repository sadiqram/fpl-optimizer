"""FastAPI app (M8). Primary interface for live, multi-tenant use once deployed — the CLI
stays the local/dev/backtest entry point (Architecture §4.7)."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from fpl_optimizer.api import scheduler
from fpl_optimizer.api.routers import (
    auth, backtest, evaluate, ingest, plan, players, recommend, squad, train, users,
)

load_dotenv()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    bg_scheduler = scheduler.start()
    try:
        yield
    finally:
        bg_scheduler.shutdown(wait=False)


app = FastAPI(title="fpl-optimizer API", lifespan=lifespan)

_cors_origins = [o.strip() for o in os.environ.get("CORS_ORIGIN", "http://localhost:3000").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in (auth.router, users.router, ingest.router, squad.router, recommend.router,
               plan.router, train.router, backtest.router, evaluate.router, players.router):
    app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}
