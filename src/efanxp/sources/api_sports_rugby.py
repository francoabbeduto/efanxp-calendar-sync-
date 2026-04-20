"""API-Sports Rugby adapter — used for Selknam (Super Rugby Americas)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import httpx

from efanxp.config import get_settings
from efanxp.models import EventStatus, EventType, RawEvent
from efanxp.sources.base import BaseSource
from efanxp.utils.logger import get_logger
from efanxp.utils.retry import http_retry

log = get_logger(__name__)

BASE_URL = "https://v1.rugby.api-sports.io"


class ApiSportsRugbySource(BaseSource):
    name = "api_sports_rugby"

    def __init__(self, club_id: str, source_config: dict[str, Any]):
        super().__init__(club_id, source_config)
        self.team_id: str = str(source_config["team_id"])
        self.league_id: str = str(source_config.get("league_id", ""))
        self.api_key: str = get_settings().api_sports_key

    def fetch(self, lookahead_days: int = 90, lookback_days: int = 7) -> list[RawEvent]:
        if not self.api_key:
            log.warning("api_sports_key_missing", club=self.club_id)
            return []

        today = date.today()
        season = str(today.year)
        games = self._fetch_season(season)

        cutoff_past = today - timedelta(days=lookback_days)
        cutoff_future = today + timedelta(days=lookahead_days)

        events: list[RawEvent] = []
        for game in games:
            parsed = self._parse_game(game)
            if parsed is None:
                continue
            if parsed.start_date:
                ev_date = date.fromisoformat(parsed.start_date)
                if ev_date < cutoff_past or ev_date > cutoff_future:
                    continue
            events.append(parsed)
        return events

    @http_retry
    def _fetch_season(self, season: str) -> list[dict]:
        headers = {"x-apisports-key": self.api_key}
        params: dict[str, str] = {"team": self.team_id, "season": season}
        if self.league_id:
            params["league"] = self.league_id

        with httpx.Client(timeout=15) as client:
            r = client.get(f"{BASE_URL}/games", headers=headers, params=params)
            r.raise_for_status()
            return r.json().get("response") or []

    def _parse_game(self, game: dict) -> RawEvent | None:
        try:
            game_id = str(game.get("id", ""))
            if not game_id:
                return None

            teams = game.get("teams", {})
            home = teams.get("home", {}).get("name", "")
            away = teams.get("away", {}).get("name", "")

            is_home = self.club_id in home.lower() or "selknam" in home.lower()
            event_type = EventType.MATCH_HOME if is_home else EventType.MATCH_AWAY

            date_str = game.get("date", "")
            start_date: str | None = date_str[:10] if date_str else None
            start_time: str | None = date_str[11:16] if len(date_str) > 10 else None
            if start_time in ("00:00", ""):
                start_time = None

            status_str = game.get("status", {}).get("long", "")
            status_map = {
                "Not Started": EventStatus.SCHEDULED,
                "Finished": EventStatus.FINISHED,
                "Canceled": EventStatus.CANCELLED,
                "Postponed": EventStatus.POSTPONED,
            }
            status = status_map.get(status_str, EventStatus.SCHEDULED)

            return RawEvent(
                source_id=self.build_source_id(game_id),
                club_id=self.club_id,
                source_name=self.name,
                title=f"{home} vs {away}",
                event_type=event_type,
                start_date=start_date,
                start_time=start_time,
                timezone="UTC",
                home_team=home,
                away_team=away,
                competition=game.get("league", {}).get("name"),
                venue_name=game.get("venue", {}).get("name"),
                country=game.get("country", {}).get("name"),
                status=status,
                notes=None if start_time else "Hora no confirmada",
                raw_data=game,
            )
        except Exception as exc:
            log.warning("rugby_parse_error", game=game.get("id"), error=str(exc))
            return None
