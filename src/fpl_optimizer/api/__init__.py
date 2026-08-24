"""Web API (M8) — FastAPI wrapper around the same service layer the CLI uses.

The CLI (`cli.py`) stays the local/dev/backtest entry point (Architecture P2's one-code-path
principle still holds: both front doors call `services/`, never duplicate its logic). This
package is the primary interface for live, multi-tenant use once deployed.
"""
