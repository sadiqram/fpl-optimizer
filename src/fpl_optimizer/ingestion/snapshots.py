"""Writes timestamped raw fetches to data/raw/{source}/{date}/, then hands off to the storage parse step."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_RAW_DIR = Path("data/raw")


def save_raw(
    source: str,
    name: str,
    payload,
    *,
    when: datetime | None = None,
    raw_dir: Path = DEFAULT_RAW_DIR,
) -> Path:
    """Writes `payload` as immutable JSON to data/raw/{source}/{date}/{name}_{time}.json.

    `when` defaults to the actual fetch time — raw ingestion always uses wall-clock time,
    there's no backtest mode for a live HTTP fetch (Architecture §4.1). The filename
    carries a time component so same-day snapshots (e.g. an explicit pre-deadline pull)
    don't clobber each other.
    """
    when = when or datetime.now(timezone.utc)
    date_dir = raw_dir / source / when.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    path = date_dir / f"{name}_{when.strftime('%H%M%S')}.json"
    path.write_text(json.dumps(payload))
    return path
