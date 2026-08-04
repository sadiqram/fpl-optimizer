#!/usr/bin/env bash
# Daily FPL snapshot ingestion, run via cron. No orchestration framework needed —
# a shell script + cron entry is what Architecture.md §5 calls for at this scale.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$PROJECT_DIR/data/logs"
mkdir -p "$LOG_DIR"

cd "$PROJECT_DIR"
source .venv/bin/activate

{
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  fpl-optimizer ingest
} >> "$LOG_DIR/ingest.log" 2>&1
