"""Google's implementation of CalendarProvider. `calendar.freebusy` only for
availability (returns *when* someone is busy, never event titles/attendees —
see ARCHITECTURE.md's security boundary), `calendar.events` only to book.

Least-privilege by design: `freebusy` needs Google verification only, not the
CASA security assessment that a broader `calendar.readonly` scope triggers.
"""

from datetime import datetime
from urllib.parse import urlencode

import httpx

from app.calendar.provider import BusyBlock, InvalidGrantError

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GOOGLE_FREEBUSY_URL = "https://www.googleapis.com/calendar/v3/freeBusy"
GOOGLE_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"

# freebusy for availability, events only to book — see this module's docstring.
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar.freebusy",
    "https://www.googleapis.com/auth/calendar.events",
]


def build_authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "state": state,
        "access_type": "offline",  # required to get a refresh_token back
        "prompt": "consent",  # forces a refresh_token even on re-consent
    }
    return f"{GOOGLE_AUTHORIZE_URL}?{urlencode(params)}"


async def fetch_user_email(http_client: httpx.AsyncClient, access_token: str) -> str:
    """Called once at link time — the linked email is stored so meeting
    booking can invite this person as a calendar attendee later."""
    response = await http_client.get(
        GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
    )
    response.raise_for_status()
    body = response.json()
    try:
        return body["email"]
    except KeyError as exc:
        raise GoogleOAuthError(f"unexpected userinfo response shape: missing {exc}") from exc


class GoogleOAuthError(Exception):
    """Google's own API returned an error unrelated to an expired grant —
    never silently swallowed, never confused with InvalidGrantError."""


async def exchange_code_for_tokens(
    http_client: httpx.AsyncClient,
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> tuple[str, str]:
    """The initial account-link exchange. Returns (refresh_token, access_token)."""
    response = await http_client.post(
        GOOGLE_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        },
    )
    body = response.json()
    if response.status_code != 200:
        raise GoogleOAuthError(f"Google token exchange failed: {body.get('error', body)}")

    try:
        return body["refresh_token"], body["access_token"]
    except KeyError as exc:
        raise GoogleOAuthError(f"unexpected Google token response shape: missing {exc}") from exc


async def _get_access_token(
    http_client: httpx.AsyncClient, *, client_id: str, client_secret: str, refresh_token: str
) -> str:
    response = await http_client.post(
        GOOGLE_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    body = response.json()
    if response.status_code != 200:
        if body.get("error") == "invalid_grant":
            raise InvalidGrantError("Google refresh token is no longer valid")
        raise GoogleOAuthError(f"Google token refresh failed: {body.get('error', body)}")

    try:
        return body["access_token"]
    except KeyError as exc:
        raise GoogleOAuthError(f"unexpected Google token response shape: missing {exc}") from exc


class GoogleCalendarProvider:
    def __init__(self, http_client: httpx.AsyncClient, *, client_id: str, client_secret: str):
        self._http_client = http_client
        self._client_id = client_id
        self._client_secret = client_secret

    async def _access_token(self, refresh_token: str) -> str:
        return await _get_access_token(
            self._http_client,
            client_id=self._client_id,
            client_secret=self._client_secret,
            refresh_token=refresh_token,
        )

    async def get_busy_blocks(
        self, refresh_token: str, window_start_utc: datetime, window_end_utc: datetime
    ) -> list[BusyBlock]:
        access_token = await self._access_token(refresh_token)
        response = await self._http_client.post(
            GOOGLE_FREEBUSY_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "timeMin": window_start_utc.isoformat(),
                "timeMax": window_end_utc.isoformat(),
                "items": [{"id": "primary"}],
            },
        )
        response.raise_for_status()
        body = response.json()

        try:
            busy_periods = body["calendars"]["primary"]["busy"]
        except KeyError as exc:
            raise GoogleOAuthError(f"unexpected freeBusy response shape: missing {exc}") from exc

        return [
            BusyBlock(
                start_utc=datetime.fromisoformat(period["start"]),
                end_utc=datetime.fromisoformat(period["end"]),
            )
            for period in busy_periods
        ]

    async def create_event(
        self,
        refresh_token: str,
        *,
        title: str,
        start_utc: datetime,
        end_utc: datetime,
        attendee_emails: list[str],
    ) -> str:
        access_token = await self._access_token(refresh_token)
        response = await self._http_client.post(
            GOOGLE_EVENTS_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "summary": title,
                "start": {"dateTime": start_utc.isoformat(), "timeZone": "UTC"},
                "end": {"dateTime": end_utc.isoformat(), "timeZone": "UTC"},
                "attendees": [{"email": email} for email in attendee_emails],
            },
        )
        response.raise_for_status()
        body = response.json()

        try:
            return body["id"]
        except KeyError as exc:
            raise GoogleOAuthError(
                f"unexpected events.insert response shape: missing {exc}"
            ) from exc
