"""Tests for event normalization."""

from efanxp.core.normalizer import normalize
from efanxp.models import EventType, RawEvent


def _make(**kwargs) -> RawEvent:
    defaults = dict(
        source_id="test_club_001",
        club_id="boca-juniors",
        source_name="test",
        title="Boca Juniors vs River Plate",
        event_type=EventType.MATCH_HOME,
    )
    defaults.update(kwargs)
    return RawEvent(**defaults)


def test_timezone_filled_from_country():
    ev = _make(timezone="UTC")
    result = normalize(ev, club_country="AR")
    assert result.timezone == "America/Argentina/Buenos_Aires"


def test_timezone_not_overridden_if_set():
    ev = _make(timezone="America/Lima")
    result = normalize(ev, club_country="AR")
    assert result.timezone == "America/Lima"


def test_title_whitespace_cleaned():
    ev = _make(title="  Boca  Juniors  vs   River  ")
    result = normalize(ev)
    assert result.title == "Boca Juniors vs River"


def test_numeric_competition_prefixed():
    ev = _make(competition="12")
    result = normalize(ev)
    assert result.competition == "Fecha 12"


def test_no_country_leaves_utc():
    ev = _make(timezone="UTC")
    result = normalize(ev, club_country=None)
    assert result.timezone == "UTC"


def test_unknown_country_leaves_utc():
    ev = _make(timezone="UTC")
    result = normalize(ev, club_country="XX")
    assert result.timezone == "UTC"
