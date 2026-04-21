"""API-Sports Football adapter — fetches fixtures by date range (no season format issues)."""

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

BASE_URL = "https://v3.football.api-sports.io"

STATUS_MAP = {
    "NS": EventStatus.SCHEDULED,
    "TBD": EventStatus.SCHEDULED,
    "1H": EventStatus.IN_PROGRESS,
    "HT": EventStatus.IN_PROGRESS,
    "2H": EventStatus.IN_PROGRESS,
    "ET": EventStatus.IN_PROGRESS,
    "BT": EventStatus.IN_PROGRESS,
    "P": EventStatus.IN_PROGRESS,
    "SUSP": EventStatus.POSTPONED,
    "INT": EventStatus.IN_PROGRESS,
    "FT": EventStatus.FINISHED,
    "AET": EventStatus.FINISHED,
    "PEN": EventStatus.FINISHED,
    "PST": EventStatus.POSTPONED,
    "CANC": EventStatus.CANCELLED,
    "ABD": EventStatus.CANCELLED,
    "AWD": EventStatus.FINISHED,
    "WO": EventStatus.FINISHED,
    "LIVE": EventStatus.IN_PROGRESS,
}


class ApiSportsFootballSource(BaseSource):
    name = "api_sports_football"

    def __init__(self, club_id: str, source_config: dict[str, Any]):
        super().__init__(club_id, source_config)
        self.team_id: str = str(source_config["team_id"])
        self.api_key: str = get_settings().api_sports_key

    def fetch(self, lookahead_days: int = 90, lookback_days: int = 7) -> list[RawEvent]:
        if not self.api_key:
            log.warning("api_sports_key_missing", club=self.club_id)
            return []

        today = date.today()
        date_from = today - timedelta(days=lookback_days)
        date_to = today + timedelta(days=lookahead_days)

        fixtures = self._fetch_fixtures(str(date_from), str(date_to))

        events: list[RawEvent] = []
        for fixture in fixtures:
            parsed = self._parse_fixture(fixture)
            if parsed is not None:
                events.append(parsed)

        seen: dict[str, RawEvent] = {}
        for ev in events:
            seen[ev.source_id] = ev
        return list(seen.values())

    @http_retry
    def _fetch_fixtures(self, date_from: str, date_to: str) -> list[dict]:
        headers = {"x-apisports-key": self.api_key}
        params = {"team": self.team_id, "from": date_from, "to": date_to}

        with httpx.Client(timeout=20) as client:
            r = client.get(f"{BASE_URL}/fixtures", headers=headers, params=params)
            r.raise_for_status()
            return r.json().get("response") or []

    def _parse_fixture(self, fixture: dict) -> RawEvent | None:
        try:
            info = fixture.get("fixture", {})
            teams = fixture.get("teams", {})
            league = fixture.get("league", {})

            fixture_id = str(info.get("id", ""))
            if not fixture_id:
                return None

            home_team = teams.get("home", {}).get("name", "")
            away_team = teams.get("away", {}).get("name", "")
            is_home = str(teams.get("home", {}).get("id", "")) == self.team_id
            event_type = EventType.MATCH_HOME if is_home else EventType.MATCH_AWAY

            raw_datetime = info.get("date", "")  # "2026-04-25T14:00:00+00:00"
            start_date: str | None = raw_datetime[:10] if raw_datetime else None
            start_time: str | None = raw_datetime[11:16] if len(raw_datetime) > 10 else None
            if start_time in ("00:00", ""):
                start_time = None

            status_code = info.get("status", {}).get("short", "")
            status = STATUS_MAP.get(status_code, EventStatus.SCHEDULED)

            venue = info.get("venue", {})
            venue_name = venue.get("name")

            notes = None
            if start_time is None:
                notes = "Hora no confirmada"
            if status == EventStatus.POSTPONED:
                notes = "Partido postergado"

            return RawEvent(
                source_id=self.build_source_id(fixture_id),
                club_id=self.club_id,
                source_name=self.name,
                title=f"{home_team} vs {away_team}",
                event_type=event_type,
                start_date=start_date,
                start_time=start_time,
                timezone="UTC",
                home_team=home_team,
                away_team=away_team,
                competition=league.get("name"),
                venue_name=venue_name,
                country=league.get("country"),
                status=status,
                notes=notes,
                raw_data=fixture,
            )
        except Exception as exc:
            log.warning("football_parse_error", fixture=fixture.get("fixture", {}).get("id"), error=str(exc))
            return None
