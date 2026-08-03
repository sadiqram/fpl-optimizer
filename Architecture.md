# Architecture — `fpl-optimizer`

Technical design for a human-in-the-loop FPL decision-support system. Companion to the PRD; this doc covers *how*, not *what/why-build-it*.

---

## 1. Guiding principles

These drive most of the decisions below, so they're stated up front.

**P1. Prediction and optimization are separate problems.**
Given predicted points, finding the best squad is exactly solvable. Predicting points is irreducibly noisy. Mixing them makes it impossible to tell whether a bad recommendation came from a bad forecast or a bad solver setup.

**P2. Backtest and live must run the same code path.**
The single biggest failure mode in this kind of project is a system that backtests brilliantly and performs poorly live, because the backtest quietly had access to information the live run doesn't. The architecture enforces one code path with a swappable clock, rather than a separate "research" script and "production" script.

**P3. Every intermediate artifact is persisted.**
Not a chain of in-memory function calls. Raw data, features, predictions, and recommendations each get written to disk. This is what makes debugging, auditing, and evaluation possible after the fact — a season from now you need to answer "why did it tell me to sell Salah in GW12?"

**P4. Simplicity over scale.**
One user, one team, ~600 players, 38 gameweeks/season. That's tiny. Any decision justified by "but what if we scale" is almost certainly wrong here.

---

## 2. High-level data flow

```
┌─────────────┐
│  Ingestion  │  FPL API, Understat/FBref → raw snapshots (immutable)
└──────┬──────┘
       ▼
┌─────────────┐
│   Storage   │  SQLite: normalized, point-in-time-correct tables
└──────┬──────┘
       ▼
┌─────────────┐
│  Features   │  Per (player, gameweek) feature rows, as-of a given date
└──────┬──────┘
       ▼
┌─────────────┐
│ Prediction  │  P(minutes) model  ×  E(points | plays) model → E[points]
└──────┬──────┘
       ▼
┌─────────────┐
│ Optimization│  MILP: squad/XI/captain subject to FPL rules
└──────┬──────┘
       ▼
┌─────────────┐
│  Strategy   │  Rolling horizon: transfers, hits, chip timing
└──────┬──────┘
       ▼
┌─────────────┐
│  Interface  │  CLI → human-readable recommendation + rationale
└─────────────┘
       ▼
┌─────────────┐
│ Evaluation  │  Post-gameweek: predicted vs actual, logged forever
└─────────────┘
```

---

## 3. Repo layout

```
fpl-optimizer/
├── src/fpl_optimizer/
│   ├── ingestion/          # API clients, scrapers → raw snapshots
│   │   ├── fpl_api.py
│   │   ├── understat.py
│   │   └── snapshots.py
│   ├── storage/            # schema, migrations, repository functions
│   │   ├── schema.sql
│   │   └── db.py
│   ├── features/           # feature builders (pure functions)
│   │   ├── form.py
│   │   ├── fixtures.py
│   │   ├── minutes.py
│   │   └── build.py
│   ├── models/
│   │   ├── base.py         # Predictor interface
│   │   ├── baseline.py     # naive + Poisson baselines
│   │   ├── minutes_model.py
│   │   ├── points_model.py
│   │   └── ensemble.py     # combines the two into E[points]
│   ├── optimize/
│   │   ├── squad.py        # MILP: best XV given predictions + budget
│   │   ├── lineup.py       # XI, bench order, captain
│   │   └── constraints.py  # FPL rules in one place
│   ├── strategy/
│   │   ├── horizon.py      # multi-GW rolling optimization
│   │   ├── transfers.py    # hit thresholds, FT banking
│   │   └── chips.py        # chip timing scenarios
│   ├── evaluation/
│   │   ├── backtest.py
│   │   └── metrics.py
│   ├── cli.py
│   └── clock.py            # "what time is it" — the injection point for P2
├── config/
│   ├── default.yaml
│   └── models/*.yaml
├── data/                   # gitignored
│   ├── raw/                # immutable snapshots
│   ├── db/fpl.sqlite
│   └── artifacts/          # predictions, recommendations, logs
├── notebooks/              # exploration only, never imported by src/
├── tests/
├── .env.example            # FPL_TEAM_ID etc. — real .env gitignored
└── ARCHITECTURE.md
```

**Why `src/` layout:** forces the package to be installed (`pip install -e .`) rather than relying on relative imports and CWD luck. Prevents the classic "works in the notebook, breaks in the test suite" problem.

