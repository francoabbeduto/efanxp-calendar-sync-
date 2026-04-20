"""Tests for deduplication logic."""

from efanxp.core.deduplicator import dedup_events
from efanxp.models import EventType, RawEvent


def _make(source_id: str, event_type=EventType.MATCH_HOME,
          date="2024-08-15", away_team="River Plate") -> RawEvent:
    return RawEvent(
        source_id=source_id,
        club_id="boca-juniors",
        source_name="test",
        title=f"Boca Juniors vs {away_team}",
        event_type=event_type,
        start_date=date,
        away_team=away_team,
        home_team="Boca Juniors",
    )


def test_no_duplicates_passthrough():
    events = [_make("src1"), _make("src2", date="2024-09-01")]
    result = dedup_events(events)
    assert len(result) == 2


def test_exact_source_id_dedup():
    events = [_make("src1"), _make("src1")]  # exact same ID
    result = dedup_events(events)
    assert len(result) == 1


def test_cross_source_same_match():
    """Same match from two adapters — keep first (highest priority)."""
    e1 = _make("thesportsdb_boca-juniors_100")
    e2 = _make("venue_scraper_boca-juniors_200")  # same club + date + opponent
    result = dedup_events([e1, e2])
    assert len(result) == 1
    assert result[0].source_id == "thesportsdb_boca-juniors_100"


def test_away_matches_not_cross_deduped_with_home():
    """A home match and an away match on the same day are NOT duplicates."""
    home = _make("src1", event_type=EventType.MATCH_HOME)
    away = _make("src2", event_type=EventType.MATCH_AWAY)
    result = dedup_events([home, away])
    assert len(result) == 2


def test_preserves_order():
    a = _make("src1", date="2024-08-01")
    b = _make("src2", date="2024-08-05")
    c = _make("src3", date="2024-08-10")
    result = dedup_events([c, a, b])
    assert [r.source_id for r in result] == ["src3", "src1", "src2"]
