"""The availability-intersection function — Part 0.5's decided product logic:
2-8 participants, 09:00-17:00 Mon-Fri in each participant's *own* timezone,
earliest mutual free slot. This is the piece worth an interview story: it's
the one function that actually has to get cross-timezone correctness right.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.calendar.provider import BusyBlock

SEARCH_GRANULARITY = timedelta(minutes=15)
WORKING_HOURS_START = 9
WORKING_HOURS_END = 17


def _within_working_hours(moment_utc: datetime, tz_name: str) -> bool:
    local = moment_utc.astimezone(ZoneInfo(tz_name))
    if local.weekday() >= 5:  # Saturday, Sunday
        return False
    return WORKING_HOURS_START <= local.hour < WORKING_HOURS_END


def _overlaps_any(start: datetime, end: datetime, busy_blocks: list[BusyBlock]) -> bool:
    return any(start < block.end_utc and block.start_utc < end for block in busy_blocks)


def find_earliest_slot(
    *,
    busy_by_participant: dict[str, list[BusyBlock]],
    timezone_by_participant: dict[str, str],
    duration: timedelta,
    search_window_start_utc: datetime,
    search_window_end_utc: datetime,
) -> datetime | None:
    """Returns the earliest UTC start time where every participant is both
    inside their own working hours and free, for the whole `duration`.
    Returns None if no slot exists in the search window — a real "everyone's
    booked solid" is a legitimate answer, not a bug to work around."""
    if busy_by_participant.keys() != timezone_by_participant.keys():
        raise ValueError(
            "busy_by_participant and timezone_by_participant must cover the same participants"
        )

    candidate = search_window_start_utc
    while candidate + duration <= search_window_end_utc:
        slot_end = candidate + duration
        last_minute = slot_end - timedelta(minutes=1)

        fits_everyone = all(
            _within_working_hours(candidate, timezone_by_participant[participant])
            and _within_working_hours(last_minute, timezone_by_participant[participant])
            and not _overlaps_any(candidate, slot_end, busy_by_participant[participant])
            for participant in busy_by_participant
        )
        if fits_everyone:
            return candidate

        candidate += SEARCH_GRANULARITY

    return None
