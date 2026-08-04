"""FPL public API client. Read-only, unauthenticated (Constraints §7) — fetches only, never transforms."""

from __future__ import annotations

import requests

BASE_URL = "https://fantasy.premierleague.com/api"


class FPLClient:
    def __init__(self, session: requests.Session | None = None, timeout: float = 15.0):
        self._session = session or requests.Session()
        self._timeout = timeout

    def _get(self, path: str, **params) -> dict | list:
        resp = self._session.get(f"{BASE_URL}/{path}", params=params, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    def bootstrap_static(self) -> dict:
        """Players, teams, positions, gameweeks — the core current-state snapshot."""
        return self._get("bootstrap-static/")

    def fixtures(self, event: int | None = None) -> list[dict]:
        return self._get("fixtures/", **({"event": event} if event is not None else {}))

    def entry(self, team_id: int) -> dict:
        """Manager/team identity, leagues, current value/bank."""
        return self._get(f"entry/{team_id}/")

    def entry_history(self, team_id: int) -> dict:
        """Past-season summaries and this season's per-gameweek history for a team."""
        return self._get(f"entry/{team_id}/history/")

    def entry_transfers(self, team_id: int) -> list[dict]:
        """Every transfer made, with element_in_cost/element_out_cost — the basis for
        selling-price tracking (schema.sql squad_transfers). Does not include the
        season-opening 15, which were never "transferred in"."""
        return self._get(f"entry/{team_id}/transfers/")

    def entry_picks(self, team_id: int, event: int) -> dict:
        """Squad + captain/vice for a specific gameweek. 404s before that gameweek's
        deadline has passed for this team (e.g. pre-season, or a GW not yet played)."""
        return self._get(f"entry/{team_id}/event/{event}/picks/")

    def element_summary(self, player_id: int) -> dict:
        """Per-fixture and per-gameweek history for one player, plus prior-season summaries."""
        return self._get(f"element-summary/{player_id}/")
