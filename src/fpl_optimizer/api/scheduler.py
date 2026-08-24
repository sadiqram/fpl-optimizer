"""Daily background refresh — the actual fix for the original problem (README/error_log
context: WSL's cron only runs while the WSL VM happens to be awake, so the daily ingest
silently stopped firing for two weeks). An always-on deployed process doesn't have that
failure mode; APScheduler's in-process `BackgroundScheduler` is enough at this scale
(Architecture §5 "no orchestration framework" — this is one more job, still not a fleet).
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from fpl_optimizer.api.deps import DB_PATH
from fpl_optimizer.services import ingest_service, squad_service
from fpl_optimizer.storage import db

logger = logging.getLogger("fpl_optimizer.api.scheduler")


def daily_refresh() -> None:
    conn = db.connect(DB_PATH)
    try:
        summary = ingest_service.run_ingest(conn)
        season = summary["season"]
        logger.info("Daily ingest: %s players, %s fixtures", summary["players"], summary["fixtures"])

        users = conn.execute("SELECT id, fpl_team_id FROM users WHERE fpl_team_id IS NOT NULL").fetchall()
        for user in users:
            try:
                squad_service.sync_squad(conn, user_id=user["id"], team_id=user["fpl_team_id"], season=season)
            except Exception:
                # NFR2: one account's sync failing (season not started, FPL API hiccup for
                # that team) must not block the global refresh or the other accounts.
                logger.exception("Daily squad sync failed for user_id=%s", user["id"])
    finally:
        conn.close()


def start() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(daily_refresh, "cron", hour=6, minute=0, id="daily_refresh")
    scheduler.start()
    return scheduler
