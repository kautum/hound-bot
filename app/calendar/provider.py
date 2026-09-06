"""The calendar-provider abstraction — decided in Part 13 to make a second
provider (Microsoft Graph) a cheap addition later, even though only Google
is implemented now (Microsoft's free developer tenant is currently blocked;
see the plan). The interface is the spec; a new provider's tests are the
acceptance check.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class BusyBlock:
    start_utc: datetime
    end_utc: datetime


class InvalidGrantError(Exception):
    """The stored refresh token no longer works — password change, revocation,
    or (in Google's unverified "Testing" mode) the 7-day expiry. The caller
    must mark the link broken and prompt the user to re-link; it must not
    retry silently. See ARCHITECTURE.md's OAuth flows section."""


class CalendarProvider(Protocol):
    async def get_busy_blocks(
        self, refresh_token: str, window_start_utc: datetime, window_end_utc: datetime
    ) -> list[BusyBlock]: ...

    async def create_event(
        self,
        refresh_token: str,
        *,
        title: str,
        start_utc: datetime,
        end_utc: datetime,
        attendee_emails: list[str],
    ) -> str:
        """Returns the provider's event ID."""
        ...
