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
- **Not** a multi-user product/SaaS — single user (you), single team, to start.
- **Not** predicting live in-match events (bonus points, red cards) in real time — weekly cadence, not live.
- **Not** full mini-league game theory (modelling specific rivals' squads and playing directly against them). v1 handles league context only through a single risk parameter (see §6a), not opponent-by-opponent strategy.
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
- **FR9:** Expose a single risk parameter, settable manually per gameweek or inferred automatically from a target league's context (§6a.2).
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

### 6a.2 Risk appetite: one scalar, two ways to set it

The optimizer takes a **single risk parameter**. Two modes write to it:

- **Manual** — set directly per gameweek by the user.
- **Auto-infer** — computed from league context when manual is off.

The optimizer only ever sees one number and does not know or care which mode produced it. This keeps the two modes from becoming two parallel systems.

**Auto-infer must use more than league position.** Being 40 points behind in GW5 is recoverable with normal play; 40 behind in GW35 needs a hail mary. Inference inputs:
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

## 7. Constraints

- FPL's official API is public/unauthenticated for read access, but has **no official write/transfer API** — this is a hard constraint that shapes the "human-in-the-loop" scope decision, not just a preference.
- Data on injuries/rotation is inherently incomplete and lags real-world news — the system will sometimes be wrong for reasons no model could fix (a manager's press-conference comment 2 hours before deadline).
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
| FPL API changes/breaks | Keep data layer isolated so only one module needs updating |
| High variance makes it hard to tell if the system is "working" | Commit to full-season evaluation windows, not week-to-week judgment |
| Time investment doesn't pay off vs. just playing FPL normally | Treat v1 as a learning project first, ranking improvement second |

## 10. Milestones (proposed)

1. **Data layer**: pull + store historical FPL data, Understat data. *(Validates: can we even get clean data reliably?)*
2. **Baseline predictor + optimizer**: simple heuristic/Poisson model + LP solver → first end-to-end recommendation, even if crude.
3. **ML predictor v1**: gradient-boosted model, compared against baseline.
4. **Backtesting harness**: simulate past seasons, measure against actual results.
5. **Weekly live run**: use it for real, log predictions vs. outcomes.
6. **Strategy layer**: rolling-horizon transfers + chip timing logic.
7. **(Stretch) Auto-execution**: revisit only after v1-v6 prove reliable and trustworthy.

## 11. Open Questions

### Resolved (see §6a)
- ~~How much lookahead is useful vs. noise?~~ → Rolling 3–5 GW horizon with decay weighting, re-planned weekly, only week 1 executed.
- ~~Pure expected points or risk-adjusted?~~ → Always max expected points; a single risk parameter (manual or auto-inferred) tunes variance preference.
- ~~How to handle team value?~~ → Not a competing objective. Enters as a soft tiebreaker via the *Value-conscious* preset.

### Still open
- **Selling-price correctness.** The optimizer must use *selling* price (purchase price + 50% of rise, rounded down), not current market price, or it will think the budget is larger than it is. Mechanically straightforward, but must be right from day one — it silently corrupts every recommendation otherwise. Requires tracking purchase price per player held.
- **Decay curve shape.** Starting point is `1.0, 0.8, 0.6, 0.45, 0.3`, but the right curve — and whether the horizon is 3, 4, or 5 — is an empirical question for the backtesting harness.
- **Auto-infer calibration.** What gap-to-risk mapping actually helps? Needs backtesting against historical mini-league situations, and risks overfitting to a small number of season-end scenarios.
- **Variance estimation.** The *Safe* and *Aggressive* presets need a per-player variance estimate, not just a mean. Does the predictor output a distribution, or do we approximate variance from historical points spread?
- **Chip timing vs. rolling horizon.** Chips are once-per-season decisions whose value often lies beyond a 5-gameweek window (e.g., a double gameweek 10 weeks out). Does chip evaluation need a longer, separate horizon than the transfer optimizer?