from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.calendar.google_calendar import (
    GOOGLE_EVENTS_URL,
    GOOGLE_FREEBUSY_URL,
    GOOGLE_TOKEN_URL,
    GoogleCalendarProvider,
    GoogleOAuthError,
)
from app.calendar.provider import InvalidGrantError


def _mock_token_ok():
    respx.post(GOOGLE_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "ya29.fake"})
    )


class TestGoogleCalendarProvider:
    @respx.mock
    async def test_get_busy_blocks_parses_the_response(self):
        _mock_token_ok()
        respx.post(GOOGLE_FREEBUSY_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "calendars": {
                        "primary": {
                            "busy": [
                                {
                                    "start": "2026-09-07T09:00:00+00:00",
                                    "end": "2026-09-07T10:00:00+00:00",
                                }
                            ]
                        }
                    }
                },
            )
        )

        async with httpx.AsyncClient() as http_client:
            provider = GoogleCalendarProvider(http_client, client_id="x", client_secret="y")
            blocks = await provider.get_busy_blocks(
                "refresh-token",
                datetime(2026, 9, 7, 0, 0, tzinfo=UTC),
                datetime(2026, 9, 8, 0, 0, tzinfo=UTC),
            )

        assert len(blocks) == 1
        assert blocks[0].start_utc == datetime(2026, 9, 7, 9, 0, tzinfo=UTC)

    @respx.mock
    async def test_invalid_grant_raises_the_typed_error(self):
        respx.post(GOOGLE_TOKEN_URL).mock(
            return_value=httpx.Response(400, json={"error": "invalid_grant"})
        )

        async with httpx.AsyncClient() as http_client:
            provider = GoogleCalendarProvider(http_client, client_id="x", client_secret="y")
            with pytest.raises(InvalidGrantError):
                await provider.get_busy_blocks(
                    "dead-refresh-token",
                    datetime(2026, 9, 7, 0, 0, tzinfo=UTC),
                    datetime(2026, 9, 8, 0, 0, tzinfo=UTC),
                )

    @respx.mock
    async def test_create_event_returns_the_event_id(self):
        _mock_token_ok()
        respx.post(GOOGLE_EVENTS_URL).mock(
            return_value=httpx.Response(200, json={"id": "evt_12345"})
        )

        async with httpx.AsyncClient() as http_client:
            provider = GoogleCalendarProvider(http_client, client_id="x", client_secret="y")
            event_id = await provider.create_event(
                "refresh-token",
                title="Sync",
                start_utc=datetime(2026, 9, 7, 13, 0, tzinfo=UTC),
                end_utc=datetime(2026, 9, 7, 13, 30, tzinfo=UTC),
                attendee_emails=["a@example.com", "b@example.com"],
            )

        assert event_id == "evt_12345"

    @respx.mock
    async def test_unexpected_response_shape_raises_rather_than_returning_garbage(self):
        _mock_token_ok()
        respx.post(GOOGLE_FREEBUSY_URL).mock(return_value=httpx.Response(200, json={}))

        async with httpx.AsyncClient() as http_client:
            provider = GoogleCalendarProvider(http_client, client_id="x", client_secret="y")
            with pytest.raises(GoogleOAuthError, match="unexpected"):
                await provider.get_busy_blocks(
                    "refresh-token",
                    datetime(2026, 9, 7, 0, 0, tzinfo=UTC),
                    datetime(2026, 9, 8, 0, 0, tzinfo=UTC),
                )

    @respx.mock
    async def test_cancel_event_deletes_the_event(self):
        _mock_token_ok()
        delete_route = respx.delete(f"{GOOGLE_EVENTS_URL}/evt_12345").mock(
            return_value=httpx.Response(200, json={})
        )

        async with httpx.AsyncClient() as http_client:
            provider = GoogleCalendarProvider(http_client, client_id="x", client_secret="y")
            await provider.cancel_event("refresh-token", "evt_12345")

        assert delete_route.call_count == 1
