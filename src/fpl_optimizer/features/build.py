"""Assembles feature families into per (player, gameweek) rows; materializes to data/artifacts/features/{as_of}/ as Parquet.

Also owns the feature-trust registry (outcome-derived vs. state-at-deadline), read by the evaluation layer to label backtest conclusions trusted vs. provisional (Architecture §4.2, PRD §6a.4).
"""
