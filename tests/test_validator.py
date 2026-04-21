"""Tests for Promiedos cross-validation logic."""

from datetime import date
from unittest.mock import patch

import pytest

from efanxp.core.validator import (
    TIME_MISMATCH_THRESHOLD_MIN,
    ValidationResult,
    _names_match,
    _time_delta_minutes,
    validate_against_promiedos,
)
from efanxp.models import EventStatus, EventType, RawEvent
from efanxp.sources.promiedos import PromiedosMatch


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_event(**kwargs) -> RawEvent:
    defaults = dict(
        source_id="espn_boca-juniors_1",
        club_id="boca-juniors",
        source_name="espn",
        title="Boca Juniors vs River Plate",
        event_type=EventType.MATCH_HOME,
        start_date="2024-08-15",
        start_time="21:00",
        timezone="America/Argentina/Buenos_Aires",
        home_team="Boca Juniors",
        away_team="River Plate",
        competition="Liga Profesional",
        country="AR",
        status=EventStatus.SCHEDULED,
    )
    defaults.update(kwargs)
    return RawEvent(**defaults)


def _pm(home: str, away: str, time: str | None = "21:00") -> PromiedosMatch:
    return PromiedosMatch(
        match_id="x",
        home_team=home,
        away_team=away,
        start_time=time,
        league_name="Liga Profesional",
        league_id="hc",
    )


def _run(events: list[RawEvent], pm_matches: list[PromiedosMatch]) -> list[ValidationResult]:
    """Run validator with mocked Promiedos responses."""
    with patch(
        "efanxp.core.validator.PromiedosClient.get_matches",
        return_value=pm_matches,
    ):
        return validate_against_promiedos(events)


# ── Unit tests: helpers ───────────────────────────────────────────────────────

class TestNamesMatch:
    def test_exact_match(self):
        assert _names_match("Boca Juniors", "Boca Juniors")

    def test_case_insensitive(self):
        assert _names_match("BOCA juniors", "boca juniors")

    def test_accent_normalization(self):
        assert _names_match("Vélez", "Velez")
        assert _names_match("Huracán", "Huracan")
        assert _names_match("Estudiantes", "Estudiantes")

    def test_substring_match(self):
        assert _names_match("River", "River Plate")
        assert _names_match("River Plate", "River")

    def test_no_match(self):
        assert not _names_match("Boca Juniors", "River Plate")

    def test_empty_string(self):
        assert not _names_match("Boca", "")
        assert not _names_match("", "Boca")


class TestTimeDelta:
    def test_zero_delta(self):
        assert _time_delta_minutes("21:00", "21:00") == 0

    def test_thirty_minutes(self):
        assert _time_delta_minutes("21:00", "21:30") == 30

    def test_one_hour(self):
        assert _time_delta_minutes("20:00", "21:00") == 60

    def test_order_independent(self):
        assert _time_delta_minutes("21:30", "21:00") == _time_delta_minutes("21:00", "21:30")

    def test_across_hour_boundary(self):
        assert _time_delta_minutes("20:45", "21:15") == 30


# ── Integration tests: validate_against_promiedos ────────────────────────────

class TestValidateOk:
    def test_matching_times_returns_ok(self):
        event = _make_event(start_time="21:00")
        results = _run([event], [_pm("Boca Juniors", "River Plate", "21:00")])

        assert len(results) == 1
        assert results[0].status == "ok"
        assert results[0].delta_minutes == 0

    def test_small_delta_within_threshold_is_ok(self):
        event = _make_event(start_time="21:00")
        results = _run([event], [_pm("Boca Juniors", "River Plate", "21:15")])

        assert results[0].status == "ok"
        assert results[0].delta_minutes == 15

    def test_delta_exactly_at_threshold_is_ok(self):
        event = _make_event(start_time="21:00")
        results = _run(
            [event],
            [_pm("Boca Juniors", "River Plate", f"21:{TIME_MISMATCH_THRESHOLD_MIN:02d}")],
        )
        assert results[0].status == "ok"


class TestValidateMismatch:
    def test_large_delta_returns_time_mismatch(self):
        event = _make_event(start_time="21:00")
        results = _run([event], [_pm("Boca Juniors", "River Plate", "19:00")])

        assert results[0].status == "time_mismatch"
        assert results[0].delta_minutes == 120
        assert results[0].our_time == "21:00"
        assert results[0].promiedos_time == "19:00"

    def test_delta_just_over_threshold_is_mismatch(self):
        event = _make_event(start_time="21:00")
        results = _run(
            [event],
            [_pm("Boca Juniors", "River Plate", f"21:{TIME_MISMATCH_THRESHOLD_MIN + 1:02d}")],
        )
        assert results[0].status == "time_mismatch"


