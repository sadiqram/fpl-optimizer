# fpl-optimizer

A human-in-the-loop decision-support system for Fantasy Premier League: predicts player points, solves for the optimal squad/transfers/captain via MILP, and outputs a weekly recommendation for a human to review and act on. No auto-execution — FPL has no write API, and the design keeps a human in the loop by choice as well as by constraint.

See [`fpl_ai_prd.md`](fpl_ai_prd.md) for the *what/why*, [`Architecture.md`](Architecture.md) for the *how*, and [`docs/error_log.md`](docs/error_log.md) for real bugs found along the way and how they were fixed.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # then fill in FPL_TEAM_ID
```

Dependency versions are pinned in `requirements.lock.txt` (regenerate with `pip freeze | grep -v '^-e ' > requirements.lock.txt` after changing `pyproject.toml` deps).

## Usage

```bash
fpl-optimizer ingest                                    # fetch current FPL state, write raw snapshots + parse into data/db/fpl.sqlite
fpl-optimizer bootstrap-season --season 2024-25          # load a past season from the vaastav archive
fpl-optimizer understat --season 2024-25 --max-players 5 # fetch live Understat data, match to FPL players
fpl-optimizer features --season 2024-25 --gameweek 20    # build + materialize features for one (season, gameweek)
```

Planned, not yet implemented (see milestones in the PRD):

```bash
fpl-optimizer recommend --gameweek 12
fpl-optimizer backtest --season 2023-24
fpl-optimizer evaluate --gameweek 11
```

## Tests

```bash
pytest
```
