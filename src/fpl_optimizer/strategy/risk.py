"""Risk parameter writers and presets (M6d; Architecture §4.6, PRD §6a.2-6a.3).

`RiskWriter.get(gameweek) -> float` is the interface Architecture specifies for a future
auto-infer writer; `ManualRiskWriter` is the only implementation in v1 (PRD §6a.2 — auto-
infer is its own later milestone, M7). Risk and the secondary preset weights are applied as
*pool preprocessing* (`apply_risk_adjustment`), not solver surgery — `optimize/squad.py`,
`optimize/lineup.py`, and `strategy/transfers.py` never see risk math at all, which is what
makes adding a second writer later a change to this file alone.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import pandas as pd
import yaml

DEFAULT_CONFIG_PATH = Path("config/default.yaml")

# Every preset maximizes expected points (PRD §6a.3) — this is what "no preset" resolves to,
# a pure no-op adjustment.
BALANCED_PRESET = {"risk": 0.0, "variance_penalty": 0.0, "ownership_bonus": 0.0, "value_bonus": 0.0}


class RiskWriter(Protocol):
    def get(self, gameweek: int) -> float: ...


class ManualRiskWriter:
    """The only writer in v1 (PRD §6a.2) — a single value, set once, used for every
    gameweek regardless of what's asked. `optimize_transfers`/`plan_horizon` only ever
    consume the resolved scalar and don't know or care which writer produced it."""

    def __init__(self, risk: float):
        self._risk = risk

    def get(self, gameweek: int) -> float:
        return self._risk


def load_presets(config_path: Path = DEFAULT_CONFIG_PATH) -> dict[str, dict]:
    return yaml.safe_load(config_path.read_text())["presets"]


def default_preset_name(config_path: Path = DEFAULT_CONFIG_PATH) -> str:
    return yaml.safe_load(config_path.read_text())["risk"]["default_preset"]


def resolve_preset(name: str, config_path: Path = DEFAULT_CONFIG_PATH) -> dict:
    presets = load_presets(config_path)
    if name not in presets:
        raise ValueError(f"Unknown preset {name!r} — choices: {sorted(presets)}")
    return presets[name]


def apply_risk_adjustment(pool: pd.DataFrame, preset: dict) -> pd.DataFrame:
    """Replaces `expected_points` with a risk-adjusted score built from a preset's
    {risk, variance_penalty, ownership_bonus, value_bonus} — every preset still maximizes
    expected points (PRD §6a.3); these are secondary weightings on top of it, not a
    different objective. `optimize_transfers` then optimizes on this adjusted score with no
    idea risk was ever involved.

    `pool` needs a `std_dev` column (always present — every Predictor emits it, Architecture
    §4.4). `selected_by_percent`/`now_cost` are optional and degrade to a 0 contribution if
    absent (NFR2) rather than raising — not every caller has ownership data wired through.

    - `risk`: reward (positive) or penalize (negative) variance — differentials/explosive
      picks vs. nailed-on safe returns.
    - `variance_penalty`: an explicit *extra* dampener on variance regardless of `risk`'s own
      sign — what the Safe preset uses.
    - `ownership_bonus`: rewards low-ownership differentials — only meaningful alongside a
      positive `risk` (Aggressive preset); harmless but inert otherwise.
    - `value_bonus`: a small tiebreaker toward cheaper players, preserving budget
      flexibility (§6a.3's Value-conscious preset) — deliberately small by design, not a
      value-maximizing objective (§6a.3 explains why team value is never a competing goal).
      Coarse first cut scaling (0.1m units / 10, roughly points-sized), not calibrated — PRD
      §11 already frames the exact secondary weightings as an empirical/backtesting question.
    """
    adjusted = pool.copy()
    std_dev = adjusted["std_dev"].fillna(0.0)
    # .fillna(0): a player missing ownership/price data shouldn't have their whole score
    # nulled out by a term that just has nothing to contribute for them (NFR2).
    ownership_term = (
        (1 - adjusted["selected_by_percent"] / 100).fillna(0.0) if "selected_by_percent" in adjusted else 0.0
    )
    value_term = ((100 - adjusted["now_cost"]) / 10).fillna(0.0) if "now_cost" in adjusted else 0.0

    adjusted["expected_points"] = (
        adjusted["expected_points"]
        + preset.get("risk", 0.0) * std_dev
        - preset.get("variance_penalty", 0.0) * std_dev
        + preset.get("ownership_bonus", 0.0) * ownership_term
        + preset.get("value_bonus", 0.0) * value_term
    )
    return adjusted