class TestValidateNotFound:
    def test_no_promiedos_match_returns_not_found(self):
        event = _make_event()
        results = _run([event], [])

        assert results[0].status == "not_found"
        assert results[0].promiedos_time is None

    def test_different_date_promiedos_data_returns_not_found(self):
        event = _make_event(home_team="Boca Juniors", away_team="River Plate")
        results = _run([event], [_pm("San Lorenzo", "Vélez")])

        assert results[0].status == "not_found"


class TestValidateSkipped:
    def test_away_match_skipped(self):
        event = _make_event(event_type=EventType.MATCH_AWAY)
        results = _run([event], [_pm("Boca Juniors", "River Plate")])

        assert results[0].status == "skipped"

    def test_cancelled_event_skipped(self):
        event = _make_event(status=EventStatus.CANCELLED)
        results = _run([event], [_pm("Boca Juniors", "River Plate")])

        assert results[0].status == "skipped"

    def test_finished_event_skipped(self):
        event = _make_event(status=EventStatus.FINISHED)
        results = _run([event], [_pm("Boca Juniors", "River Plate")])

        assert results[0].status == "skipped"

    def test_no_start_date_skipped(self):
        event = _make_event(start_date=None, status=EventStatus.TBD)
        results = _run([event], [])

        assert results[0].status == "skipped"

    def test_venue_event_skipped(self):
        event = RawEvent(
            source_id="venue_bahia_1",
            club_id="bahia",
            source_name="venue_scraper",
            title="Show en Arena",
            event_type=EventType.CONCERT,
            start_date="2024-08-15",
            country="BR",
            status=EventStatus.CONFIRMED,
        )
        results = _run([event], [])
        assert results[0].status == "skipped"


class TestValidateTbdTime:
    def test_our_time_tbd_match_found_returns_ok(self):
        event = _make_event(start_time=None)
        results = _run([event], [_pm("Boca Juniors", "River Plate", "21:00")])

        assert results[0].status == "ok"
        assert results[0].delta_minutes is None
        assert "not available" in results[0].note

    def test_promiedos_time_tbd_match_found_returns_ok(self):
        event = _make_event(start_time="21:00")
        results = _run([event], [_pm("Boca Juniors", "River Plate", None)])

        assert results[0].status == "ok"
        assert results[0].delta_minutes is None

    def test_both_times_tbd_match_found_returns_ok(self):
        event = _make_event(start_time=None)
        results = _run([event], [_pm("Boca Juniors", "River Plate", None)])

        assert results[0].status == "ok"


class TestValidateFuzzyMatching:
    def test_matches_with_accent_in_promiedos_name(self):
        event = _make_event(
            home_team="Velez Sarsfield",
            away_team="Huracan",
            source_id="espn_velez_1",
            club_id="velez",
            start_time="18:30",
        )
        results = _run([event], [_pm("Vélez", "Huracán", "18:30")])

        assert results[0].status == "ok"

    def test_partial_name_match(self):
        event = _make_event(home_team="Estudiantes de La Plata", away_team="San Lorenzo")
        results = _run([event], [_pm("Estudiantes", "San Lorenzo", "21:00")])

        assert results[0].status == "ok"

    def test_home_away_flip_fallback(self):
        """Some sources report home/away differently — validator checks both."""
        event = _make_event(home_team="Boca Juniors", away_team="River Plate")
        results = _run([event], [_pm("River Plate", "Boca Juniors", "21:00")])

        assert results[0].status == "ok"


class TestValidateMultipleEvents:
    def test_returns_one_result_per_event(self):
        events = [
            _make_event(source_id="espn_boca_1", start_time="21:00"),
            _make_event(
                source_id="espn_velez_1",
                club_id="velez",
                home_team="Vélez",
                away_team="Huracán",
                start_time="18:30",
            ),
        ]
        pm_matches = [
            _pm("Boca Juniors", "River Plate", "21:00"),
            _pm("Vélez", "Huracán", "18:30"),
        ]
        results = _run(events, pm_matches)

        assert len(results) == 2
        assert all(r.status == "ok" for r in results)

    def test_mixed_statuses(self):
        events = [
            _make_event(source_id="espn_boca_1", start_time="21:00"),
            _make_event(source_id="espn_river_1", club_id="river-plate",
                        home_team="River Plate", away_team="San Lorenzo", start_time="20:00"),
        ]
        pm_matches = [
            _pm("Boca Juniors", "River Plate", "21:00"),  # ok
            _pm("River Plate", "San Lorenzo", "17:00"),   # mismatch: 180 min off
        ]
        results = _run(events, pm_matches)

        statuses = {r.club_id: r.status for r in results}
        assert statuses["boca-juniors"] == "ok"
        assert statuses["river-plate"] == "time_mismatch"
