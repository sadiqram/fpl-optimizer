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
fpl-optimizer recommend --season 2024-25 --gameweek 20 --model poisson  # predict + optimize: squad, XI, captain
```

`--model` is `naive` (recent scoring average), `poisson` (default — goals/assists/clean-sheets
modelled from xG/xA/team defense, scored by real FPL position rules), or `gbm` (loads a model
already trained and saved via `train --save`). `naive`/`poisson` are permanent baselines to
compare a real model against, not scaffolding (Architecture §4.4).

Every `recommend` run logs its predictions and full recommendation to the DB (FR6, Architecture
P3) — that's what `evaluate` below reads.

```bash
fpl-optimizer squad --season 2026-27                    # pull owned squad, selling prices, free transfers, chips for FPL_TEAM_ID
fpl-optimizer plan --season 2026-27 --gameweek 3 --preset aggressive --risk 0.7
```

`squad` is what `plan` needs and only makes sense for the live season — it fetches your
current squad from the FPL API (as of the last locked gameweek; there's no "current squad"
before then) plus free transfers/chip status, both derived rather than pulled directly since
FPL doesn't expose them as public fields (strategy/squad_state.py). `plan` is `recommend`'s
transfer-aware sibling (M6, FR3/4/8): instead of picking a fresh 15, it plans a decayed 3-5
gameweek horizon against your *owned* squad and commits only this gameweek's transfer +
lineup decision (receding horizon control, PRD §6a.1) — `recommend` stays useful for
wildcards and backtesting, where there's no continuity to respect. `--preset` (`balanced`
default, `safe`/`aggressive`/`value_conscious` — PRD §6a.3) and `--risk` (overrides just the
resolved preset's risk scalar) tune variance/differential/value tradeoffs without changing
the underlying objective (every preset still maximizes expected points). Output also
includes a chip-opportunities section — Bench Boost/Triple Captain/Wildcard/Free Hit, each
with a reasoned expected-points delta, for whichever chips are still available.

```bash
fpl-optimizer train --season 2024-25 --train-start 2 --train-end 27 --test-start 28 --test-end 38 --save
```

Trains the GBM ensemble (P(minutes) x E[points|plays], per Architecture §4.4) on one
gameweek range and reports MAE against both baselines on a held-out range. `--save`
persists the trained model to `data/artifacts/models/`.

```bash
fpl-optimizer backtest --season 2024-25 --start-gameweek 30 --end-gameweek 38 --model gbm
```

Replays a season gameweek-by-gameweek through the exact same pipeline `recommend` uses
(Architecture §4.8, P2 — the only thing that differs is the injected clock), scoring each
gameweek's MAE/RMSE and squad-selection regret (recommended squad's actual points vs. a
hindsight-optimal squad chosen with perfect knowledge of the results). `--model gbm` trains
automatically on every gameweek strictly before `--start-gameweek`, so the backtest window
is never leaked into training.

```bash
fpl-optimizer results --season 2026-27 --gameweek 3    # fetch + store actual per-player results for a live gameweek
fpl-optimizer evaluate --season 2026-27 --gameweek 3   # compare logged predictions against those results
```

`results` is the live-season counterpart to `bootstrap-season` — archive-bootstrapped
seasons already have actual results and don't need it. `evaluate` reads only from the DB
(predictions logged by `recommend`, actuals from `results`/the archive) and reports
MAE/RMSE overall and by position, plus minutes calibration, per logged model — pass `--model`
to filter to one. Each run appends a summary row to `data/artifacts/evaluation/accuracy_log.csv`
(Architecture §4.8, "Live tracking").

## Tests

```bash
pytest
```
