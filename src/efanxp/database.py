"""SQLite persistence layer."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from efanxp.config import get_settings
from efanxp.models import Base, EventRecord, RawEvent, SyncLog


def _make_engine():
    settings = get_settings()
    return create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        echo=False,
    )


_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = _make_engine()
    return _engine


def get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal


def init_db() -> None:
    Base.metadata.create_all(get_engine())


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    factory = get_session_factory()
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ─── Event persistence ────────────────────────────────────────────────────────

def upsert_event(session: Session, raw: RawEvent) -> tuple[EventRecord, bool]:
    """Insert or update an event. Returns (record, is_new)."""
    existing = session.scalar(
        select(EventRecord).where(EventRecord.source_id == raw.source_id)
    )
    if existing is None:
        record = EventRecord.from_raw(raw)
        session.add(record)
        return record, True

    # Update mutable fields
    existing.title = raw.title
    existing.event_type = raw.event_type.value
    existing.start_date = raw.start_date
    existing.start_time = raw.start_time
    existing.end_time = raw.end_time
    existing.timezone = raw.timezone
    existing.home_team = raw.home_team
    existing.away_team = raw.away_team
    existing.competition = raw.competition
    existing.venue_name = raw.venue_name
    existing.status = raw.status.value
    existing.notes = raw.notes
    existing.raw_data = raw.raw_data
    return existing, False


def get_event_by_source_id(session: Session, source_id: str) -> EventRecord | None:
    return session.scalar(
        select(EventRecord).where(EventRecord.source_id == source_id)
    )


def get_events_for_club(session: Session, club_id: str) -> list[EventRecord]:
    return list(
        session.scalars(
            select(EventRecord)
            .where(EventRecord.club_id == club_id)
            .order_by(EventRecord.start_date)
        )
    )


def mark_synced(
    session: Session,
    record: EventRecord,
    google_event_id: str,
    fingerprint: str,
) -> None:
    record.google_event_id = google_event_id
    record.last_fingerprint = fingerprint
    record.last_synced_at = datetime.now(timezone.utc)


# ─── Sync log ─────────────────────────────────────────────────────────────────

def start_sync_log(session: Session, club_id: str | None, dry_run: bool) -> SyncLog:
    log = SyncLog(club_id=club_id, dry_run=dry_run)
    session.add(log)
    session.flush()
    return log


def finish_sync_log(session: Session, log: SyncLog, **counts) -> None:
    log.finished_at = datetime.now(timezone.utc)
    for key, value in counts.items():
        setattr(log, key, value)
