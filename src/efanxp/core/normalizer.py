"""
Normalizes raw events:
- Resolves timezones per country
- Cleans titles
- Infers missing fields where possible
"""

from __future__ import annotations

from efanxp.models import EventType, RawEvent

# Default timezone by country code
COUNTRY_TZ: dict[str, str] = {
    "AR": "America/Argentina/Buenos_Aires",
    "CL": "America/Santiago",
    "PE": "America/Lima",
    "BR": "America/Bahia",
    "UY": "America/Montevideo",
}


def normalize(raw: RawEvent, club_country: str | None = None) -> RawEvent:
    """Returns a copy of raw with normalized/inferred fields."""

    data = raw.model_copy(deep=True)

    # Fill timezone from country if not set or is UTC
    if (data.timezone in (None, "", "UTC")) and club_country:
        tz = COUNTRY_TZ.get(club_country.upper())
        if tz:
            data.timezone = tz

    # Clean title — remove extra whitespace, normalize dashes
    data.title = " ".join(data.title.split())
    data.title = data.title.replace(" - ", " vs ") if (
        data.event_type == EventType.MATCH_HOME and " - " in data.title
    ) else data.title

    # If competition is a round number only, clear it
    if data.competition and data.competition.isdigit():
        data.competition = f"Fecha {data.competition}"

    return data
