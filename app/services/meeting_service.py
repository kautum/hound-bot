"""Ties the CalendarProvider, the availability intersection, and Meeting
storage together. "I can't see Bob's calendar yet" is a normal, expected
state (ARCHITECTURE.md) — unlinked or broken-link participants are excluded
from the availability constraint, not treated as a hard error, and reported
back so the caller can prompt them to link.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.calendar.intersection import find_earliest_slot
from app.calendar.provider import CalendarProvider, InvalidGrantError
from app.core.security import TokenCipher
from app.models.meeting import STATUS_BOOKED, Meeting
from app.repositories.user_repository import UserRepository


@dataclass
class ProposalResult:
    meeting: Meeting | None
    slot_start_utc: datetime | None
    unavailable_participants: list[str]  # not linked, or link was broken


async def propose_meeting(
    session: AsyncSession,
    provider: CalendarProvider,
    cipher: TokenCipher,
    *,
    team_id: str,
    organiser_slack_id: str,
    participant_slack_ids: list[str],
    duration: timedelta,
    search_window_start_utc: datetime,
    search_window_end_utc: datetime,
) -> ProposalResult:
    users = UserRepository(session)
    busy_by_participant = {}
    timezone_by_participant = {}
    unavailable: list[str] = []

    # The organiser's own availability must be checked too — a caller
    # forgetting to include themselves in participant_slack_ids would
    # otherwise silently produce a slot the organiser can't attend. Dedupe
    # while preserving order, in case the caller included them anyway.
    all_participants = list(dict.fromkeys([organiser_slack_id, *participant_slack_ids]))

    # Everyone needs a `users` row to satisfy MeetingParticipant's FK below,
    # whether or not they've ever linked a calendar — being invited to a
    # meeting is a lighter-weight interaction than linking one.
    for slack_user_id in all_participants:
        await users.get_or_create(slack_user_id, team_id, tz="UTC")

    for slack_user_id in all_participants:
        user = await users.get(slack_user_id)
        if user.google_refresh_token_enc is None or user.google_link_broken_at:
            unavailable.append(slack_user_id)
            continue

        refresh_token = cipher.decrypt(user.google_refresh_token_enc, user.key_version)
        try:
            busy = await provider.get_busy_blocks(
                refresh_token, search_window_start_utc, search_window_end_utc
            )
        except InvalidGrantError:
            await users.mark_google_link_broken(slack_user_id)
            unavailable.append(slack_user_id)
            continue

        busy_by_participant[slack_user_id] = busy
        timezone_by_participant[slack_user_id] = user.tz

    slot_start = None
    if busy_by_participant:
        slot_start = find_earliest_slot(
            busy_by_participant=busy_by_participant,
            timezone_by_participant=timezone_by_participant,
            duration=duration,
            search_window_start_utc=search_window_start_utc,
            search_window_end_utc=search_window_end_utc,
        )

    if slot_start is None:
        return ProposalResult(
            meeting=None, slot_start_utc=None, unavailable_participants=unavailable
        )

    meeting = Meeting(
        team_id=team_id,
        organiser_slack_id=organiser_slack_id,
        duration_min=int(duration.total_seconds() // 60),
    )
    session.add(meeting)
    await session.flush()

    from app.models.meeting import MeetingParticipant

    for slack_user_id in all_participants:
        session.add(
            MeetingParticipant(meeting_id=meeting.id, team_id=team_id, slack_user_id=slack_user_id)
        )
    await session.flush()

    return ProposalResult(
        meeting=meeting, slot_start_utc=slot_start, unavailable_participants=unavailable
    )


async def book_meeting(
    session: AsyncSession,
    provider: CalendarProvider,
    cipher: TokenCipher,
    *,
    meeting: Meeting,
    slot_start_utc: datetime,
    organiser_refresh_token: str,
    attendee_emails: list[str],
    title: str,
) -> Meeting:
    slot_end_utc = slot_start_utc + timedelta(minutes=meeting.duration_min)
    event_id = await provider.create_event(
        organiser_refresh_token,
        title=title,
        start_utc=slot_start_utc,
        end_utc=slot_end_utc,
        attendee_emails=attendee_emails,
    )
    meeting.status = STATUS_BOOKED
    meeting.google_event_id = event_id
    await session.flush()
    return meeting
