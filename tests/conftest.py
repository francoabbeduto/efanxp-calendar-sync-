"""Shared pytest fixtures."""

import pytest
from efanxp.models import EventStatus, EventType, RawEvent


@pytest.fixture
def home_match() -> RawEvent:
    return RawEvent(
        source_id="thesportsdb_boca-juniors_12345",
        club_id="boca-juniors",
        source_name="thesportsdb",
        title="Boca Juniors vs River Plate",
        event_type=EventType.MATCH_HOME,
        start_date="2024-08-15",
        start_time="21:00",
        timezone="America/Argentina/Buenos_Aires",
        home_team="Boca Juniors",
        away_team="River Plate",
        competition="Liga Profesional",
        venue_name="La Bombonera",
        country="AR",
        status=EventStatus.SCHEDULED,
    )


@pytest.fixture
def away_match() -> RawEvent:
    return RawEvent(
        source_id="thesportsdb_boca-juniors_99999",
        club_id="boca-juniors",
        source_name="thesportsdb",
        title="River Plate vs Boca Juniors",
        event_type=EventType.MATCH_AWAY,
        start_date="2024-09-01",
        start_time="20:00",
        timezone="America/Argentina/Buenos_Aires",
        home_team="River Plate",
        away_team="Boca Juniors",
        competition="Liga Profesional",
        status=EventStatus.SCHEDULED,
    )


@pytest.fixture
def tbd_match() -> RawEvent:
    return RawEvent(
        source_id="thesportsdb_boca-juniors_tbd01",
        club_id="boca-juniors",
        source_name="thesportsdb",
        title="Boca Juniors vs Independiente",
        event_type=EventType.MATCH_HOME,
        start_date="2024-10-05",
        start_time=None,  # time TBD
        timezone="America/Argentina/Buenos_Aires",
        home_team="Boca Juniors",
        away_team="Independiente",
        status=EventStatus.SCHEDULED,
    )


@pytest.fixture
def venue_event() -> RawEvent:
    return RawEvent(
        source_id="venue_scraper_bahia_concert01",
        club_id="bahia",
        source_name="venue_scraper",
        title="Djavanear — Arena Fonte Nova",
        event_type=EventType.CONCERT,
        start_date="2024-11-23",
        start_time="20:00",
        timezone="America/Bahia",
        venue_name="Arena Fonte Nova",
        country="BR",
        status=EventStatus.CONFIRMED,
    )
