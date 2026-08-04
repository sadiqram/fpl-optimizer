"""'What time is it' — the injection point shared by live and backtest runs (Architecture §4.8, P2).

The backtester and the live run must differ in exactly one respect: what `clock.today()`
returns. Every as-of query, every feature build, every model input flows from that single
call — swap `SystemClock` for a `FixedClock` and the identical pipeline code (strategy.horizon)
replays a past gameweek instead of predicting the next one. A separate backtest script that
reimplements the pipeline would inevitably drift from live behaviour and quietly reintroduce
leakage (Architecture §1, P2) — this is the mechanism that makes "one code path" possible
rather than aspirational.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def today(self) -> str:
        """ISO date string ('YYYY-MM-DD') — the as-of date the rest of the pipeline treats
        as 'now'."""
        ...


class SystemClock:
    """Real wall-clock time, in UTC. What a live run uses."""

    def today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class FixedClock:
    """A single, unmoving date — what a backtest uses while it's replaying one gameweek.
    The backtester advances through a season by constructing a new FixedClock per
    gameweek, not by mutating one; a clock is a value, not something with hidden state
    accumulating across calls."""

    def __init__(self, date: str):
        self._date = date

    def today(self) -> str:
        return self._date
