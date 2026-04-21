"""
Main orchestration logic.

Flow: clubs.yaml → sources → normalize → dedup → SQLite → ICS files
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from efanxp.config import Settings, get_settings
from efanxp.core.deduplicator import dedup_events
from efanxp.core.normalizer import normalize
from efanxp.database import (
    init_db,
    session_scope,
    start_sync_log,
    finish_sync_log,
    upsert_event,
)
from efanxp.ics_writer import ICSWriter
from efanxp.models import EventType, RawEvent, EventRecord
from efanxp.sources.api_sports_football import ApiSportsFootballSource
from efanxp.sources.api_sports_rugby import ApiSportsRugbySource
from efanxp.sources.base import BaseSource
from efanxp.sources.espn import ESPNSource
from efanxp.sources.sofascore import SofascoreSource
from efanxp.sources.thesportsdb import TheSportsDBSource
from efanxp.sources.venue_scraper import VenueScraperSource
from efanxp.utils.logger import get_logger
from sqlalchemy import select

log = get_logger(__name__)

ADAPTER_MAP: dict[str, type[BaseSource]] = {
    "thesportsdb": TheSportsDBSource,
    "espn": ESPNSource,
    "sofascore": SofascoreSource,
    "api_sports_football": ApiSportsFootballSource,
    "api_sports_rugby": ApiSportsRugbySource,
    "venue_scraper": VenueScraperSource,
}


@dataclass
class SyncStats:
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: int = 0
    ics_files: list[str] = field(default_factory=list)
    clubs_processed: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"fetched={self.fetched} inserted={self.inserted} "
            f"updated={self.updated} unchanged={self.unchanged} errors={self.errors}"
        )


class Orchestrator:
    def __init__(self, dry_run: bool = False, settings: Settings | None = None):
        self.dry_run = dry_run
        self.settings = settings or get_settings()
        self._clubs: list[dict] = []

    # ── Entry points ──────────────────────────────────────────────────────────

    def run_full_sync(self, club_ids: list[str] | None = None) -> SyncStats:
        init_db()
        clubs = self._load_clubs()
        if club_ids:
            clubs = [c for c in clubs if c["id"] in club_ids]
        if not clubs:
            log.warning("no_clubs_matched", requested=club_ids)
            return SyncStats()

        stats = SyncStats()

        with session_scope() as session:
            sync_log = start_sync_log(
                session,
                club_id=",".join(club_ids) if club_ids else None,
                dry_run=self.dry_run,
            )
            session.flush()  # get sync_log.id without holding a write lock

            try:
                for club in clubs:
                    self._sync_club(club, stats, session)

                # After all clubs: regenerate ICS files from DB
                if not self.dry_run:
                    ics_files = self._write_ics(club_ids, session)
                    stats.ics_files = list(ics_files.keys())

                finish_sync_log(
                    session,
                    sync_log,
                    events_fetched=stats.fetched,
                    events_created=stats.inserted,
                    events_updated=stats.updated,
                    events_skipped=stats.unchanged,
                    errors=stats.errors,
                )

            except Exception as exc:
                sync_log.error_detail = str(exc)
                raise

        log.info("sync_complete", dry_run=self.dry_run, **{
            k: v for k, v in vars(stats).items() if isinstance(v, int)
        })
        return stats

    # ── Per-club logic ────────────────────────────────────────────────────────

    def _sync_club(self, club: dict, stats: SyncStats, session) -> None:
        club_id = club["id"]
        country = club.get("country", "")
        log.info("syncing_club", club=club_id)

        # 1. Fetch from all enabled sources
        raw_events = self._fetch_with_fallback(club)
        stats.fetched += len(raw_events)

        if not raw_events:
            log.warning("no_events_fetched", club=club_id)
            return

        # 2. Keep only home matches + venue events (filter out away matches)
        home_events = [
            e for e in raw_events
            if e.event_type != EventType.MATCH_AWAY
        ]

        # 3. Normalize (timezone, title cleanup, etc.)
        home_events = [normalize(e, country) for e in home_events]

        # 4. Dedup
        home_events = dedup_events(home_events)

        # 5. Persist to DB using the shared session
        for raw in home_events:
            try:
                _record, is_new = upsert_event(session, raw)
                if is_new:
                    stats.inserted += 1
                else:
                    fp_now = raw.fingerprint()
                    if _record.last_fingerprint != fp_now:
                        _record.last_fingerprint = fp_now
                        stats.updated += 1
                    else:
                        stats.unchanged += 1
            except Exception as exc:
                stats.errors += 1
                log.error("db_upsert_error",
                          club=club_id, source_id=raw.source_id, error=str(exc))

        stats.clubs_processed.append(club_id)

    # ── ICS generation ────────────────────────────────────────────────────────

    def _write_ics(self, club_ids: list[str] | None, session) -> dict[str, Path]:
        root = Path(__file__).resolve().parents[3]
        output_dir = root / "public"

        stmt = select(EventRecord)
        if club_ids:
            from sqlalchemy import or_
            stmt = stmt.where(
                or_(*[EventRecord.club_id == cid for cid in club_ids])
            )
        records = list(session.scalars(stmt))

        writer = ICSWriter(output_dir)
        return writer.write_all(records)

    # ── Source management ─────────────────────────────────────────────────────

    def _fetch_with_fallback(self, club: dict) -> list[RawEvent]:
        all_events: list[RawEvent] = []
        for src_cfg in club.get("sources", []):
            adapter_name = src_cfg.get("adapter")
            adapter_cls = ADAPTER_MAP.get(adapter_name)
            if not adapter_cls:
                log.warning("unknown_adapter", adapter=adapter_name)
                continue

            source = adapter_cls(club["id"], src_cfg)
            if not source.is_enabled():
                continue

            try:
                events = source.fetch(
                    lookahead_days=self.settings.sync_lookahead_days,
                    lookback_days=self.settings.sync_lookback_days,
                )
                log.info("source_fetched",
                         club=club["id"], adapter=adapter_name, count=len(events))
                all_events.extend(events)
            except Exception as exc:
                log.error("source_fetch_error",
                          club=club["id"], adapter=adapter_name, error=str(exc))

        return all_events

    def _load_clubs(self) -> list[dict]:
        if not self._clubs:
            with open(self.settings.clubs_config) as f:
                self._clubs = yaml.safe_load(f)["clubs"]
        return self._clubs
