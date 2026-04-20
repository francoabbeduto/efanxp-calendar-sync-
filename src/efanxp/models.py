"""Data models: Pydantic (validation/transport) + SQLAlchemy (persistence)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import JSON, DateTime, Index, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# ─── Enums ────────────────────────────────────────────────────────────────────

class EventType(str, Enum):
    MATCH_HOME = "match_home"
    MATCH_AWAY = "match_away"    # fetched but not synced to calendar
    CONCERT = "concert"
    SHOW = "show"
    FESTIVAL = "festival"
    CONGRESS = "congress"
    OTHER = "other"


class EventStatus(str, Enum):
    SCHEDULED = "scheduled"
    CONFIRMED = "confirmed"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"
    FINISHED = "finished"
    TBD = "tbd"           # date/time not yet confirmed


class SyncAction(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    CANCEL = "cancel"
    SKIP = "skip"         # no changes detected
    ERROR = "error"


# ─── Pydantic models (transport / validation) ─────────────────────────────────

class RawEvent(BaseModel):
    """Normalised event as returned by any source adapter."""

    # Stable ID: built by the adapter as "{adapter}_{club_id}_{source_event_id}"
    source_id: str
    club_id: str
    source_name: str        # e.g. "thesportsdb", "venue_scraper"

    title: str
    event_type: EventType

    # Both can be None when TBD; date alone is valid (kickoff TBD)
    start_date: Optional[str] = None          # ISO date "2024-08-15"
    start_time: Optional[str] = None          # "HH:MM" local time or null
    end_time: Optional[str] = None
    timezone: str = "UTC"

    home_team: Optional[str] = None
    away_team: Optional[str] = None
    competition: Optional[str] = None
    venue_name: Optional[str] = None
    country: Optional[str] = None

    status: EventStatus = EventStatus.SCHEDULED
    notes: Optional[str] = None
    raw_data: Dict[str, Any] = {}

    @field_validator("source_id")
    @classmethod
    def source_id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("source_id must not be empty")
        return v.strip()

    @model_validator(mode="after")
    def warn_missing_date(self) -> "RawEvent":
        if self.start_date is None and self.status not in (
            EventStatus.TBD, EventStatus.CANCELLED
        ):
            if self.notes is None:
                self.notes = "Date TBD"
        return self

    def fingerprint(self) -> str:
        """Hash of the fields that, if changed, require a calendar update."""
        payload = {
            "title": self.title,
            "start_date": self.start_date,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "status": self.status,
            "venue_name": self.venue_name,
            "competition": self.competition,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()[:16]


class SyncResult(BaseModel):
    club_id: str
    source_id: str
    action: SyncAction
    google_event_id: Optional[str] = None
    error: Optional[str] = None


# ─── SQLAlchemy models (persistence) ─────────────────────────────────────────

class Base(DeclarativeBase):
    pass


class EventRecord(Base):
    """Persisted state for every event we've seen."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Stable external identity
    source_id: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    club_id: Mapped[str] = mapped_column(String(100), nullable=False)
    source_name: Mapped[str] = mapped_column(String(50), nullable=False)

    # Event data
    title: Mapped[str] = mapped_column(String(500))
    event_type: Mapped[str] = mapped_column(String(50))
    start_date: Mapped[Optional[str]] = mapped_column(String(10))
    start_time: Mapped[Optional[str]] = mapped_column(String(5))
    end_time: Mapped[Optional[str]] = mapped_column(String(5))
    timezone: Mapped[str] = mapped_column(String(50), default="UTC")
    home_team: Mapped[Optional[str]] = mapped_column(String(200))
    away_team: Mapped[Optional[str]] = mapped_column(String(200))
    competition: Mapped[Optional[str]] = mapped_column(String(200))
    venue_name: Mapped[Optional[str]] = mapped_column(String(200))
    country: Mapped[Optional[str]] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default="scheduled")
    notes: Mapped[Optional[str]] = mapped_column(Text)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict)

    # Google Calendar state
    google_event_id: Mapped[Optional[str]] = mapped_column(String(200))
    last_fingerprint: Mapped[Optional[str]] = mapped_column(String(32))

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    __table_args__ = (
        Index("ix_events_club_date", "club_id", "start_date"),
        Index("ix_events_gcal", "google_event_id"),
    )

    @classmethod
    def from_raw(cls, raw: RawEvent) -> "EventRecord":
        return cls(
            source_id=raw.source_id,
            club_id=raw.club_id,
            source_name=raw.source_name,
            title=raw.title,
            event_type=raw.event_type.value,
            start_date=raw.start_date,
            start_time=raw.start_time,
            end_time=raw.end_time,
            timezone=raw.timezone,
            home_team=raw.home_team,
            away_team=raw.away_team,
            competition=raw.competition,
            venue_name=raw.venue_name,
            country=raw.country,
            status=raw.status.value,
            notes=raw.notes,
            raw_data=raw.raw_data,
        )


class SyncLog(Base):
    """One row per sync run, for observability."""

    __tablename__ = "sync_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    club_id: Mapped[Optional[str]] = mapped_column(String(100))  # null = full sync
    events_fetched: Mapped[int] = mapped_column(default=0)
    events_created: Mapped[int] = mapped_column(default=0)
    events_updated: Mapped[int] = mapped_column(default=0)
    events_cancelled: Mapped[int] = mapped_column(default=0)
    events_skipped: Mapped[int] = mapped_column(default=0)
    errors: Mapped[int] = mapped_column(default=0)
    dry_run: Mapped[bool] = mapped_column(default=False)
    error_detail: Mapped[Optional[str]] = mapped_column(Text)
