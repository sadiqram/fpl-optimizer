"""Multi-GW rolling optimization (receding horizon control): plan the window, commit only
week 1 (Architecture §4.6, PRD §6a.1).

`recommend_gameweek` implements the single-gameweek case: the shared pipeline (features ->
predict -> optimize) that both the live `recommend` command and the backtest harness call,
so they can never drift apart (Architecture P2 — "the backtester and the live run must
differ in exactly one respect: what the clock returns"). It stays the right tool for
independent per-gameweek recommendations (what `backtest_season` replays) even now that
`plan_horizon` exists.

`plan_horizon` (M6c) is the real multi-gameweek rolling-horizon logic: decay-weighted
lookahead informs *which transfer* to make, but only week 1's actual decision is returned —
receding horizon control, not a multi-week plan to follow blindly.
"""

from __future__ import annotations

import pandas as pd

from fpl_optimizer.clock import Clock
from fpl_optimizer.features import build as feature_build
from fpl_optimizer.models.base import Predictor
from fpl_optimizer.optimize import lineup, squad
from fpl_optimizer.strategy import transfers

# PRD §6a.1's starting point — declining confidence in more distant gameweeks. horizon_gws
# is derived from len(decay) rather than passed alongside it, so the two can't drift out of
# sync with each other.
DEFAULT_DECAY = [1.0, 0.8, 0.6, 0.45, 0.3]

# Players missing any of these can't be priced, positioned, or scored — dropped rather than
# guessed at (NFR2). Shared so recommend_gameweek and plan_horizon can't quietly diverge on
# what counts as a usable prediction row.
USABLE_PREDICTION_COLUMNS = ["element_type", "team_id", "now_cost", "expected_points"]


def predict_gameweek(conn, as_of_date: str, season: str, gameweek: int, predictor: Predictor) -> tuple[pd.DataFrame, pd.DataFrame]:
    """features -> predict -> merge, for one gameweek. Returns (features_df, merged) — not
    yet filtered to usable rows, since callers differ on whether they need the full merged
    frame (e.g. to report how many players were excluded)."""
    features_df = feature_build.assemble_features(conn, as_of_date, season, gameweek)
    predictions = predictor.predict(features_df)
    merged = predictions.merge(
        features_df[["player_id", "element_type", "team_id", "now_cost"]], on="player_id", how="left"
    )
    return features_df, merged


def recommend_gameweek(conn, clock: Clock, season: str, gameweek: int, predictor: Predictor) -> dict:
    """Builds features as of `clock.today()`, predicts with an already-fitted `predictor`
    (training is the caller's job — a separate lifecycle stage, not something this function
    redoes per call), and optimizes a *fresh* squad + lineup (wildcard-style — no owned
    squad or budget history involved; see `plan_horizon` for transfer-constrained planning).

    Returns {"as_of_date", "features", "predictions" (predictions joined with
    element_type/team_id/now_cost), "squad", "lineup"}. Players missing price, position, or
    a prediction are excluded from optimization — there's nothing to fabricate a value from
    for them, so they're dropped rather than guessed at.
    """
    as_of_date = clock.today()
    features_df, merged = predict_gameweek(conn, as_of_date, season, gameweek, predictor)
    usable = merged.dropna(subset=USABLE_PREDICTION_COLUMNS)

    squad_df = squad.build_squad(usable)
    lineup_result = lineup.build_lineup(squad_df)

    return {
        "as_of_date": as_of_date,
        "features": features_df,
        "predictions": merged,
        "squad": squad_df,
        "lineup": lineup_result,
    }


def plan_horizon(
    conn,
    clock: Clock,
    season: str,
    gameweek: int,
    predictor: Predictor,
    owned_squad: pd.DataFrame,
    bank: int,
    free_transfers: int,
    decay: list[float] = DEFAULT_DECAY,
) -> dict:
    """Plans over `len(decay)` gameweeks starting at `gameweek`, but commits only this
    week's transfer + lineup decision (PRD §6a.1) — re-planning next week with fresh data
    is what makes committing to weeks 2+ unnecessary and undesirable (predictions that far
    out are the weakest input this system has).

    The horizon's decay-weighted, summed expected points *only* drive the transfer decision
    — a player worth owning needs to look good across the window, not just this week
    (avoids buying into a good week 1 ahead of a brutal fixture run). The starting XI and
    captain, once the squad is settled, are chosen from week 1's own *undecayed* prediction:
    captaincy is "who scores most this week", not a horizon blend.

    `owned_squad`: player_id, selling_price — see `strategy.transfers.optimize_transfers`.

    Returns {"as_of_date", "gameweek", "weekly_predictions" (list of {"gameweek",
    "predictions"} per window week, usable rows only, for logging/rationale),
    "transfer_result" (see `transfers.optimize_transfers`), "lineup"}.
    """
    as_of_date = clock.today()

    weekly_predictions = []
    weighted_frames = []
    for offset, weight in enumerate(decay):
        gw = gameweek + offset
        _, merged = predict_gameweek(conn, as_of_date, season, gw, predictor)
        usable = merged.dropna(subset=USABLE_PREDICTION_COLUMNS)
        weekly_predictions.append({"gameweek": gw, "predictions": usable})
        weighted_frames.append(
            usable[["player_id", "expected_points"]].assign(expected_points=lambda d, w=weight: d["expected_points"] * w)
        )

    horizon_points = pd.concat(weighted_frames).groupby("player_id", as_index=False)["expected_points"].sum()

    week1 = weekly_predictions[0]["predictions"]
    pool = week1[["player_id", "element_type", "team_id", "now_cost"]].merge(horizon_points, on="player_id", how="inner")

    transfer_result = transfers.optimize_transfers(pool, owned_squad, bank, free_transfers)

    # Lineup/captain from week 1's own raw (undecayed) prediction, not the horizon-blended
    # scoring column the transfer decision used.
    new_squad_ids = set(transfer_result["new_squad"]["player_id"])
    week1_scored_squad = week1[week1.player_id.isin(new_squad_ids)][["player_id", "element_type", "expected_points"]]
    lineup_result = lineup.build_lineup(week1_scored_squad)

    return {
        "as_of_date": as_of_date,
        "gameweek": gameweek,
        "weekly_predictions": weekly_predictions,
        "transfer_result": transfer_result,
        "lineup": lineup_result,
    }
