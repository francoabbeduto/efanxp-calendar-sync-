"""ESPN public API adapter — no key required, works from GitHub Actions."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from efanxp.models import EventStatus, EventType, RawEvent
from efanxp.sources.base import BaseSource
from efanxp.utils.logger import get_logger
from efanxp.utils.retry import http_retry

log = get_logger(__name__)

import pytz

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer"
HEADERS = {"Accept": "application/json", "Accept-Encoding": "gzip, deflate"}

CHUNK_DAYS = 30

LEAGUE_TZ: dict[str, str] = {
    "arg.1": "America/Argentina/Buenos_Aires",
    "chi.1": "America/Santiago",
    "per.1": "America/Lima",
    "bra.1": "America/Sao_Paulo",
}

COPA_LEAGUES = ["conmebol.libertadores", "conmebol.sudamericana"]


class ESPNSource(BaseSource):
    name = "espn"

    def __init__(self, club_id: str, source_config: dict[str, Any]):
        super().__init__(club_id, source_config)
        self.team_id: str = str(source_config["team_id"])
        self.league: str = source_config["league"]  # e.g. "arg.1", "chi.1"
        self.home_tz: str = LEAGUE_TZ.get(self.league, "UTC")

    def fetch(self, lookahead_days: int = 90, lookback_days: int = 7) -> list[RawEvent]:
        today = date.today()
        date_from = today - timedelta(days=lookback_days)
        date_to = today + timedelta(days=lookahead_days)

        all_raw: list[dict] = []
        for league in [self.league] + COPA_LEAGUES:
            all_raw.extend(self._fetch_range(date_from, date_to, league))

        events: list[RawEvent] = []
        for ev in all_raw:
            parsed = self._parse_event(ev)
            if parsed is not None:
                events.append(parsed)

        seen: dict[str, RawEvent] = {}
        for ev in events:
            seen[ev.source_id] = ev
        return list(seen.values())

    def _fetch_range(self, date_from: date, date_to: date, league: str) -> list[dict]:
        all_events: list[dict] = []
        chunk_start = date_from
        while chunk_start <= date_to:
            chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS - 1), date_to)
            events = self._fetch_scoreboard(chunk_start, chunk_end, league)
            all_events.extend(events)
            chunk_start = chunk_end + timedelta(days=1)
        return all_events

    @http_retry
    def _fetch_scoreboard(self, date_from: date, date_to: date, league: str) -> list[dict]:
        dates_param = f"{date_from.strftime('%Y%m%d')}-{date_to.strftime('%Y%m%d')}"
        url = f"{BASE_URL}/{league}/scoreboard"
        params = {"dates": dates_param, "limit": 200}
        with httpx.Client(timeout=20) as client:
            r = client.get(url, headers=HEADERS, params=params)
            r.raise_for_status()
            data = r.json()

        events = data.get("events") or []
        # Filter to only events involving our team
        return [e for e in events if self._involves_team(e)]

    def _involves_team(self, event: dict) -> bool:
        for comp in event.get("competitions", []):
            for comp_team in comp.get("competitors", []):
                if str(comp_team.get("id", "")) == self.team_id:
                    return True
        return False

    def _parse_event(self, event: dict) -> RawEvent | None:
        try:
            event_id = str(event.get("id", ""))
            if not event_id:
                return None

            competitions = event.get("competitions", [])
            if not competitions:
                return None
            comp = competitions[0]

            competitors = comp.get("competitors", [])
            home_comp = next((c for c in competitors if c.get("homeAway") == "home"), None)
            away_comp = next((c for c in competitors if c.get("homeAway") == "away"), None)
            if not home_comp or not away_comp:
                return None

            home_team = home_comp.get("team", {}).get("displayName", "")
            away_team = away_comp.get("team", {}).get("displayName", "")
            is_home = str(home_comp.get("id", "")) == self.team_id
            event_type = EventType.MATCH_HOME if is_home else EventType.MATCH_AWAY

            raw_date = event.get("date", "")  # "2026-04-23T23:00Z" (UTC)
            start_date: str | None = None
            start_time: str | None = None
            if raw_date:
                dt_utc = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                dt_local = dt_utc.astimezone(pytz.timezone(self.home_tz))
                start_date = dt_local.date().isoformat()
                time_str = dt_local.strftime("%H:%M")
                if time_str != "00:00":
                    start_time = time_str

            completed = comp.get("status", {}).get("type", {}).get("completed", False)
            status_name = comp.get("status", {}).get("type", {}).get("name", "")
            if completed:
                status = EventStatus.FINISHED
            elif "postponed" in status_name.lower() or "cancelled" in status_name.lower():
                status = EventStatus.POSTPONED
            else:
                status = EventStatus.SCHEDULED

            venue = comp.get("venue", {})
            venue_name = venue.get("fullName")

            competition_name = (
                event.get("season", {}).get("slug")
                or event.get("name")
            )

            notes = None
            if start_time is None:
                notes = "Hora no confirmada"
            if status == EventStatus.POSTPONED:
                notes = "Partido postergado"

            return RawEvent(
                source_id=self.build_source_id(event_id),
                club_id=self.club_id,
                source_name=self.name,
                title=f"{home_team} vs {away_team}",
                event_type=event_type,
                start_date=start_date,
                start_time=start_time,
                timezone=self.home_tz,
                home_team=home_team,
                away_team=away_team,
                competition=competition_name,
                venue_name=venue_name,
                country=None,
                status=status,
                notes=notes,
                raw_data=event,
            )
        except Exception as exc:
            log.warning("espn_parse_error", event=event.get("id"), error=str(exc))
            return None
