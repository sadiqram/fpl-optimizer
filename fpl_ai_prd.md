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
- **Not** solving mini-league specific strategy (e.g., "I need to overtake a specific rival") — v1 optimizes for absolute expected points, not relative standing.

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
- **FR2:** Generate predicted points per player for the upcoming gameweek (and ideally a 5-gameweek lookahead).
- **FR3:** Given predictions + current squad state, output the optimal transfer decision(s), including a "hold, don't transfer" recommendation when no transfer clears the -4 hit threshold.
- **FR4:** Recommend starting XI, bench order, captain, and vice-captain from the resulting squad.
- **FR5:** Flag chip usage opportunities (e.g., "this looks like a strong Bench Boost week") with reasoning.
- **FR6:** Log every prediction and every recommendation made, with a way to compare predicted vs. actual after the gameweek resolves.
- **FR7:** Present output in a reviewable format (not auto-executed) — e.g., a summary a human reads and approves.

### Non-Functional
- **NFR1:** Weekly recommendation must be available before the gameweek deadline (typically Friday/Saturday) with enough lead time to review — target: runnable at least 24 hours before deadline.
- **NFR2:** System should degrade gracefully with missing data (e.g., no confirmed team news yet) rather than fail outright.
- **NFR3:** All predictions and recommendations must be explainable — able to show *why* (which features/stats) drove a recommendation, not just a black-box number.
- **NFR4:** Reproducible: rerunning on the same data should give the same recommendation (no unexplained nondeterminism).

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

- How much lookahead is actually useful vs. noise (2 gameweeks? 5? full season)?
- Do we optimize purely for expected points, or add a risk term (e.g., penalize high-variance picks, or the opposite — chase upside if behind in a mini-league)?
- How do we handle price changes / team value in the optimization (selling a player who's risen in price nets more budget than the LP naively assumes)?