"""Abstract base class for all source adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from efanxp.models import RawEvent


class BaseSource(ABC):
    """
    Each adapter receives one club config dict and returns a list of RawEvents.
    Adapters must be stateless — no DB, no calendar — pure fetch + parse.
    """

    name: str = "base"

    def __init__(self, club_id: str, source_config: dict[str, Any]):
        self.club_id = club_id
        self.config = source_config

    @abstractmethod
    def fetch(self, lookahead_days: int, lookback_days: int) -> list[RawEvent]:
        """Fetch events for this club from the external source."""
        ...

    def build_source_id(self, external_id: str) -> str:
        return f"{self.name}_{self.club_id}_{external_id}"

    def is_enabled(self) -> bool:
        return self.config.get("enabled", True)
