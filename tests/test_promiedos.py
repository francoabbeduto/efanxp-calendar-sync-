"""Tests for the Promiedos API client."""

from datetime import date
from unittest.mock import MagicMock, patch

import httpx
import pytest

from efanxp.sources.promiedos import PromiedosClient, PromiedosMatch


SAMPLE_RESPONSE = {
    "leagues": [
        {
            "league": {"id": "hc", "name": "Liga Profesional"},
            "matches": [
                {
                    "id": "abc123",
                    "home_team": {"name": "Boca Juniors"},
                    "away_team": {"name": "River Plate"},
                    "time_to_display": "21:00",
                },
                {
                    "id": "def456",
                    "home_team": {"name": "Vélez"},
                    "away_team": {"name": "Huracán"},
                    "time_to_display": "18:30",
                },
            ],
        },
        {
            "league": {"id": "gea", "name": "Copa Argentina"},
            "matches": [
                {
                    "id": "ghi789",
                    "home_team": {"name": "San Lorenzo"},
                    "away_team": {"name": "Estudiantes"},
                    "time_to_display": "00:00",  # TBD
                },
            ],
        },
    ]
}


def _mock_response(data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    return resp


class TestPromiedosClientParsing:
    def test_parses_matches_from_multiple_leagues(self):
        client = PromiedosClient()
        matches = client._parse(SAMPLE_RESPONSE)

        assert len(matches) == 3
        assert all(isinstance(m, PromiedosMatch) for m in matches)

    def test_match_fields_populated(self):
        client = PromiedosClient()
        matches = client._parse(SAMPLE_RESPONSE)

        boca_match = next(m for m in matches if m.home_team == "Boca Juniors")
        assert boca_match.match_id == "abc123"
        assert boca_match.away_team == "River Plate"
        assert boca_match.start_time == "21:00"
        assert boca_match.league_name == "Liga Profesional"
        assert boca_match.league_id == "hc"

    def test_zero_zero_time_becomes_none(self):
        """'00:00' signals TBD in Promiedos — must be stored as None."""
        client = PromiedosClient()
        matches = client._parse(SAMPLE_RESPONSE)

        san_lorenzo = next(m for m in matches if m.home_team == "San Lorenzo")
        assert san_lorenzo.start_time is None

    def test_normal_time_preserved(self):
        client = PromiedosClient()
        matches = client._parse(SAMPLE_RESPONSE)

        velez = next(m for m in matches if m.home_team == "Vélez")
        assert velez.start_time == "18:30"

    def test_empty_leagues_returns_empty_list(self):
        client = PromiedosClient()
        assert client._parse({}) == []
        assert client._parse({"leagues": []}) == []

    def test_match_without_teams_skipped(self):
        data = {
            "leagues": [{
                "league": {"id": "hc", "name": "Liga Profesional"},
                "matches": [
                    {"id": "x1", "home_team": {"name": ""}, "away_team": {"name": "River"}},
                    {"id": "x2", "home_team": None, "away_team": {"name": "River"}},
                ],
            }]
        }
        client = PromiedosClient()
        assert client._parse(data) == []


class TestPromiedosClientFetch:
    def test_successful_fetch_returns_matches(self):
        client = PromiedosClient()
        with patch("efanxp.sources.promiedos.httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__.return_value.get.return_value = (
                _mock_response(SAMPLE_RESPONSE)
            )
            result = client.get_matches(date(2024, 8, 15))

        assert len(result) == 3

    def test_result_is_cached_on_second_call(self):
        client = PromiedosClient()
        with patch("efanxp.sources.promiedos.httpx.Client") as mock_cls:
            mock_get = mock_cls.return_value.__enter__.return_value.get
            mock_get.return_value = _mock_response(SAMPLE_RESPONSE)

            client.get_matches(date(2024, 8, 15))
            client.get_matches(date(2024, 8, 15))

        assert mock_get.call_count == 1

    def test_different_dates_not_shared_in_cache(self):
        client = PromiedosClient()
        with patch("efanxp.sources.promiedos.httpx.Client") as mock_cls:
            mock_get = mock_cls.return_value.__enter__.return_value.get
            mock_get.return_value = _mock_response(SAMPLE_RESPONSE)

            client.get_matches(date(2024, 8, 15))
            client.get_matches(date(2024, 8, 16))

        assert mock_get.call_count == 2

    def test_http_error_returns_empty_list(self):
        client = PromiedosClient()
        with patch("efanxp.sources.promiedos.httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__.return_value.get.return_value = (
                _mock_response({}, status_code=403)
            )
            result = client.get_matches(date(2024, 8, 15))

        assert result == []

    def test_network_error_returns_empty_list(self):
        client = PromiedosClient()
        with patch("efanxp.sources.promiedos.httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__.return_value.get.side_effect = (
                httpx.ConnectError("connection refused")
            )
            result = client.get_matches(date(2024, 8, 15))

        assert result == []

    def test_error_result_is_cached_to_avoid_hammering(self):
        """A failed fetch should be cached as [] so we don't retry on every event."""
        client = PromiedosClient()
        with patch("efanxp.sources.promiedos.httpx.Client") as mock_cls:
            mock_get = mock_cls.return_value.__enter__.return_value.get
            mock_get.side_effect = httpx.ConnectError("refused")

            client.get_matches(date(2024, 8, 15))
            client.get_matches(date(2024, 8, 15))

        assert mock_get.call_count == 1
