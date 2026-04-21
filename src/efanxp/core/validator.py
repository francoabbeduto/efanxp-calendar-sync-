"""
Cross-validates events against Promiedos.

Compares dates and kick-off times of AR football home matches
against Promiedos data and logs discrepancies.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Literal, Optional

from efanxp.models import EventStatus, EventType, RawEvent
from efanxp.sources.promiedos import PromiedosClient, PromiedosMatch
from efanxp.utils.logger import get_logger

log = get_logger(__name__)

TIME_MISMATCH_THRESHOLD_MIN = 30


@dataclass
class ValidationResult:
    source_id: str
    club_id: str
    event_title: str
    status: Literal["ok", "time_mismatch", "not_found", "skipped"]
    our_time: Optional[str]
    promiedos_time: Optional[str]
    delta_minutes: Optional[int]
    note: str


def _normalize(name: str) -> str:
    """Lowercase ASCII, alphanumeric + spaces only."""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9 ]", "", ascii_str.lower()).strip()


def _names_match(a: str, b: str) -> bool:
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def _time_delta_minutes(t1: str, t2: str) -> int:
    h1, m1 = map(int, t1.split(":"))
    h2, m2 = map(int, t2.split(":"))
    return abs((h1 * 60 + m1) - (h2 * 60 + m2))


def _find_promiedos_match(
    event: RawEvent,
    candidates: list[PromiedosMatch],
) -> Optional[PromiedosMatch]:
    """Match an event to a Promiedos entry by team names."""
    club_team = event.home_team or event.club_id
    opponent = event.away_team or ""

    for pm in candidates:
        home_ok = _names_match(club_team, pm.home_team)
        away_ok = not opponent or _names_match(opponent, pm.away_team)
        if home_ok and away_ok:
            return pm
        # Fallback: sources sometimes flip home/away
        if _names_match(club_team, pm.away_team) and (
            not opponent or _names_match(opponent, pm.home_team)
        ):
            return pm
    return None


def validate_against_promiedos(events: list[RawEvent]) -> list[ValidationResult]:
    """
    For each event, attempt to find it in Promiedos and compare the kick-off time.
    Skips non-football, non-home, cancelled/finished, and TBD events.
    """
    results: list[ValidationResult] = []
    client = PromiedosClient()

    for event in events:
        # Only validate scheduled AR football home matches with a known date
        if (
            event.event_type != EventType.MATCH_HOME
            or event.status in (EventStatus.CANCELLED, EventStatus.FINISHED)
            or not event.start_date
        ):
            results.append(ValidationResult(
                source_id=event.source_id,
                club_id=event.club_id,
                event_title=event.title,
                status="skipped",
                our_time=event.start_time,
                promiedos_time=None,
                delta_minutes=None,
                note="not eligible for Promiedos validation",
            ))
            continue

        try:
            target_date = date.fromisoformat(event.start_date)
        except ValueError:
            results.append(ValidationResult(
                source_id=event.source_id,
                club_id=event.club_id,
                event_title=event.title,
                status="skipped",
                our_time=event.start_time,
                promiedos_time=None,
                delta_minutes=None,
                note=f"invalid date: {event.start_date}",
            ))
            continue

        pm_matches = client.get_matches(target_date)
        found = _find_promiedos_match(event, pm_matches)

        if not found:
            log.warning(
                "promiedos_not_found",
                club=event.club_id,
                title=event.title,
                date=event.start_date,
            )
            results.append(ValidationResult(
                source_id=event.source_id,
                club_id=event.club_id,
                event_title=event.title,
                status="not_found",
                our_time=event.start_time,
                promiedos_time=None,
                delta_minutes=None,
                note="match not found in Promiedos for this date",
            ))
            continue

        if event.start_time and found.start_time:
            delta = _time_delta_minutes(event.start_time, found.start_time)
            if delta > TIME_MISMATCH_THRESHOLD_MIN:
                log.warning(
                    "promiedos_time_mismatch",
                    club=event.club_id,
                    title=event.title,
                    date=event.start_date,
                    our_time=event.start_time,
                    promiedos_time=found.start_time,
                    delta_min=delta,
                )
                results.append(ValidationResult(
                    source_id=event.source_id,
                    club_id=event.club_id,
                    event_title=event.title,
                    status="time_mismatch",
                    our_time=event.start_time,
                    promiedos_time=found.start_time,
                    delta_minutes=delta,
                    note=(
                        f"time differs by {delta} min "
                        f"(ours={event.start_time}, promiedos={found.start_time})"
                    ),
                ))
            else:
                log.debug(
                    "promiedos_ok",
                    club=event.club_id,
                    title=event.title,
                    delta_min=delta,
                )
                results.append(ValidationResult(
                    source_id=event.source_id,
                    club_id=event.club_id,
                    event_title=event.title,
                    status="ok",
                    our_time=event.start_time,
                    promiedos_time=found.start_time,
                    delta_minutes=delta,
                    note="times match",
                ))
        else:
            # Match found but one or both times are TBD — can't compare
            results.append(ValidationResult(
                source_id=event.source_id,
                club_id=event.club_id,
                event_title=event.title,
                status="ok",
                our_time=event.start_time,
                promiedos_time=found.start_time,
                delta_minutes=None,
                note="match found in Promiedos; time not available for comparison",
            ))

    return results
