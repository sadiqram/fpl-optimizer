#!/usr/bin/env bash
# Weekly recommendation run, run via cron (PRD M5: "use it for real"). No orchestration
# framework needed — a shell script + cron entry is what Architecture.md §5 calls for.
# Not installed into crontab by this repo, same as daily_ingest.sh — add it yourself, e.g.
# a few hours ahead of the usual Friday/Saturday deadline (NFR1).
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$PROJECT_DIR/data/logs"
mkdir -p "$LOG_DIR"

cd "$PROJECT_DIR"
source .venv/bin/activate

{
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

  # The CLI takes an explicit --season/--gameweek (Architecture §4.3: as-of is never
  # implicit) — this resolves "current" from the live bootstrap-static the same way
  # cli.infer_current_season does, rather than guessing from wall-clock dates.
  read -r SEASON GAMEWEEK <<< "$(python3 -c '
from fpl_optimizer.cli import infer_current_season
from fpl_optimizer.ingestion.fpl_api import FPLClient

bootstrap = FPLClient().bootstrap_static()
season = infer_current_season(bootstrap)
events = bootstrap["events"]
target = next((e for e in events if e["is_next"]), next((e for e in events if e["is_current"]), events[-1]))
print(season, target["id"])
')"

  echo "Recommending ${SEASON} GW${GAMEWEEK}..."
  fpl-optimizer recommend --season "$SEASON" --gameweek "$GAMEWEEK"
} >> "$LOG_DIR/recommend.log" 2>&1
