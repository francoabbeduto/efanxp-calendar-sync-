"""
Deduplication strategy.

Primary key: source_id (built as "{adapter}_{club_id}_{external_id}").
This is stable across runs — the same event always gets the same source_id.

Cross-source dedup (when the same match appears in multiple adapters):
We keep the highest-priority source. Priority is defined by the order of
sources in clubs.yaml; the first source wins.

Within-run dedup: if two adapters return the same match (same club + date +
opponent), we keep only the first one encountered (highest priority source).
"""

from __future__ import annotations

from efanxp.models import EventType, RawEvent


def dedup_events(events: list[RawEvent]) -> list[RawEvent]:
    """
    Remove duplicates from a mixed list of events (possibly from multiple sources).
    Preserves order — first occurrence wins.
    """
    seen_source_ids: set[str] = set()
    seen_match_keys: set[tuple] = set()
    result: list[RawEvent] = []

    for ev in events:
        # 1. Primary dedup: exact source_id
        if ev.source_id in seen_source_ids:
            continue

        # 2. Cross-source dedup: same club + date + opponent combo
        if ev.event_type in (EventType.MATCH_HOME, EventType.MATCH_AWAY):
            match_key = _match_key(ev)
            if match_key in seen_match_keys:
                continue
            seen_match_keys.add(match_key)

        seen_source_ids.add(ev.source_id)
        result.append(ev)

    return result


def _match_key(ev: RawEvent) -> tuple:
    """Canonical key for a match: (club_id, event_type, date, away_team_slug)."""
    away = _slug(ev.away_team or ev.title)
    return (ev.club_id, ev.event_type.value, ev.start_date or "unknown", away)


def _slug(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]", "", text.lower())[:30]
