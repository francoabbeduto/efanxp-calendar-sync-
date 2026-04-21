"""TheSportsDB adapter — covers Argentine, Chilean, Peruvian and Brazilian football."""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any

import httpx

from efanxp.config import get_settings
from efanxp.models import EventStatus, EventType, RawEvent
from efanxp.sources.base import BaseSource
from efanxp.utils.logger import get_logger
from efanxp.utils.retry import http_retry

log = get_logger(__name__)

BASE_URL = "https://www.thesportsdb.com/api/v1/json"

# TheSportsDB event status strings → our enum
STATUS_MAP: dict[str, EventStatus] = {
    "": EventStatus.SCHEDULED,
    "Not Started": EventStatus.SCHEDULED,
    "In Progress": EventStatus.CONFIRMED,
    "Finished": EventStatus.FINISHED,
    "Postponed": EventStatus.POSTPONED,
    "Cancelled": EventStatus.CANCELLED,
    "Abandoned": EventStatus.CANCELLED,
    "TBD": EventStatus.TBD,
}


class TheSportsDBSource(BaseSource):
    name = "thesportsdb"

    def __init__(self, club_id: str, source_config: dict[str, Any]):
        super().__init__(club_id, source_config)
        self.team_id: str = source_config["team_id"]
        self.api_key: str = get_settings().thesportsdb_api_key

    # ── Public interface ──────────────────────────────────────────────────────

    def fetch(self, lookahead_days: int = 90, lookback_days: int = 7) -> list[RawEvent]:
        today = date.today()
        cutoff_past = today - timedelta(days=lookback_days)
        cutoff_future = today + timedelta(days=lookahead_days)

        # Fetch full season (free, up to 250 events) + next/last as fallback
        # 1-second delay between calls to avoid TheSportsDB rate limiting (429)
        current_year = today.year
        raw_events = self._fetch_season(str(current_year))
        time.sleep(1)
        raw_events += self._fetch_season(str(current_year + 1))
        time.sleep(1)
        raw_events += self._fetch_next_events()
        time.sleep(1)
        raw_events += self._fetch_past_events()

        events: list[RawEvent] = []
        for ev in raw_events:
            parsed = self._parse_event(ev)
            if parsed is None:
                continue
            if parsed.start_date:
                ev_date = date.fromisoformat(parsed.start_date)
                if ev_date < cutoff_past or ev_date > cutoff_future:
                    continue
            events.append(parsed)

        # Deduplicate by source_id
        seen: dict[str, RawEvent] = {}
        for ev in events:
            seen[ev.source_id] = ev
        return list(seen.values())

    def find_team_id(self, team_name: str) -> list[dict]:
        """Helper for the CLI `efanxp sources find` command."""
        url = f"{BASE_URL}/{self.api_key}/searchteams.php"
        with httpx.Client(timeout=15) as client:
            r = client.get(url, params={"t": team_name})
            r.raise_for_status()
            return (r.json().get("teams") or [])

    # ── Private helpers ───────────────────────────────────────────────────────

    @http_retry
    def _fetch_season(self, season: str) -> list[dict]:
        """Fetch all events for a team in a given season (free, up to 250 events)."""
        url = f"{BASE_URL}/{self.api_key}/eventsseason.php"
        with httpx.Client(timeout=20) as client:
            r = client.get(url, params={"id": self.team_id, "s": season})
            r.raise_for_status()
            return r.json().get("events") or []

    @http_retry
    def _fetch_next_events(self) -> list[dict]:
        url = f"{BASE_URL}/{self.api_key}/eventsnext.php"
        with httpx.Client(timeout=15) as client:
            r = client.get(url, params={"id": self.team_id})
            r.raise_for_status()
            data = r.json()
            return data.get("events") or []

    @http_retry
    def _fetch_past_events(self) -> list[dict]:
        url = f"{BASE_URL}/{self.api_key}/eventslast.php"
        with httpx.Client(timeout=15) as client:
            r = client.get(url, params={"id": self.team_id})
            r.raise_for_status()
            data = r.json()
            return data.get("results") or []

    def _parse_event(self, ev: dict) -> RawEvent | None:
        try:
            event_id = ev.get("idEvent", "")
            if not event_id:
                return None

            home_team = ev.get("strHomeTeam", "")
            away_team = ev.get("strAwayTeam", "")

            # Only include home matches for this club
            club_name_variants = self._club_name_variants()
            is_home = any(v.lower() in home_team.lower() for v in club_name_variants)
            is_away = any(v.lower() in away_team.lower() for v in club_name_variants)

            if not is_home and not is_away:
                log.debug("event_skipped_not_our_team",
                          event_id=event_id, home=home_team, away=away_team)
                return None

            event_type = EventType.MATCH_HOME if is_home else EventType.MATCH_AWAY

            raw_date = ev.get("dateEvent") or ev.get("strDate") or ""
            raw_time = ev.get("strTime") or ""

            start_date = raw_date if raw_date else None
            start_time: str | None = None
            if raw_time and raw_time not in ("", "00:00:00", "00:00"):
                start_time = raw_time[:5]  # "HH:MM"

            status_str = ev.get("strStatus") or ev.get("strProgress") or ""
            status = STATUS_MAP.get(status_str, EventStatus.SCHEDULED)

            notes = None
            if start_time is None:
                notes = "Hora no confirmada"
            if status == EventStatus.POSTPONED:
                notes = "Partido postergado"

            return RawEvent(
                source_id=self.build_source_id(event_id),
                club_id=self.club_id,
                source_name=self.name,
                title=self._build_title(home_team, away_team),
                event_type=event_type,
                start_date=start_date,
                start_time=start_time,
                timezone=ev.get("strTimezone") or "UTC",
                home_team=home_team,
                away_team=away_team,
                competition=ev.get("strLeague") or ev.get("strRound"),
                venue_name=ev.get("strVenue"),
                country=ev.get("strCountry"),
                status=status,
                notes=notes,
                raw_data=ev,
            )

        except Exception as exc:
            log.warning("parse_error", event=ev.get("idEvent"), error=str(exc))
            return None

    def _build_title(self, home: str, away: str) -> str:
        return f"{home} vs {away}"

    def _club_name_variants(self) -> list[str]:
        """Returns name variants to match against API responses."""
        variants_map = {
            "boca-juniors": ["Boca Juniors", "Boca"],
            "river-plate": ["River Plate", "River"],
            "estudiantes": ["Estudiantes", "Estudiantes de La Plata"],
            "velez": ["Vélez", "Velez", "Vélez Sarsfield", "Velez Sarsfield"],
            "huracan": ["Huracán", "Huracan"],
            "san-lorenzo": ["San Lorenzo"],
            "universidad-de-chile": ["Universidad de Chile", "U de Chile", "La U"],
            "colo-colo": ["Colo Colo", "Colo-Colo"],
            "alianza-lima": ["Alianza Lima", "Alianza"],
            "bahia": ["Bahia", "E.C. Bahia", "EC Bahia"],
            "selknam": ["Selknam"],
        }
        return variants_map.get(self.club_id, [self.club_id])
