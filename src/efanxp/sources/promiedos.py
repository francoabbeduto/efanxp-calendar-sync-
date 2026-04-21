"""
Promiedos API client.
Fetches match data from the unofficial api.promiedos.com.ar JSON API.
Only used for cross-validation — not a primary event source.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Optional

import httpx

from efanxp.utils.logger import get_logger

log = get_logger(__name__)

BASE_URL = "https://api.promiedos.com.ar"
HEADERS = {
    "Referer": "https://www.promiedos.com.ar/",
    "User-Agent": "Mozilla/5.0 (compatible; efanxp-calendar-sync)",
    "Accept": "application/json",
}
TIMEOUT = 10.0


@dataclass
class PromiedosMatch:
    match_id: str
    home_team: str
    away_team: str
    start_time: Optional[str]   # "HH:MM" Argentine local time, or None if TBD
    league_name: str
    league_id: str


class PromiedosClient:
    """Thin client for api.promiedos.com.ar. Results are cached per date."""

    def __init__(self) -> None:
        self._cache: dict[str, list[PromiedosMatch]] = {}

    def get_matches(self, target_date: date) -> list[PromiedosMatch]:
        """Return all matches Promiedos lists for a given date."""
        cache_key = target_date.isoformat()
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Promiedos API expects DD-MM-YYYY, not ISO format
        date_str = target_date.strftime("%d-%m-%Y")
        url = f"{BASE_URL}/games/{date_str}"
        try:
            with httpx.Client(timeout=TIMEOUT, headers=HEADERS) as client:
                resp = client.get(url)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            log.warning("promiedos_http_error", date=cache_key,
                        status=exc.response.status_code)
            self._cache[cache_key] = []
            return []
        except Exception as exc:
            log.warning("promiedos_fetch_error", date=cache_key, error=str(exc))
            self._cache[cache_key] = []
            return []

        matches = self._parse(data)
        self._cache[cache_key] = matches
        log.debug("promiedos_fetched", date=cache_key, count=len(matches))
        return matches

    def _parse(self, data: dict) -> list[PromiedosMatch]:
        matches: list[PromiedosMatch] = []
        for league_block in data.get("leagues", []):
            league = league_block.get("league", {})
            league_name = league.get("name", "")
            league_id = str(league.get("id", ""))
            for m in league_block.get("matches", []):
                home = (m.get("home_team") or {}).get("name", "")
                away = (m.get("away_team") or {}).get("name", "")
                raw_time = m.get("time_to_display")
                # "00:00" means TBD in Promiedos
                start_time = raw_time if raw_time and raw_time != "00:00" else None
                if home and away:
                    matches.append(PromiedosMatch(
                        match_id=str(m.get("id", "")),
                        home_team=home,
                        away_team=away,
                        start_time=start_time,
                        league_name=league_name,
                        league_id=league_id,
                    ))
        return matches
