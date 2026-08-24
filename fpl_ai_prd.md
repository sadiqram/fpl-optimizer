# PRD: FPL Decision-Support AI

## 1. Problem Statement

Fantasy Premier League (FPL) managers make weekly decisions — transfers, captaincy, chip usage, starting XI — under uncertainty, budget constraints, and incomplete information (injuries, rotation risk, fixture difficulty). These decisions are high-frequency (weekly, 38 gameweeks/season), high-variance (football outcomes are noisy), and cognitively demanding to do well consistently.

**Core problem:** Manually tracking form, underlying stats (xG/xA), fixtures, and price changes across 600+ players every week is time-consuming and error-prone, and human decision-making is subject to recency bias, favorite-player bias, and emotional overreaction to single results.

## 2. Goal

Build a system that recommends, each gameweek, the optimal:
- Transfer(s) to make (or none)
- Starting XI and bench order
- Captain and vice-captain
- Chip usage (Wildcard, Free Hit, Bench Boost, Triple Captain) timing

...based on data-driven point predictions, subject to FPL's rules and the user's current squad/budget/transfer state.

## 3. Non-Goals (v1)

- **Not** fully autonomous execution — no auto-submitting transfers via FPL's private API. Human stays in the loop to review and confirm. (Revisit post-v1 if trust in the system is established.)
- ~~Not a multi-user product/SaaS — single user (you), single team, to start.~~ **Revised at
  M8**: the web app (Architecture §4.7) is multi-tenant — each account connects its own FPL
  team and gets its own squad/plan/recommendation history. What's still true from the
  original non-goal: this isn't opponent-aware or league-strategy-aware across accounts (see
  §6a.2/§6a.3 below, unchanged), and it's still not a hosted SaaS with billing, support, or
  onboarding beyond a login screen — multi-tenant here means "more than one person can use
  it," not "productized."
