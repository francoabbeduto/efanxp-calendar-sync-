"""
ICS (iCalendar) file generator.

Writes one combined ICS file (all clubs) + one per club into the public/ directory.
Each event gets a stable UID derived from source_id so calendar clients
detect updates rather than creating duplicates on re-import.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import List

import pytz
from icalendar import Calendar, Event, vText

from efanxp.models import EventRecord, EventStatus, EventType
from efanxp.utils.logger import get_logger

log = get_logger(__name__)

# Maps event type → human label for the calendar summary prefix
TYPE_PREFIX: dict[str, str] = {
    EventType.MATCH_HOME.value: "⚽",
    EventType.MATCH_AWAY.value: "⚽",
    EventType.CONCERT.value: "🎵",
    EventType.SHOW.value: "🎭",
    EventType.FESTIVAL.value: "🎪",
    EventType.CONGRESS.value: "🏢",
    EventType.OTHER.value: "📅",
}

STATUS_MAP: dict[str, str] = {
    EventStatus.SCHEDULED.value: "CONFIRMED",
    EventStatus.CONFIRMED.value: "CONFIRMED",
    EventStatus.POSTPONED.value: "CANCELLED",
    EventStatus.CANCELLED.value: "CANCELLED",
    EventStatus.FINISHED.value: "CONFIRMED",
    EventStatus.TBD.value: "TENTATIVE",
}


class ICSWriter:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write_all(self, records: list[EventRecord]) -> dict[str, Path]:
        """
        Write ICS files from a list of EventRecords.
        Returns {filename: path} for all written files.
        """
        # Group by club
        by_club: dict[str, list[EventRecord]] = {}
        for rec in records:
            by_club.setdefault(rec.club_id, []).append(rec)

        written: dict[str, Path] = {}

        # One file per club
        for club_id, club_records in by_club.items():
            cal = self._build_calendar(
                name=f"eFanXP — {club_id.replace('-', ' ').title()}",
                description=f"Fixtures y eventos — {club_id}",
                records=club_records,
            )
            path = self.output_dir / f"efanxp-{club_id}.ics"
            self._write(cal, path)
            written[f"efanxp-{club_id}.ics"] = path
            log.info("ics_written", file=str(path), events=len(club_records))

        # Combined file with all clubs
        all_cal = self._build_calendar(
            name="eFanXP — Todos los clubes",
            description="Fixtures y eventos de todos los clientes eFanXP",
            records=records,
        )
        all_path = self.output_dir / "efanxp-all.ics"
        self._write(all_cal, all_path)
        written["efanxp-all.ics"] = all_path
        log.info("ics_written", file=str(all_path), events=len(records))

        return written

    # ── Builders ──────────────────────────────────────────────────────────────

    def _build_calendar(
        self,
        name: str,
        description: str,
        records: list[EventRecord],
    ) -> Calendar:
        cal = Calendar()
        cal.add("prodid", "-//eFanXP//Calendar Sync//EN")
        cal.add("version", "2.0")
        cal.add("calscale", "GREGORIAN")
        cal.add("method", "PUBLISH")
        cal.add("x-wr-calname", name)
        cal.add("x-wr-caldesc", description)
        cal.add("x-wr-timezone", "UTC")
        cal.add("refresh-interval;value=duration", "PT6H")
        cal.add("x-published-ttl", "PT6H")

        for rec in records:
            event = self._build_event(rec)
            if event:
                cal.add_component(event)

        return cal

    def _build_event(self, rec: EventRecord) -> Event | None:
        try:
            event = Event()

            # Stable UID — same source_id always maps to same UID
            uid = f"{rec.source_id}@efanxp.com"
            event.add("uid", uid)

            # Summary
            prefix = TYPE_PREFIX.get(rec.event_type, "📅")
            event.add("summary", f"{prefix} {rec.title}")

            # Timestamps
            dtstart, dtend = self._build_dt(rec)
            event.add("dtstart", dtstart)
            event.add("dtend", dtend)
            event.add("dtstamp", datetime.now(timezone.utc))
            event.add("last-modified", rec.updated_at or datetime.now(timezone.utc))

            # Location
            if rec.venue_name:
                event.add("location", rec.venue_name)

            # Status
            status_str = STATUS_MAP.get(rec.status, "CONFIRMED")
            event.add("status", status_str)

            # Description — all metadata
            event.add("description", self._build_description(rec))

            # Custom properties for machine readability
            event.add("x-efanxp-source-id", rec.source_id)
            event.add("x-efanxp-club", rec.club_id)
            event.add("x-efanxp-source", rec.source_name)
            event.add("x-efanxp-type", rec.event_type)

            return event

        except Exception as exc:
            log.warning("ics_event_build_error", source_id=rec.source_id, error=str(exc))
            return None

    def _build_dt(self, rec: EventRecord):
        """Returns (dtstart, dtend) as date or datetime objects."""
        from datetime import date, timedelta
        from icalendar import vDate, vDatetime

        DURATION_HOURS = 2

        if not rec.start_date:
            # No date — use far-future placeholder
            d = date(2099, 1, 1)
            return d, d

        start_date = date.fromisoformat(rec.start_date)

        if not rec.start_time:
            # All-day event
            return start_date, start_date

        # Timed event
        tz_name = rec.timezone or "UTC"
        try:
            tz = pytz.timezone(tz_name)
        except pytz.UnknownTimeZoneError:
            tz = pytz.UTC

        h, m = map(int, rec.start_time.split(":"))
        start_naive = datetime(start_date.year, start_date.month, start_date.day, h, m)
        start_dt = tz.localize(start_naive)

        if rec.end_time:
            eh, em = map(int, rec.end_time.split(":"))
            end_naive = datetime(start_date.year, start_date.month, start_date.day, eh, em)
            end_dt = tz.localize(end_naive)
        else:
            end_dt = start_dt + timedelta(hours=DURATION_HOURS)

        return start_dt, end_dt

    def _build_description(self, rec: EventRecord) -> str:
        lines = [
            f"🏟 Venue: {rec.venue_name or 'N/D'}",
            f"🏆 Competencia: {rec.competition or 'N/D'}",
            f"👥 Cliente: {rec.club_id.replace('-', ' ').title()}",
            f"🌐 País: {rec.country or 'N/D'}",
            f"📡 Fuente: {rec.source_name}",
            f"🆔 ID interno: {rec.source_id}",
            f"🔄 Última actualización: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        ]
        if rec.notes:
            lines.append(f"⚠️  Notas: {rec.notes}")
        if rec.status in (EventStatus.POSTPONED.value, EventStatus.CANCELLED.value):
            lines.append(f"🚫 Estado: {rec.status.upper()}")
        return "\n".join(lines)

    @staticmethod
    def _write(cal: Calendar, path: Path) -> None:
        path.write_bytes(cal.to_ical())
