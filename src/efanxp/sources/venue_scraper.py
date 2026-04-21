"""
Generic venue scraper — fetches concert/show events from a venue's website.

Each venue has CSS selectors configured in clubs.yaml under
`sources[].selectors`. This adapter handles the HTML fetch; subclasses or
per-venue configs supply the selectors.

Two selector modes are supported:
  - Classic mode: separate selectors for `title`, `date`, `description`.
  - Indexed mode: `fields` selector returns multiple elements per card;
    `title_index`, `date_index`, `competition_index` pick them by position.

Reliability: LOW — venue websites change frequently. Monitor via the
`efanxp status --scraper-health` command and update selectors as needed.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

import httpx
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from efanxp.models import EventType, RawEvent
from efanxp.sources.base import BaseSource
from efanxp.utils.logger import get_logger
from efanxp.utils.retry import http_retry

log = get_logger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; eFanXP-CalendarBot/1.0; "
        "+https://efanxp.com/calendarbot)"
    )
}

# Portuguese month abbreviations → month number
_PT_MONTHS = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}


class VenueScraperSource(BaseSource):
    name = "venue_scraper"

    def __init__(self, club_id: str, source_config: dict[str, Any]):
        super().__init__(club_id, source_config)
        self.url: str = source_config["url"]
        self.selectors: dict[str, Any] = source_config.get("selectors", {})
        self._indexed_mode = "fields" in self.selectors

    def is_enabled(self) -> bool:
        return self.config.get("enabled", False)

    def fetch(self, lookahead_days: int = 90, lookback_days: int = 7) -> list[RawEvent]:
        html = self._get_html(self.url)
        if not html:
            return []

        soup = BeautifulSoup(html, "lxml")
        containers = self._find_event_containers(soup)

        events: list[RawEvent] = []
        for i, container in enumerate(containers):
            parsed = (
                self._parse_indexed(container, index=i)
                if self._indexed_mode
                else self._parse_classic(container, index=i)
            )
            if parsed:
                events.append(parsed)

        log.info("venue_scraper_fetched", club=self.club_id, count=len(events))
        return events

    # ── HTML helpers ──────────────────────────────────────────────────────────

    @http_retry
    def _get_html(self, url: str) -> str | None:
        try:
            with httpx.Client(timeout=20, follow_redirects=True, headers=HEADERS) as c:
                r = c.get(url)
                r.raise_for_status()
                return r.text
        except Exception as exc:
            log.error("venue_scraper_fetch_error", url=url, error=str(exc))
            return None

    def _find_event_containers(self, soup: BeautifulSoup) -> list:
        selector = self.selectors.get("event_list", "")
        if not selector:
            log.warning("no_event_list_selector", club=self.club_id)
            return []
        return soup.select(selector)

    # ── Indexed mode (e.g. JetEngine/Elementor cards) ────────────────────────

    def _parse_indexed(self, container, index: int) -> RawEvent | None:
        try:
            fields_selector = self.selectors["fields"]
            fields = [
                el.get_text(strip=True)
                for el in container.select(fields_selector)
                if el.get_text(strip=True)
            ]
            if not fields:
                return None

            title_idx = int(self.selectors.get("title_index", 0))
            date_idx = int(self.selectors.get("date_index", 1))
            comp_idx = self.selectors.get("competition_index")

            title = fields[title_idx] if title_idx < len(fields) else None
            date_text = fields[date_idx] if date_idx < len(fields) else None
            competition = (
                fields[int(comp_idx)]
                if comp_idx is not None and int(comp_idx) < len(fields)
                else None
            )

            if not title:
                return None

            locale = self.selectors.get("date_locale", "")
            if locale == "pt":
                start_date, start_time = _parse_pt_date(date_text or "")
            else:
                start_date, start_time = self._parse_date_text(date_text or "")

            event_type = self._infer_event_type(title, competition)
            external_id = f"scraped_{index}_{self._slugify(title)}"

            home_team, away_team = None, None
            if event_type == EventType.MATCH_HOME:
                for sep in (" x ", " vs ", " VS ", " Vs "):
                    if sep in title:
                        parts = title.split(sep, 1)
                        home_team = parts[0].strip()
                        away_team = parts[1].strip()
                        break

            return RawEvent(
                source_id=self.build_source_id(external_id),
                club_id=self.club_id,
                source_name=self.name,
                title=title,
                event_type=event_type,
                start_date=start_date,
                start_time=start_time,
                competition=competition,
                home_team=home_team,
                away_team=away_team,
                notes="Hora no confirmada" if not start_time else None,
                raw_data={"fields": fields, "html_index": index},
            )
        except Exception as exc:
            log.warning("venue_parse_error", club=self.club_id, index=index, error=str(exc))
            return None

    # ── Classic mode (separate selectors per field) ───────────────────────────

    def _parse_classic(self, container, index: int) -> RawEvent | None:
        try:
            title = self._extract_text(container, "title")
            if not title:
                return None

            date_text = self._extract_text(container, "date")
            start_date, start_time = self._parse_date_text(date_text)
            description = self._extract_text(container, "description")
            event_type = self._infer_event_type(title)
            external_id = f"scraped_{index}_{self._slugify(title)}"

            return RawEvent(
                source_id=self.build_source_id(external_id),
                club_id=self.club_id,
                source_name=self.name,
                title=title,
                event_type=event_type,
                start_date=start_date,
                start_time=start_time,
                notes=description or ("Hora no confirmada" if not start_time else None),
                raw_data={"html_index": index, "raw_date": date_text},
            )
        except Exception as exc:
            log.warning("venue_parse_error", club=self.club_id, error=str(exc))
            return None

    # ── Shared helpers ────────────────────────────────────────────────────────

    def _extract_text(self, container, key: str) -> str:
        selector = self.selectors.get(key, "")
        if not selector:
            return ""
        el = container.select_one(selector)
        return el.get_text(strip=True) if el else ""

    def _parse_date_text(self, text: str) -> tuple[str | None, str | None]:
        if not text:
            return None, None
        try:
            dt = dateparser.parse(text, dayfirst=True, fuzzy=True)
            if dt:
                start_date = dt.date().isoformat()
                start_time = dt.strftime("%H:%M") if dt.hour or dt.minute else None
                return start_date, start_time
        except Exception:
            pass
        return None, None

    @staticmethod
    def _infer_event_type(title: str, competition: str | None = None) -> EventType:
        combined = f"{title} {competition or ''}".lower()
        if any(w in combined for w in ("partido", " x ", " vs ", "copa", "liga",
                                       "campeonato", "torneo", "brasileiro")):
            return EventType.MATCH_HOME
        if any(w in combined for w in ("concierto", "show", "recital", "concert")):
            return EventType.CONCERT
        if "festival" in combined:
            return EventType.FESTIVAL
        if any(w in combined for w in ("congreso", "convención", "congress")):
            return EventType.CONGRESS
        return EventType.OTHER

    @staticmethod
    def _slugify(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", text.lower())[:40]


# ── Portuguese date parser ────────────────────────────────────────────────────

def _parse_pt_date(text: str) -> tuple[str | None, str | None]:
    """
    Parse Portuguese date strings like "22 abr  |  qua  -  19h00".
    Returns (ISO date string, "HH:MM") or (None, None).
    """
    day_match = re.search(r"(\d{1,2})\s+(\w{3})", text)
    time_match = re.search(r"(\d{1,2})h(\d{2})", text)

    if not day_match:
        return None, None

    day = int(day_match.group(1))
    month = _PT_MONTHS.get(day_match.group(2).lower())
    if not month:
        return None, None

    today = date.today()
    year = today.year
    try:
        event_date = date(year, month, day)
        if event_date < today - timedelta(days=7):
            event_date = date(year + 1, month, day)
    except ValueError:
        return None, None

    start_time = None
    if time_match:
        h, m = int(time_match.group(1)), time_match.group(2)
        start_time = f"{h:02d}:{m}"

    return event_date.isoformat(), start_time
