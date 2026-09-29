"""Ties the CalendarProvider, the availability intersection, and Meeting
storage together. "I can't see Bob's calendar yet" is a normal, expected
state (ARCHITECTURE.md) — unlinked or broken-link participants are excluded
from the availability constraint, not treated as a hard error, and reported
back so the caller can prompt them to link.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.calendar.intersection import find_earliest_slot
from app.calendar.provider import CalendarProvider, InvalidGrantError
from app.core.security import TokenCipher
from app.models.meeting import (
    STATUS_BOOKED,
    STATUS_CANCELLED,
    STATUS_PROPOSED,
    Meeting,
    MeetingParticipant,
)
from app.repositories.user_repository import UserRepository


class MeetingConfirmationError(Exception):
    """A proposed meeting can't be booked as requested — not found, not in
    `proposed` status, or the organiser hasn't linked a calendar to book
    through. Never silently books something else instead."""


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
    users = UserRepository(session, team_id)
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
        await users.get_or_create(slack_user_id, tz="UTC")

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
        proposed_start_utc=slot_start,
    )
    session.add(meeting)
    await session.flush()

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


async def confirm_meeting(
    session: AsyncSession,
    provider: CalendarProvider,
    cipher: TokenCipher,
    *,
    team_id: str,
    meeting_id: uuid.UUID,
    requesting_slack_user_id: str,
    title: str,
) -> Meeting:
    """The path a slash command or agent tool actually calls to turn a
    proposed slot into a real calendar event, using the slot persisted by
    propose_meeting rather than re-running the availability search (which
    could legitimately return a different answer by the time this runs).

    Only the organiser may confirm: booking creates the event on *their*
    calendar using *their* credentials, so anyone else doing it would be
    spending the organiser's Google account without their say-so — a real
    authorisation boundary, not a formality.
    """
    result = await session.execute(select(Meeting).filter_by(id=meeting_id, team_id=team_id))
    meeting = result.scalar_one_or_none()
    if meeting is None:
        raise MeetingConfirmationError("No meeting with that id in this workspace.")
    if meeting.organiser_slack_id != requesting_slack_user_id:
        raise MeetingConfirmationError("Only the meeting organiser can book it.")
    if meeting.status != STATUS_PROPOSED:
        raise MeetingConfirmationError(f"Meeting is already {meeting.status}.")
    if meeting.proposed_start_utc is None:
        raise MeetingConfirmationError("Meeting has no proposed time to book.")

    users = UserRepository(session, team_id)
    organiser = await users.get(meeting.organiser_slack_id)
    if (
        organiser is None
        or organiser.google_refresh_token_enc is None
        or organiser.google_link_broken_at
    ):
        raise MeetingConfirmationError("The organiser must link a calendar before booking.")
    organiser_refresh_token = cipher.decrypt(
        organiser.google_refresh_token_enc, organiser.key_version
    )

    participants_result = await session.execute(
        select(MeetingParticipant).filter_by(meeting_id=meeting_id)
    )
    attendee_emails = []
    for row in participants_result.scalars().all():
        participant = await users.get(row.slack_user_id)
        if participant is not None and participant.google_email:
            attendee_emails.append(participant.google_email)

    return await book_meeting(
        session,
        provider,
        cipher,
        meeting=meeting,
        slot_start_utc=meeting.proposed_start_utc,
        organiser_refresh_token=organiser_refresh_token,
        attendee_emails=attendee_emails,
        title=title,
    )


async def cancel_meeting(
    session: AsyncSession,
    provider: CalendarProvider,
    cipher: TokenCipher,
    *,
    team_id: str,
    meeting_id: uuid.UUID,
    requesting_slack_user_id: str,
) -> Meeting:
    result = await session.execute(select(Meeting).filter_by(id=meeting_id, team_id=team_id))
    meeting = result.scalar_one_or_none()
    if meeting is None:
        raise MeetingConfirmationError("No meeting with that id in this workspace.")
    if meeting.organiser_slack_id != requesting_slack_user_id:
        raise MeetingConfirmationError("Only the meeting organiser can cancel it.")
    if meeting.status == STATUS_CANCELLED:
        raise MeetingConfirmationError("Meeting is already cancelled.")
    if meeting.status == STATUS_BOOKED and meeting.google_event_id:
        users = UserRepository(session, team_id)
        organiser = await users.get(meeting.organiser_slack_id)
        if (
            organiser is None
            or organiser.google_refresh_token_enc is None
            or organiser.google_link_broken_at
        ):
            raise MeetingConfirmationError("The organiser must link a calendar before cancelling.")
        organiser_refresh_token = cipher.decrypt(
            organiser.google_refresh_token_enc, organiser.key_version
        )
        await provider.cancel_event(organiser_refresh_token, meeting.google_event_id)
    meeting.status = STATUS_CANCELLED
    await session.flush()
    return meeting