**Why notebooks are quarantined:** notebooks are great for exploration and terrible as dependencies — hidden state, non-linear execution order, no tests. Anything that graduates from a notebook gets rewritten into `src/`. One-directional flow only.

---

## 4. Layer-by-layer design

### 4.1 Ingestion

**Responsibility:** fetch from external sources, write immutable raw snapshots, never transform.

**Design:** each fetch writes a timestamped JSON/Parquet file to `data/raw/{source}/{date}/`, *then* a separate parse step loads it into SQLite.

**Justification — why store raw snapshots at all, rather than fetching straight into tables:**
1. **The FPL API is destructive.** The `bootstrap-static` endpoint returns *current* state — prices, ownership, injury flags, chances-of-playing. Once a gameweek passes, you cannot retrieve what a player's injury flag was *before* the deadline. If you don't snapshot it, that feature is gone forever and can never be used in backtesting. This alone justifies the whole snapshot layer.
2. Reparsing is free; refetching may be impossible. If a parsing bug is found six months in, snapshots let you rebuild the DB from scratch.
3. Isolates upstream API changes to one module.

**Snapshot cadence:** daily, plus an explicit pre-deadline snapshot (that's the state the model would actually have had at decision time).

**Rate limiting / politeness:** the FPL API is unauthenticated and free; scrapers (Understat/FBref) need throttling and caching. Cache aggressively — historical data never changes, so re-scraping it is pure waste and unnecessary load on someone else's server.

---

### 4.2 Storage

**Choice: SQLite**, single file, with a `schema.sql` and simple forward-only migrations.

**Justification:** the entire dataset is a few hundred MB at most — 600 players × 38 gameweeks × several seasons. Postgres would add a service to run, a connection to manage, and Docker to the setup, all to solve concurrency and scale problems that a single-user weekly batch job does not have. SQLite is a file, ships with Python, and is trivially backed up by copying it. If this ever becomes multi-user, swapping via SQLAlchemy is a contained change.

**Core tables:**

| Table | Grain | Notes |
|---|---|---|
| `players` | player_id | Slowly-changing: name, position, team |
| `player_gw_stats` | player_id × gameweek | Actual outcomes: points, minutes, goals, assists, bonus |
| `player_snapshots` | player_id × snapshot_date | **Point-in-time**: price, ownership, injury flag, chance_of_playing |
| `fixtures` | fixture_id | Teams, kickoff time, gameweek, home/away, FDR |
| `team_stats` | team_id × gameweek | Aggregate attacking/defensive strength |
| `understat_player_gw` | player_id × gameweek | xG, xA, shots, key passes |
| `predictions` | model_version × player_id × gameweek × run_date | Every prediction ever made |
| `recommendations` | run_id | Full recommendation output + rationale, as JSON |

**The critical design point — `player_snapshots` is separate from `player_gw_stats`:**
Outcomes (points scored) are immutable facts attached to a gameweek. Attributes (price, injury status, ownership) are *time-varying and revised*. Collapsing them into one table is how leakage sneaks in — you'd end up training on "player was flagged injured" data that was only known *after* the deadline you're pretending to predict from. Keeping snapshots separate and keyed by observation date makes as-of queries the natural default rather than a thing you have to remember to do.

**ID mapping:** FPL and Understat use different player IDs, and name matching is genuinely messy (accents, initials, transfers mid-season). A dedicated `player_id_map` table with a manual override file is the pragmatic solution — accept that ~5% needs human correction rather than over-engineering a fuzzy matcher.

---

### 4.3 Features

**Design:** pure functions, `(as_of_date, gameweek) → DataFrame`, no hidden state, no DB writes.

**The central constraint — every feature builder takes `as_of_date` and may only read data observable at that date.** This is enforced in the repository layer: query helpers require an as-of parameter rather than accepting it optionally.

**Justification:** leakage is the defining failure mode of sports prediction, and it's *silent* — the model just looks great and you feel clever. Making as-of a required argument at the data-access boundary means leakage requires actively working around the API rather than merely forgetting a filter. Test-only enforcement would catch it too late and only where tests exist.

**Feature families:**
- *Form*: rolling 3/5/10-GW points, xGI, minutes — decayed weighting
- *Fixture*: opponent strength, home/away, congestion (games in N days)
- *Role*: set-piece duty, penalty order, position in team's attacking hierarchy
- *Minutes*: start rate, sub patterns, injury flag, chance-of-playing %
- *Team context*: team xG/xGA trend, expected clean-sheet probability

**Materialization:** feature rows written to `data/artifacts/features/{as_of}/` as Parquet. Feature building is the slowest step and gets rerun constantly during model iteration; caching it turns a multi-minute loop into seconds. Parquet over CSV for type preservation and columnar reads.

---

### 4.4 Prediction

**Two-model split** (as discussed):

```
E[points] = P(plays ≥ 60min) × E[points | plays] + P(plays 1–59min) × E[points | cameo]
```

**Justification:** these are different problems with different drivers, different data, and different error profiles. Minutes are driven by team news, rotation, and injury — largely categorical, and the single biggest source of catastrophic error (a predicted 6-point haul from a player who doesn't start is a total loss). Points-if-playing is driven by underlying quality and matchup. A single model predicting raw points must implicitly learn both and gives you no way to see which part is wrong. Splitting them means when a recommendation misfires you can attribute it: bad minutes call, or bad quality call.

It also makes the output honest — a fit-and-firing premium and a rotation-risk punt can have the same E[points] for very different reasons, and the split surfaces that distinction to the human reviewer.

**Model interface** — every predictor implements the same contract:

```python
class Predictor(Protocol):
    def fit(self, features: pd.DataFrame, targets: pd.DataFrame) -> None: ...
    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        """→ columns: player_id, gameweek, expected_points, p_start, std_dev"""
```

**Justification:** a stable interface is what lets the naive baseline, the Poisson model, and the gradient-boosted model be swapped by config and compared apples-to-apples. The baseline isn't scaffolding to be deleted — it's a permanent fixture. Without a live baseline to beat, "the model is good" is unfalsifiable.

**Returning `std_dev`, not just a point estimate:** downstream, captaincy and chip decisions care about variance, not just the mean. A 6.0-expected-points nailed-on starter and a 6.0-expected-points explosive differential are not the same captaincy choice, and the optimizer can't know that if the model only emits a mean. Cheap to produce, expensive to retrofit later.

**Model versioning:** every prediction row records `model_version`. Non-negotiable, because a season of prediction logs is worthless if you can't tell which model produced which row.

---

### 4.5 Optimization

**Choice: Mixed-Integer Linear Programming via PuLP (CBC solver).**

**Justification:** the squad problem is naturally linear — maximize a weighted sum of binary selection variables under linear budget/count constraints. MILP gives the *provably* optimal answer in well under a second at this problem size. Genetic algorithms and greedy heuristics are common in hobbyist FPL projects and are strictly worse here: slower, approximate, and non-deterministic, solving a problem that has an exact method. PuLP over OR-Tools purely for readability — constraints written in PuLP look like the rules they encode, and the model is small enough that solver performance is irrelevant.

**Constraints live in exactly one module** (`optimize/constraints.py`) and are shared by the single-GW solver, the multi-GW horizon solver, and the backtester. FPL rules change between seasons (the free-transfer banking cap changed recently); one definition means one place to update.

**Explicitly modelled:**
- 15 players: 2 GK, 5 DEF, 5 MID, 3 FWD
- Budget (using *selling* price for owned players, not current price)
- Max 3 per real team
- Valid XI formation, captain/vice as binary variables
- Bench order as a weighted objective term (bench points only matter probabilistically)

**On selling price:** FPL sells owned players at purchase price plus half of any rise. A naive optimizer using current market price will hallucinate budget that doesn't exist and produce illegal transfers. This is a rules detail, not a modelling detail, and it belongs in constraints where it can be tested directly.

---

### 4.6 Strategy

**Choice: rolling-horizon optimization over a configurable lookahead (default 5 GWs), with decayed confidence on distant gameweeks.**

Each run solves for the whole horizon but only *commits* the current gameweek's decision, then re-solves next week with fresh data.

**Justification for rolling horizon over reinforcement learning:** a season is ~38 decisions. RL needs orders of magnitude more episodes than that to learn a policy, the reward (final rank) is extremely delayed and noisy, and a learned policy would be unexplainable — which conflicts with the requirement that a human review and approve each recommendation. Rolling horizon captures the main strategic effects (banking transfers, planning around fixture swings) with an explainable, tunable, debuggable method. Committing only the first decision is what makes it robust: distant predictions are unreliable, and they're allowed to inform the current move without being acted on directly.

**Decay factor on future gameweeks:** predictions 5 weeks out are much weaker than next week's (unknown injuries, form shifts, unannounced rotation). Weighting them equally would let speculative future value justify a costly transfer today.

**Chips as scenario comparison, not as optimizer variables:** for each chip, run the horizon solver with and without it active at each candidate gameweek, and compare totals. Chips are one-shot, discrete, and rare — four decisions per season. Folding them into the MILP adds combinatorial complexity for something a handful of scenario runs answers directly, and scenario output is far easier for a human to sanity-check ("Bench Boost in GW29 is worth +9 vs GW25's +4").

---

### 4.7 Interface

**Choice: CLI, outputting a human-readable summary plus a persisted JSON artifact.**

```bash
fpl-optimizer recommend --gameweek 12
fpl-optimizer backtest --season 2023-24
fpl-optimizer evaluate --gameweek 11
```

**Justification:** the deliverable is one decision per week that a human reads and acts on. A web UI is substantial work that adds no decision quality. The JSON artifact means a UI can be added later without touching any logic — the recommendation is already a data structure, not print statements.

**Output must include rationale, not just conclusions:** which players changed, expected point delta, whether a hit clears its threshold, and the key features driving each prediction. Per NFR3, an unexplained recommendation can't be meaningfully approved — the human's job in the loop is to catch what the model can't see (a press-conference comment, a suspension), and they can only do that if they can see the model's reasoning.

---

### 4.8 Evaluation

**Two distinct mechanisms, deliberately separate:**

**Backtest** (`evaluation/backtest.py`): replay a historical season by advancing the injected clock gameweek by gameweek, running the *same* recommendation pipeline live uses, and scoring the result.

**Live tracking** (`evaluation/metrics.py`): after each real gameweek resolves, join logged predictions to actual outcomes and append to a running accuracy log.

**Justification for the injected clock (`clock.py`):** this is the mechanism behind P2. The backtester differs from the live run in exactly one respect — what "now" returns. Every as-of query, every feature build, every model input flows from that. A separate backtest script that reimplements the pipeline would inevitably drift from live behaviour and quietly reintroduce leakage. One code path, one clock.

**Metrics tracked:** MAE/RMSE by position, minutes-model calibration, points-model calibration, recommendation-level regret (points from recommended transfer vs. best possible in hindsight vs. holding), and season-level rank.

**Recommendation-level regret matters more than raw MAE.** The system's job isn't to forecast every player accurately — it's to pick the right transfer. A model can have mediocre MAE across all 600 players while consistently ranking the top options correctly, and that's the model you want.

---

## 5. Cross-cutting decisions

**Config in YAML, secrets in `.env`.** Model hyperparameters, horizon length, decay factors, and hit thresholds are all things you'll tune constantly — in config, not hardcoded, so experiments are reproducible from a config diff. `FPL_TEAM_ID` goes in gitignored `.env` with a committed `.env.example`, which keeps the private→public repo flip a non-event.

**Determinism.** Fixed random seeds, pinned dependencies, `model_version` on every prediction row. Per NFR4, if the same inputs produce different recommendations on rerun you cannot debug anything and cannot trust any evaluation.

**Testing priority — highest value first:**
1. *Constraint tests* — every FPL rule has a test asserting invalid squads are rejected. Bugs here produce recommendations that are simply illegal.
2. *Leakage tests* — assert feature builders raise if asked for data after `as_of_date`. The failure they prevent is silent and expensive.
3. *Pipeline smoke test* — end-to-end on a fixture season.
4. Model accuracy is monitored, not unit-tested — "is the model good" is an evaluation question, not a pass/fail assertion.

**No orchestration framework.** A shell script or cron entry calling the CLI weekly is sufficient. Airflow/Prefect solve dependency management and retries across many interdependent jobs; this is one job, once a week.

---

## 6. Known weak points

Stated plainly so they're not discovered as surprises:

| Weakness | Why accepted |
|---|---|
| Injury/rotation data is the weakest input and the largest error source | No free data source solves it; mitigated by surfacing `p_start` explicitly so the human can override |
| Understat/FBref scraping is fragile to site changes | Isolated to ingestion; snapshots mean a break doesn't lose history |
| ID mapping needs periodic manual correction | Fuzzy matching alone can't handle transfers and name variants; manual override file is the honest fix |
| Price-change prediction not modelled in v1 | Affects team value slowly; large added complexity for small point impact |
| Optimizes absolute points, not mini-league relative position | Different objective (variance-seeking when behind); deferred to v2 |