"""
Generic venue scraper — fetches concert/show events from a venue's website.

Each venue has CSS selectors configured in clubs.yaml under
`sources[].selectors`. This adapter handles the HTML fetch; subclasses or
per-venue configs supply the selectors.

Reliability: LOW — venue websites change frequently. Monitor via the
`efanxp status --scraper-health` command and update selectors as needed.
"""

from __future__ import annotations

from datetime import date
from typing import Any
import re

import httpx
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from efanxp.models import EventType, RawEvent
from efanxp.sources.base import BaseSource
from efanxp.utils.logger import get_logger
from efanxp.utils.retry import http_retry

log = get_logger(__name__)

# User-Agent that avoids trivial bot blocks
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; eFanXP-CalendarBot/1.0; "
        "+https://efanxp.com/calendarbot)"
    )
}


class VenueScraperSource(BaseSource):
    name = "venue_scraper"

    def __init__(self, club_id: str, source_config: dict[str, Any]):
        super().__init__(club_id, source_config)
        self.url: str = source_config["url"]
        self.selectors: dict[str, str] = source_config.get("selectors", {})

    def is_enabled(self) -> bool:
        return self.config.get("enabled", False)  # opt-in per club

    def fetch(self, lookahead_days: int = 90, lookback_days: int = 7) -> list[RawEvent]:
        html = self._get_html(self.url)
        if not html:
            return []

        soup = BeautifulSoup(html, "lxml")
        event_containers = self._find_event_containers(soup)

        events: list[RawEvent] = []
        for i, container in enumerate(event_containers):
            parsed = self._parse_container(container, index=i)
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

    def _parse_container(self, container, index: int) -> RawEvent | None:
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
    def _infer_event_type(title: str) -> EventType:
        title_lower = title.lower()
        if any(w in title_lower for w in ("partido", "vs", "copa", "liga", "torneo")):
            return EventType.MATCH_HOME
        if any(w in title_lower for w in ("concierto", "show", "recital", "concert")):
            return EventType.CONCERT
        if "festival" in title_lower:
            return EventType.FESTIVAL
        if any(w in title_lower for w in ("congreso", "convención", "congress")):
            return EventType.CONGRESS
        return EventType.OTHER

    @staticmethod
    def _slugify(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", text.lower())[:40]
