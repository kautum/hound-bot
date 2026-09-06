"""Deterministic parsing for `/meet` — the same "explicit syntax, fail loud"
approach as task_command_parser.py. Natural language ("sometime next week")
is Phase 5's job, on top of this.
"""

import re
from datetime import UTC, datetime

MENTION_RE = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")

USAGE = (
    "Usage: /meet @user1 @user2 30 | 2026-09-10T09:00+00:00 | 2026-09-10T17:00+00:00\n"
    "(duration in minutes, then the search window as two ISO 8601 timestamps with timezones)"
)


class MeetCommandError(ValueError):
    pass


def _parse_utc_datetime(raw: str, label: str) -> datetime:
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise MeetCommandError(f"Could not parse {label} {raw!r} as ISO 8601") from exc
    if dt.tzinfo is None:
        raise MeetCommandError(
            f"{label} {raw!r} has no timezone — include one explicitly (e.g. 'Z' or '+00:00')"
        )
    return dt.astimezone(UTC)


def parse_meet_command(text: str) -> tuple[list[str], int, datetime, datetime]:
    """Returns (other_participant_slack_ids, duration_minutes, window_start_utc,
    window_end_utc). Raises MeetCommandError on anything ambiguous — never
    guesses at a missing duration or an unspecified timezone."""
    segments = text.split("|")
    if len(segments) != 3:
        raise MeetCommandError(USAGE)
    head, start_raw, end_raw = (segment.strip() for segment in segments)

    participants = MENTION_RE.findall(head)
    if not participants:
        raise MeetCommandError("Mention at least one other participant with @. " + USAGE)

    duration_text = MENTION_RE.sub("", head).strip()
    if not duration_text:
        raise MeetCommandError("Missing meeting duration in minutes. " + USAGE)
    try:
        duration_minutes = int(duration_text.split()[-1])
    except ValueError as exc:
        raise MeetCommandError(
            f"Could not parse duration {duration_text!r} as an integer number of minutes"
        ) from exc

    if not (1 <= len(participants) <= 7):
        raise MeetCommandError("Meetings support 2-8 participants total, including you.")

    window_start = _parse_utc_datetime(start_raw, "window start")
    window_end = _parse_utc_datetime(end_raw, "window end")
    if window_end <= window_start:
        raise MeetCommandError("Window end must be after window start.")

    return participants, duration_minutes, window_start, window_end
