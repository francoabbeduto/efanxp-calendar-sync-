"""Sofascore adapter — unofficial API, no key required, full future fixture coverage."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from efanxp.models import EventStatus, EventType, RawEvent
from efanxp.sources.base import BaseSource
from efanxp.utils.logger import get_logger
from efanxp.utils.retry import http_retry

log = get_logger(__name__)

BASE_URL = "https://api.sofascore.com/api/v1"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

STATUS_MAP = {
    "notstarted": EventStatus.SCHEDULED,
    "inprogress": EventStatus.CONFIRMED,
    "finished": EventStatus.FINISHED,
    "postponed": EventStatus.POSTPONED,
    "canceled": EventStatus.CANCELLED,
    "cancelled": EventStatus.CANCELLED,
    "interrupted": EventStatus.POSTPONED,
    "abandoned": EventStatus.CANCELLED,
    "coverage": EventStatus.SCHEDULED,
}


class SofascoreSource(BaseSource):
    name = "sofascore"

    def __init__(self, club_id: str, source_config: dict[str, Any]):
        super().__init__(club_id, source_config)
        self.team_id: str = str(source_config["team_id"])

    def fetch(self, lookahead_days: int = 90, lookback_days: int = 7) -> list[RawEvent]:
        today = date.today()
        cutoff_past = today - timedelta(days=lookback_days)
        cutoff_future = today + timedelta(days=lookahead_days)

        events: list[RawEvent] = []

        # Fetch upcoming fixtures (paginated, 10 per page)
        for page in range(5):  # max 50 upcoming events
            page_events = self._fetch_page("next", page)
            if not page_events:
                break
            filtered = self._filter_and_parse(page_events, cutoff_past, cutoff_future)
            events.extend(filtered)
            # Stop if last event on this page is beyond our window
            last = page_events[-1]
            last_ts = last.get("startTimestamp", 0)
            if last_ts and datetime.fromtimestamp(last_ts, tz=timezone.utc).date() > cutoff_future:
                break

        # Also fetch recent past fixtures
        for page in range(2):  # max 20 past events
            page_events = self._fetch_page("last", page)
            if not page_events:
                break
            filtered = self._filter_and_parse(page_events, cutoff_past, cutoff_future)
            events.extend(filtered)

        seen: dict[str, RawEvent] = {}
        for ev in events:
            seen[ev.source_id] = ev
        return list(seen.values())

    @http_retry
    def _fetch_page(self, direction: str, page: int) -> list[dict]:
        url = f"{BASE_URL}/team/{self.team_id}/events/{direction}/{page}"
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            r = client.get(url, headers=HEADERS)
            if r.status_code == 404:
                return []
            if r.status_code == 403:
                log.warning("sofascore_blocked", team=self.team_id, direction=direction, page=page)
                return []
            r.raise_for_status()
            data = r.json()
            events = data.get("events") or []
            log.info("sofascore_fetched", team=self.team_id, direction=direction, page=page, count=len(events))
            return events

    def _filter_and_parse(
        self, raw_events: list[dict], cutoff_past: date, cutoff_future: date
    ) -> list[RawEvent]:
        results = []
        for ev in raw_events:
            parsed = self._parse_event(ev)
            if parsed is None:
                continue
            if parsed.start_date:
                ev_date = date.fromisoformat(parsed.start_date)
                if ev_date < cutoff_past or ev_date > cutoff_future:
                    continue
            results.append(parsed)
        return results

    def _parse_event(self, ev: dict) -> RawEvent | None:
        try:
            event_id = str(ev.get("id", ""))
            if not event_id:
                return None

            home_team = ev.get("homeTeam", {}).get("name", "")
            away_team = ev.get("awayTeam", {}).get("name", "")
            home_id = str(ev.get("homeTeam", {}).get("id", ""))
            is_home = home_id == self.team_id
            event_type = EventType.MATCH_HOME if is_home else EventType.MATCH_AWAY

            ts = ev.get("startTimestamp")
            start_date: str | None = None
            start_time: str | None = None
            if ts:
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                start_date = dt.date().isoformat()
                time_str = dt.strftime("%H:%M")
                if time_str != "00:00":
                    start_time = time_str

            status_type = ev.get("status", {}).get("type", "notstarted")
            status = STATUS_MAP.get(status_type, EventStatus.SCHEDULED)

            venue = ev.get("venue", {})
            venue_name = venue.get("name") if venue else None

            competition = (
                ev.get("tournament", {}).get("uniqueTournament", {}).get("name")
                or ev.get("tournament", {}).get("name")
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
                timezone="UTC",
                home_team=home_team,
                away_team=away_team,
                competition=competition,
                venue_name=venue_name,
                country=ev.get("tournament", {}).get("category", {}).get("name"),
                status=status,
                notes=notes,
                raw_data=ev,
            )
        except Exception as exc:
            log.warning("sofascore_parse_error", event=ev.get("id"), error=str(exc))
            return None
