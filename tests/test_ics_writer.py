"""Tests for ICS file generation."""

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from icalendar import Calendar

from efanxp.ics_writer import ICSWriter
from efanxp.models import EventRecord, EventStatus, EventType


def _make_record(
    source_id="test_001",
    club_id="boca-juniors",
    title="Boca Juniors vs River Plate",
    event_type=EventType.MATCH_HOME.value,
    start_date="2024-08-15",
    start_time="21:00",
    timezone_str="America/Argentina/Buenos_Aires",
    status=EventStatus.SCHEDULED.value,
    venue_name="La Bombonera",
) -> EventRecord:
    rec = EventRecord()
    rec.source_id = source_id
    rec.club_id = club_id
    rec.source_name = "test"
    rec.title = title
    rec.event_type = event_type
    rec.start_date = start_date
    rec.start_time = start_time
    rec.end_time = None
    rec.timezone = timezone_str
    rec.status = status
    rec.venue_name = venue_name
    rec.competition = "Liga Profesional"
    rec.country = "AR"
    rec.notes = None
    rec.updated_at = datetime.now(timezone.utc)
    return rec


def _parse_ics(path: Path) -> Calendar:
    return Calendar.from_ical(path.read_bytes())


def test_writes_combined_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        records = [_make_record(), _make_record("test_002", club_id="river-plate",
                                                 title="River vs Boca")]
        writer = ICSWriter(Path(tmpdir))
        written = writer.write_all(records)
        assert "efanxp-all.ics" in written
        assert (Path(tmpdir) / "efanxp-all.ics").exists()


def test_writes_per_club_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        records = [
            _make_record("id1", club_id="boca-juniors"),
            _make_record("id2", club_id="river-plate"),
        ]
        writer = ICSWriter(Path(tmpdir))
        written = writer.write_all(records)
        assert "efanxp-boca-juniors.ics" in written
        assert "efanxp-river-plate.ics" in written


def test_event_has_stable_uid():
    with tempfile.TemporaryDirectory() as tmpdir:
        rec = _make_record(source_id="thesportsdb_boca-juniors_12345")
        writer = ICSWriter(Path(tmpdir))
        writer.write_all([rec])
        cal = _parse_ics(Path(tmpdir) / "efanxp-all.ics")
        events = [c for c in cal.walk() if c.name == "VEVENT"]
        assert len(events) == 1
        assert "thesportsdb_boca-juniors_12345@efanxp.com" in str(events[0]["uid"])


def test_timed_event_has_datetime():
    with tempfile.TemporaryDirectory() as tmpdir:
        rec = _make_record(start_date="2024-08-15", start_time="21:00")
        writer = ICSWriter(Path(tmpdir))
        writer.write_all([rec])
        cal = _parse_ics(Path(tmpdir) / "efanxp-all.ics")
        events = [c for c in cal.walk() if c.name == "VEVENT"]
        # DTSTART should be a datetime (not a date) for timed events
        dtstart = events[0]["dtstart"].dt
        assert hasattr(dtstart, "hour"), "Expected datetime, got date"


def test_allday_event_when_no_time():
    with tempfile.TemporaryDirectory() as tmpdir:
        rec = _make_record(start_date="2024-08-15", start_time=None)
        writer = ICSWriter(Path(tmpdir))
        writer.write_all([rec])
        cal = _parse_ics(Path(tmpdir) / "efanxp-all.ics")
        events = [c for c in cal.walk() if c.name == "VEVENT"]
        dtstart = events[0]["dtstart"].dt
        from datetime import date
        assert type(dtstart) is date, "Expected all-day date, got datetime"


def test_cancelled_event_has_cancelled_status():
    with tempfile.TemporaryDirectory() as tmpdir:
        rec = _make_record(status=EventStatus.CANCELLED.value)
        writer = ICSWriter(Path(tmpdir))
        writer.write_all([rec])
        cal = _parse_ics(Path(tmpdir) / "efanxp-all.ics")
        events = [c for c in cal.walk() if c.name == "VEVENT"]
        assert str(events[0]["status"]) == "CANCELLED"


def test_efanxp_custom_properties():
    with tempfile.TemporaryDirectory() as tmpdir:
        rec = _make_record()
        writer = ICSWriter(Path(tmpdir))
        writer.write_all([rec])
        cal = _parse_ics(Path(tmpdir) / "efanxp-all.ics")
        events = [c for c in cal.walk() if c.name == "VEVENT"]
        ev = events[0]
        assert "x-efanxp-club" in ev
        assert "x-efanxp-source-id" in ev


def test_empty_records_writes_empty_calendar():
    with tempfile.TemporaryDirectory() as tmpdir:
        writer = ICSWriter(Path(tmpdir))
        written = writer.write_all([])
        assert "efanxp-all.ics" in written
        cal = _parse_ics(Path(tmpdir) / "efanxp-all.ics")
        events = [c for c in cal.walk() if c.name == "VEVENT"]
        assert len(events) == 0
