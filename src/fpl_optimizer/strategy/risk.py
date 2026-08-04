"""Risk parameter writers and presets (Architecture §4.6, PRD §6a.2-6a.3).

RiskWriter.get(gameweek) -> float. ManualRiskWriter is the only implementation in v1;
AutoInferRiskWriter is deferred to post-backtest-harness. The optimizer consumes the
resolved scalar only and never knows which writer produced it.
"""
