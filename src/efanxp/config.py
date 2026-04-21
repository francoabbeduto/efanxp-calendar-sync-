from __future__ import annotations

"""Application settings loaded from environment / .env file."""

from pathlib import Path
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[2]  # repo root


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Google Calendar
    google_service_account_file: Path = ROOT / "secrets" / "google-service-account.json"
    google_calendar_id: str = ""

    # TheSportsDB
    thesportsdb_api_key: str = "3"  # "3" is the public free key

    # API-Sports (Football + Rugby — same key covers both)
    api_sports_key: str = ""

    # Sync behaviour
    sync_lookahead_days: int = 90
    sync_lookback_days: int = 7
    sync_cron_expression: str = "0 */6 * * *"

    # Persistence
    database_url: str = f"sqlite:///{ROOT}/data/efanxp.db"

    # Logging
    log_level: str = "INFO"
    log_file: Path = ROOT / "logs" / "efanxp.log"

    # Paths
    clubs_config: Path = ROOT / "config" / "clubs.yaml"

    @field_validator("log_level")
    @classmethod
    def upper_log_level(cls, v: str) -> str:
        return v.upper()


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
