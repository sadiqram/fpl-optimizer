# fpl-optimizer

A human-in-the-loop decision-support system for Fantasy Premier League: predicts player points, solves for the optimal squad/transfers/captain via MILP, and outputs a weekly recommendation for a human to review and act on. No auto-execution — FPL has no write API, and the design keeps a human in the loop by choice as well as by constraint.

See [`fpl_ai_prd.md`](fpl_ai_prd.md) for the *what/why* and [`Architecture.md`](Architecture.md) for the *how*.

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
fpl-optimizer recommend --gameweek 12
fpl-optimizer backtest --season 2023-24
fpl-optimizer evaluate --gameweek 11
```

(Not yet implemented — see milestones in the PRD.)

## Tests

```bash
pytest
```