- **Not** predicting live in-match events (bonus points, red cards) in real time — weekly cadence, not live.
- **Not** full mini-league game theory (modelling specific rivals' squads and playing directly against them). v1 handles league context only through a single risk parameter (see §6a), not opponent-by-opponent strategy.
- **Not** automatic inference of risk appetite from league standings in v1 — the parameter exists and the interface supports it, but v1 ships manual-only. See §6a.2.
- **Not** optimizing for team value as a standalone objective — see §6a.3 for why this is deliberately excluded.

## 4. Target User

You — a knowledgeable FPL player who wants a tool to remove grunt work (data gathering, comparison) and reduce bias, while retaining final say on decisions.

## 5. Success Metrics

Since FPL is high-variance, single-gameweek outcomes are not a fair test. Evaluate over a full season (or backtested historical seasons):

| Metric | Target / Baseline |
|---|---|
| **Overall rank** | Top 10% of all FPL managers (~top 800k → top 80k) as a first milestone |
| **Points vs. "template team"** | Beat the average score of teams with typical/high ownership picks |
| **Prediction accuracy (MAE)** | Beat a naive baseline (e.g., "player scores their season average") on held-out gameweeks |
| **Backtested performance** | Outperform actual human-managed team score in ≥2 of last 3 historical seasons (retrospective simulation) |
| **Calibration** | Predicted points vs. actual points should track reasonably 1:1 in aggregate, not systematically over/under-predict |

Secondary/qualitative: time saved per week, confidence in decisions, reduced "gut-feel panic transfers."

## 6. Key Requirements

### Functional
- **FR1:** Pull current squad, budget, free transfers, and chip status from the FPL API for the user's team ID.
- **FR2:** Generate predicted points per player for the upcoming gameweek and a decaying-confidence lookahead window (see §6a.1).
- **FR3:** Given predictions + current squad state, output the optimal transfer decision(s), including a "hold, don't transfer" recommendation when no transfer clears the -4 hit threshold.
- **FR4:** Recommend starting XI, bench order, captain, and vice-captain from the resulting squad.
- **FR5:** Flag chip usage opportunities (e.g., "this looks like a strong Bench Boost week") with reasoning.
- **FR6:** Log every prediction and every recommendation made, with a way to compare predicted vs. actual after the gameweek resolves.
- **FR7:** Present output in a reviewable format (not auto-executed) — e.g., a summary a human reads and approves.
- **FR8:** Re-plan the full lookahead window every gameweek using latest data; execute only the current-gameweek decision (§6a.1).
- **FR9:** Expose a single risk parameter, settable manually per gameweek. The interface must accommodate a future auto-inference writer without changing the optimizer (§6a.2).
- **FR10:** Provide named presets that configure risk and secondary weightings, without changing the underlying objective (§6a.3).

### Non-Functional
- **NFR1:** Weekly recommendation must be available before the gameweek deadline (typically Friday/Saturday) with enough lead time to review — target: runnable at least 24 hours before deadline.
- **NFR2:** System should degrade gracefully with missing data (e.g., no confirmed team news yet) rather than fail outright.
- **NFR3:** All predictions and recommendations must be explainable — able to show *why* (which features/stats) drove a recommendation, not just a black-box number.
- **NFR4:** Reproducible: rerunning on the same data should give the same recommendation (no unexplained nondeterminism).

## 6a. Core Design Decisions

These were open questions in the first draft and are now resolved. Recorded here with rationale, since they shape the architecture.

### 6a.1 Rolling horizon: plan 3–5 gameweeks, re-plan weekly, execute only week 1

The optimizer plans over a 3–5 gameweek window, but **only the current gameweek's decision is executed**. Each week the plan is discarded and rebuilt from scratch with the latest results, injuries, and price data.

This is *receding horizon control* (a.k.a. model predictive control) — the standard approach for sequential decisions under uncertainty where information improves over time.

**Rationale:** The lookahead's job is not to produce a multi-week plan you follow. It's a **tiebreaker** that prevents decisions which look good this week but are bad next week — buying into a brutal fixture run, or burning a transfer right before a blank gameweek. Committing to weeks 2–5 would mean acting on predictions we know decay quickly.

Implementation notes:
- **Decay weighting** applied to future gameweeks (starting point: `1.0, 0.8, 0.6, 0.45, 0.3`), encoding declining confidence rather than pretending all weeks are equally knowable. Exact curve to be tuned via backtesting.
- **Log each week's full plan** and compare against what was actually done the following week. If week-2 plans rarely survive contact with reality, that's evidence the horizon is too long or the decay curve too flat — a cheap, built-in diagnostic.

### 6a.2 Risk appetite: one scalar, manual in v1, auto-infer deferred

The optimizer takes a **single risk parameter**. The interface supports multiple writers:

- **Manual** — set directly per gameweek by the user. **This is the only writer in v1.**
- **Auto-infer** — computed from league context. **Deferred to post-backtest-harness (see §10, M6).**

The optimizer only ever sees one number and does not know or care which mode produced it. This is precisely what makes deferring auto-infer cheap: the preset/objective architecture is unchanged, and adding the second writer later requires no optimizer changes.

**Why auto-infer is deferred rather than shipped:**
1. **It can't be validated yet.** Calibrating a gap-to-risk mapping requires the backtesting harness (M4). Shipping it on day one means shipping a number nobody can check.
2. **It's a new ingestion requirement** — pulling a specific mini-league's standings and rival scores — not present in the current data layer design. It expands milestone 1's scope for no v1 benefit.
3. **It's the component most likely to overfit.** Small private leagues offer very few historical `(gap, GW remaining, swing)` observations to calibrate against.

This follows the project's simplicity-over-scale principle: the whole preset architecture ships intact, without betting correctness on a calibration that can't yet be tested.

**When built, auto-infer must use more than league position.** Being 40 points behind in GW5 is recoverable with normal play; 40 behind in GW35 needs a hail mary. Inference inputs:
- Points gap to target
- **Gameweeks remaining**
- Gap volatility (how much positions in this league typically swing week to week)

Rough shape: risk appetite scales with `gap / (expected points swing available in remaining gameweeks)`.

**Auto-infer takes an explicit target league ID.** Users are typically in several leagues (work, friends, overall) whose optimal strategies contradict each other. The system does not guess — it optimizes against one named target, defaulting to overall rank if unset.

### 6a.3 Presets are risk/horizon profiles, not competing objectives

**Every preset maximizes expected points.** What varies is the risk parameter and secondary weightings.

| Preset | Behaviour | When to use |
|---|---|---|
| **Balanced** (default) | Max xPts, medium horizon, neutral risk | Normal weeks |
| **Safe** | Max xPts, penalize variance and rotation risk, favour nailed starters | Protecting a lead; late season |
| **Aggressive** | Max xPts, reward differentials/low ownership, tolerate variance | Chasing in a mini-league |
| **Value-conscious** | Max xPts, mild bonus for budget flexibility and price-rise candidates | Early season, when squad value still compounds |

**Why "maximize team value" was rejected as a preset:** Team value is a *resource*, not a goal, and optimizing for it directly loses points. The value-maximizing strategy is buying cheap players about to rise and churning transfers to chase price movements — selecting on price trajectory rather than returns. Compounding the problem, only 50% of a player's rise is recouped on sale, so £5m of paper value is £2.5m of usable budget, worth perhaps a couple of points across a season — while the transfers and hits spent chasing it can cost far more.

Value therefore enters as a **soft tiebreaker inside the points objective**: when two transfers are close in expected points, prefer the one preserving budget flexibility. That is what the *Value-conscious* preset encodes.

Consequence: §6a.2 and §6a.3 collapse into one mechanism — presets are named configurations of the risk parameter plus a few secondary weights. One objective, tunable preferences.

### 6a.4 Historical data provenance: trust features selectively, not backtests wholesale

**The gap:** The snapshot mechanism (Architecture §4.1) gives leakage-free point-in-time data *from the day we start running it*. But M4 backtesting needs history that predates the project, and the FPL API exposes only current state. Pre-launch backtests must therefore rely on a third-party archive (primary candidate: `vaastav/Fantasy-Premier-League`), whose point-in-time fidelity is whatever cadence that project happened to capture.

**This is a confirmed risk, not a theoretical one.** The archive's own data dictionary flags that its `xP` column is scraped from FPL's `ep_this` field *after* each gameweek ends, that FPL's update cadence for the field is undocumented, and that scraped values may reflect post-match rather than pre-deadline information — recommending the column be shifted or dropped for ML use. The README also indicates a reduced update cadence (on the order of a few major updates per season rather than continuous ingestion), which if current means volatile fields have little point-in-time fidelity at all. **Verify cadence directly before depending on it.**

**Decision — partition features by leakage exposure rather than accepting or rejecting backtests wholesale:**

| Class | Examples | Treatment |
|---|---|---|
| **Outcome-derived** | Minutes played, goals, assists, clean sheets, xG/xA from completed matches | **Trusted.** These are facts recorded after the match; their values don't change retroactively. No leakage risk as historical features. |
| **State-at-deadline** | Injury flags, `chance_of_playing`, price, ownership %, `xP` | **Approximate.** Archive value may differ from what was observable at the deadline. Usable, but results depending on them are provisional. |

Consequences:
- Predictor backtests built primarily on outcome-derived features are **trustworthy now**.
- Backtests whose conclusions hinge on state-at-deadline features are **provisional** until validated against our own accumulated snapshots.
- `xP` is **excluded** as a feature by default, per the archive's own guidance.
- Once ~1 season of own snapshots exists, measure archive-vs-snapshot divergence on the volatile fields to quantify how provisional the early backtests actually were.

**Secondary sources to evaluate at M1:**
- The archive already ships an `understat/` directory with xG data *and* an Understat-ID→FPL-ID mapping. The ID mapping is the genuinely fiddly part of Understat integration — this may remove the need for a hand-rolled scraper.
- `olbauday/FPL-Core-Insights` — an alternative dataset fusing FPL API data with match stats and ClubElo ratings, covering recent seasons. Worth evaluating as a provenance cross-check.

## 7. Constraints

- FPL's official API is public/unauthenticated for read access, but has **no official write/transfer API** — this is a hard constraint that shapes the "human-in-the-loop" scope decision, not just a preference.
- Data on injuries/rotation is inherently incomplete and lags real-world news — the system will sometimes be wrong for reasons no model could fix (a manager's press-conference comment 2 hours before deadline).
- **The FPL API exposes only current state, not history.** Point-in-time historical data for pre-launch seasons does not exist and cannot be reconstructed from the official API — it must come from third-party archives of unverified snapshot fidelity. See §6a.4.
- No real budget for paid data feeds in v1 — rely on free sources (official API, Understat/FBref scraping).
- Single developer/user — architecture should favor simplicity and iteration speed over scalability.

## 8. Assumptions

- You'll manually execute the recommended transfers/captaincy in the actual FPL app each week (at least for v1).
- One user, one FPL team ID, one language (Python).
- "Good enough" beats "perfect" — a model with honest uncertainty beats an overfit model that looks great in backtests.

## 9. Risks

| Risk | Mitigation |
|---|---|
| Prediction model overfits historical data, performs poorly live | Maintain a simple baseline model to compare against continuously; backtest on truly held-out seasons |
| Injury/team-news data lags reality, causing bad recommendations right before deadline | Build in a manual override step before deadline; surface confidence/uncertainty on minutes predictions |
| Third-party historical archive carries undetected lookahead leakage, making backtests overstate performance | Partition features by leakage exposure (§6a.4); exclude `xP`; label state-at-deadline-dependent results provisional; quantify divergence once own snapshots accumulate |
| FPL API changes/breaks | Keep data layer isolated so only one module needs updating |
| High variance makes it hard to tell if the system is "working" | Commit to full-season evaluation windows, not week-to-week judgment |
| Time investment doesn't pay off vs. just playing FPL normally | Treat v1 as a learning project first, ranking improvement second |

## 10. Milestones (proposed)

**Status (2026-08-24): M0–M6 and M8 done. M7 is deliberately deferred, out of order — see its entry below, not just unscheduled.**

0. **Kickoff housekeeping** — done: dependency manager + pinned deps, `.env.example` with real `FPL_TEAM_ID`, repo scaffold, test runner. Small, but cheaper now than mid-build.
1. **Data layer** — done: pull + store FPL API data; begin own snapshot accumulation immediately (every day of delay is history you can't recover). Evaluate archive sources and verify update cadence per §6a.4. Track purchase price per player from day one for selling-price correctness.
2. **Baseline predictor + optimizer** — done: simple heuristic/Poisson model + LP solver → first end-to-end recommendation, even if crude.
3. **ML predictor v1** — done: gradient-boosted model, compared against baseline.
4. **Backtesting harness** — done: simulate past seasons, measure against actual results. Label conclusions per the §6a.4 feature partition.
5. **Weekly live run** — done: use it for real, log predictions vs. outcomes.
6. **Strategy layer** — done: rolling-horizon transfers + chip timing logic (`fpl-optimizer squad`/`plan`, presets, chip scenario comparison).
7. **Auto-infer risk parameter** — **deferred**, not just next in line. Adding the second writer to the risk scalar was meant to be "calibrated against M4's harness," but M4's backtest harness and M6's season simulator only ever replay *archived* seasons using the model's own predictions — this project has never tracked a mini-league's rivals, historically or live, so there is no real `(gap, GW remaining, swing)` data to calibrate a mapping against yet (exactly the overfitting risk already named below). Revisit once the live season has started and a real mini-league's standings have actually been snapshotted for a few gameweeks — not before.
8. **Web app** — done: FastAPI + Next.js becomes the primary interface for live use (built out of milestone order relative to M7, deliberately — see M7's own deferral above and Architecture §4.7 for the full rationale). CLI business logic extracted to a shared `services/` layer so the CLI and API never diverge (Architecture P2 extended to the interface layer). Multi-tenant: each account connects its own FPL team (revises the §3 non-goal above). Not yet deployed to a real Fly.io/Vercel account — see README's "Web app" section for what's left.
9. **(Stretch) Auto-execution**: revisit only after M1–M7 (and now M8) prove reliable and trustworthy.

**Note on M1 urgency:** snapshot accumulation is the one task where starting earlier strictly dominates. Everything else can be built in any order; snapshots can only be collected forward in time.

## 11. Open Questions

### Resolved (see §6a)
- ~~How much lookahead is useful vs. noise?~~ → Rolling 3–5 GW horizon with decay weighting, re-planned weekly, only week 1 executed.
- ~~Pure expected points or risk-adjusted?~~ → Always max expected points; a single risk parameter (manual or auto-inferred) tunes variance preference.
- ~~How to handle team value?~~ → Not a competing objective. Enters as a soft tiebreaker via the *Value-conscious* preset.
- ~~Does the predictor need to output a distribution?~~ → Already settled: the Predictor interface (Architecture §4.4) emits `std_dev` alongside the mean.
- ~~Does chip evaluation need a separate horizon?~~ → Already settled: chips are evaluated by scenario comparison with a per-candidate-week horizon, deliberately outside the MILP (Architecture §4.6).
- ~~Where does pre-launch historical data come from, and can we trust it?~~ → Partition features by leakage exposure (§6a.4) rather than accepting or rejecting backtests wholesale.

### Still open
- **Selling-price correctness.** The optimizer must use *selling* price (purchase price + 50% of rise, rounded down), not current market price, or it will think the budget is larger than it is. Mechanically straightforward, but must be right from day one — it silently corrupts every recommendation otherwise. Requires tracking purchase price per player held (now folded into M1).
- **Decay curve shape.** Starting point is `1.0, 0.8, 0.6, 0.45, 0.3`, but the right curve — and whether the horizon is 3, 4, or 5 — is an empirical question for the backtesting harness.
- **Variance source, not variance existence.** The Predictor already emits `std_dev`. The narrower open question: is historical points-spread an adequate proxy early on, or do the *Safe*/*Aggressive* presets need a genuinely distributional model (e.g. Poisson-based) from the start?
- **Auto-infer calibration** (M7 itself now deferred, per §10 — not just this sub-question). What gap-to-risk mapping actually helps? Needs real mini-league standings history, which doesn't exist yet for any archived or live season this project has touched; M4/M6's backtest tooling can't substitute for it, so calibrating now would risk overfitting to an invented scenario rather than a real one.
- **Archive update cadence.** Needs direct verification (§6a.4) — if the primary archive updates only a few times per season, volatile-field fidelity may be low enough that state-at-deadline features are unusable rather than merely approximate.