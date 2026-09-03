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

### Data pipeline

| Command | What it does |
|---|---|
| `fpl-optimizer ingest` | Fetch current FPL state, write raw snapshots + parse into `data/db/fpl.sqlite` |
| `fpl-optimizer bootstrap-season --season 2024-25` | Load a past season from the vaastav archive |
| `fpl-optimizer understat --season 2024-25 --max-players 5` | Fetch live Understat data, match to FPL players |
| `fpl-optimizer features --season 2024-25 --gameweek 20` | Build + materialize features for one (season, gameweek) |

### Recommendations

```bash
fpl-optimizer recommend --season 2024-25 --gameweek 20 --model poisson  # predict + optimize: squad, XI, captain
```

`--model` is `naive` (recent scoring average), `poisson` (default — goals/assists/clean-sheets modelled from xG/xA/team defense, real FPL position rules), or `gbm` (a model already trained via `train --save`). `naive`/`poisson` are permanent baselines, not scaffolding (Architecture §4.4). Every run logs its predictions to the DB for `evaluate` to read later (FR6, Architecture P3).

```bash
fpl-optimizer squad --season 2026-27                    # pull owned squad, selling prices, free transfers, chips
fpl-optimizer plan --season 2026-27 --gameweek 3 --preset aggressive --risk 0.7
```

`squad` fetches your current squad/free-transfers/chip status from the FPL API — only meaningful for the live season. `plan` is `recommend`'s transfer-aware sibling: it plans a decayed 3-5 gameweek horizon against your *owned* squad and commits only this gameweek's transfer + lineup decision (receding horizon control, PRD §6a.1). `--preset` (`balanced` default, `safe`/`aggressive`/`value_conscious`) and `--risk` tune variance/differential/value tradeoffs — see PRD §6a.3.

### Training & backtesting

```bash
fpl-optimizer train --season 2024-25 --train-start 2 --train-end 27 --test-start 28 --test-end 38 --save
fpl-optimizer backtest --season 2024-25 --start-gameweek 30 --end-gameweek 38 --model gbm
```

`train` fits the GBM ensemble (P(minutes) × E[points|plays], Architecture §4.4) and reports MAE against both baselines on a held-out range; `--save` persists it to `data/artifacts/models/`. `backtest` replays a season gameweek-by-gameweek through the same pipeline `recommend` uses (Architecture §4.8), scoring MAE/RMSE and squad-selection regret against a hindsight-optimal squad. With `--model gbm` it trains automatically on every gameweek strictly before `--start-gameweek`, so the window is never leaked into training.

### Evaluation

```bash
fpl-optimizer results --season 2026-27 --gameweek 3    # fetch + store actual per-player results for a live gameweek
fpl-optimizer evaluate --season 2026-27 --gameweek 3   # compare logged predictions against those results
```

`results` is the live-season counterpart to `bootstrap-season` (archive seasons already have actuals). `evaluate` reports MAE/RMSE overall and by position plus minutes calibration per logged model (`--model` to filter), and appends a summary row to `data/artifacts/evaluation/accuracy_log.csv`.

## Web app (M8)

A FastAPI backend + Next.js frontend, primary interface for live/multi-tenant use once deployed (Architecture §4.7). The CLI stays the local/dev/backtest entry point — both call the same `src/fpl_optimizer/services/` layer, so behavior never diverges.

**One-time: migrate an existing DB to multi-tenant** (adds `users`/`plan_runs`, threads `user_id` through the owned-squad tables; safe to re-run):

```bash
python scripts/migrate_to_multitenant.py --email you@example.com --password '...'
```

Then set `FPL_ADMIN_EMAIL` in `.env` to that email — the CLI resolves which account it acts as from that; the web app resolves it from a JWT instead. Also set `ADMIN_EMAILS` (comma-separated) to the account(s) allowed to call `POST /train`, since that endpoint overwrites the single shared model artifact every tenant's `/recommendations` and `/plans` load.

**Run locally:**

```bash
# backend — from the repo root, same venv as the CLI
JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))") uvicorn fpl_optimizer.api.main:app --reload --port 8000

# frontend — separate terminal
cd frontend
cp .env.example .env.local   # BACKEND_URL=http://localhost:8000 by default
npm install
npm run dev
```

Open `http://localhost:3000`, register an account, connect an FPL team ID (Settings), then sync your squad.

**Deploy:** `Dockerfile` + `fly.toml` build the backend for Fly.io, already launched as `fpl-optimizer-api` in `iad`; `.github/workflows/fly-deploy.yml` redeploys it on every push to `main` via `flyctl deploy --remote-only`, gated on the `FLY_API_TOKEN` repo secret.

One-time setup for a fresh app:

```bash
fly volumes create fpl_data --size 1 --region iad
fly secrets set JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
fly secrets set FPL_TEAM_ID=<your team id> CORS_ORIGIN=https://<your-vercel-domain>
```

The backend runs one machine always-on (not scale-to-zero) so `api/scheduler.py`'s daily refresh doesn't get silently skipped. The frontend deploys to Vercel with `BACKEND_URL` pointed at the deployed backend — that side hasn't been run against a real Vercel account yet.

## Tests

```bash
pytest
```
