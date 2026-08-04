"""Multi-GW rolling optimization (receding horizon control): plan the window, commit only
week 1 (Architecture §4.6, PRD §6a.1).

Currently implements only the single-gameweek case: the shared pipeline (features ->
predict -> optimize) that both the live `recommend` command and the backtest harness call,
so they can never drift apart (Architecture P2 — "the backtester and the live run must
differ in exactly one respect: what the clock returns"). The real multi-gameweek
rolling-horizon logic (decay-weighted lookahead, commit-only-week-1) extends this function;
it doesn't replace it.
"""

from __future__ import annotations

from fpl_optimizer.clock import Clock
from fpl_optimizer.features import build as feature_build
from fpl_optimizer.models.base import Predictor
from fpl_optimizer.optimize import lineup, squad


def recommend_gameweek(conn, clock: Clock, season: str, gameweek: int, predictor: Predictor) -> dict:
    """Builds features as of `clock.today()`, predicts with an already-fitted `predictor`
    (training is the caller's job — a separate lifecycle stage, not something this function
    redoes per call), and optimizes a squad + lineup.

    Returns {"as_of_date", "features", "predictions" (predictions joined with
    element_type/team_id/now_cost), "squad", "lineup"}. Players missing price, position, or
    a prediction are excluded from optimization — there's nothing to fabricate a value from
    for them, so they're dropped rather than guessed at.
    """
    as_of_date = clock.today()
    features_df = feature_build.assemble_features(conn, as_of_date, season, gameweek)
    predictions = predictor.predict(features_df)

    merged = predictions.merge(
        features_df[["player_id", "element_type", "team_id", "now_cost"]], on="player_id", how="left"
    )
    usable = merged.dropna(subset=["element_type", "team_id", "now_cost", "expected_points"])

    squad_df = squad.build_squad(usable)
    lineup_result = lineup.build_lineup(squad_df)

    return {
        "as_of_date": as_of_date,
        "features": features_df,
        "predictions": merged,
        "squad": squad_df,
        "lineup": lineup_result,
    }
