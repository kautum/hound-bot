from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet

from app.calendar.provider import BusyBlock, InvalidGrantError
from app.core.security import TokenCipher
from app.models import User, Workspace
from app.services.meeting_service import book_meeting, propose_meeting


class FakeProvider:
    def __init__(
        self, busy_by_token: dict[str, list], raises_invalid_grant_for: set[str] | None = None
    ):
        self._busy_by_token = busy_by_token
        self._raises_for = raises_invalid_grant_for or set()
        self.created_events: list[dict] = []

    async def get_busy_blocks(self, refresh_token, window_start_utc, window_end_utc):
        if refresh_token in self._raises_for:
            raise InvalidGrantError("dead token")
        return self._busy_by_token.get(refresh_token, [])

    async def create_event(self, refresh_token, *, title, start_utc, end_utc, attendee_emails):
        self.created_events.append(
            {"title": title, "start": start_utc, "end": end_utc, "attendees": attendee_emails}
        )
        return "evt_999"


async def _make_workspace(session, team_id: str) -> None:
    session.add(
        Workspace(
            team_id=team_id,
            bot_token_enc=b"irrelevant",
            key_version=1,
            installed_at=datetime.now(UTC),
        )
    )
    await session.flush()


async def _make_linked_user(session, cipher, slack_user_id, team_id, tz, refresh_token, email):
    token_enc, version = cipher.encrypt(refresh_token)
    session.add(
        User(
            slack_user_id=slack_user_id,
            team_id=team_id,
            tz=tz,
            google_refresh_token_enc=token_enc,
            key_version=version,
            google_email=email,
        )
    )
    await session.flush()


class TestProposeMeeting:
    async def test_finds_a_slot_when_all_participants_are_linked_and_free(self, db_session):
        await _make_workspace(db_session, "team-A")
        key = Fernet.generate_key().decode()
        cipher = TokenCipher(keys={1: key}, current_version=1)
        await _make_linked_user(
            db_session, cipher, "U_ALICE", "team-A", "UTC", "tok-alice", "a@x.com"
        )
        await _make_linked_user(
            db_session, cipher, "U_BOB", "team-A", "UTC", "tok-bob", "b@x.com"
        )
        await db_session.commit()

        provider = FakeProvider(busy_by_token={})
        window_start = datetime(2026, 9, 7, 9, 0, tzinfo=UTC)  # a Monday
        window_end = datetime(2026, 9, 7, 17, 0, tzinfo=UTC)

        result = await propose_meeting(
            db_session,
            provider,
            cipher,
            team_id="team-A",
            organiser_slack_id="U_ALICE",
            participant_slack_ids=["U_ALICE", "U_BOB"],
            duration=timedelta(minutes=30),
            search_window_start_utc=window_start,
            search_window_end_utc=window_end,
        )

        assert result.meeting is not None
        assert result.slot_start_utc == window_start
        assert result.unavailable_participants == []

    async def test_organiser_own_availability_is_checked_even_when_not_in_the_participant_list(
        self, db_session
    ):
        """The bug this test exists to catch: a caller (a slash command, an
        agent tool) that only passes the *other* invitees would otherwise
        propose a slot the organiser themselves is double-booked for."""
        await _make_workspace(db_session, "team-A")
        key = Fernet.generate_key().decode()
        cipher = TokenCipher(keys={1: key}, current_version=1)
        await _make_linked_user(
            db_session, cipher, "U_ALICE", "team-A", "UTC", "tok-alice", "a@x.com"
        )
        await db_session.commit()

        busy_9_to_10 = [
            BusyBlock(
                start_utc=datetime(2026, 9, 7, 9, 0, tzinfo=UTC),
                end_utc=datetime(2026, 9, 7, 10, 0, tzinfo=UTC),
            )
        ]
        provider = FakeProvider(busy_by_token={"tok-alice": busy_9_to_10})

        # Note: organiser "U_ALICE" is deliberately NOT included here.
        result = await propose_meeting(
            db_session,
            provider,
            cipher,
            team_id="team-A",
            organiser_slack_id="U_ALICE",
            participant_slack_ids=[],
            duration=timedelta(minutes=30),
            search_window_start_utc=datetime(2026, 9, 7, 9, 0, tzinfo=UTC),
            search_window_end_utc=datetime(2026, 9, 7, 17, 0, tzinfo=UTC),
        )

        assert result.meeting is not None
        # If the organiser's own busy block were ignored, this would be 9:00.
        assert result.slot_start_utc == datetime(2026, 9, 7, 10, 0, tzinfo=UTC)

    async def test_unlinked_participant_is_excluded_but_reported(self, db_session):
        await _make_workspace(db_session, "team-A")
        key = Fernet.generate_key().decode()
        cipher = TokenCipher(keys={1: key}, current_version=1)
        await _make_linked_user(
            db_session, cipher, "U_ALICE", "team-A", "UTC", "tok-alice", "a@x.com"
        )
        await db_session.commit()  # U_BOB never linked at all

        provider = FakeProvider(busy_by_token={})
        window_start = datetime(2026, 9, 7, 9, 0, tzinfo=UTC)
        window_end = datetime(2026, 9, 7, 17, 0, tzinfo=UTC)

        result = await propose_meeting(
            db_session,
            provider,
            cipher,
            team_id="team-A",
            organiser_slack_id="U_ALICE",
            participant_slack_ids=["U_ALICE", "U_BOB"],
            duration=timedelta(minutes=30),
            search_window_start_utc=window_start,
            search_window_end_utc=window_end,
        )

        assert result.meeting is not None  # still books around whoever it can see
        assert result.unavailable_participants == ["U_BOB"]

    async def test_invalid_grant_marks_the_link_broken_and_excludes_the_participant(
        self, db_session
    ):
        await _make_workspace(db_session, "team-A")
        key = Fernet.generate_key().decode()
        cipher = TokenCipher(keys={1: key}, current_version=1)
        await _make_linked_user(
            db_session, cipher, "U_ALICE", "team-A", "UTC", "tok-alice", "a@x.com"
        )
        await _make_linked_user(
            db_session, cipher, "U_BOB", "team-A", "UTC", "tok-bob-dead", "b@x.com"
        )
        await db_session.commit()

        provider = FakeProvider(busy_by_token={}, raises_invalid_grant_for={"tok-bob-dead"})

        result = await propose_meeting(
            db_session,
            provider,
            cipher,
            team_id="team-A",
            organiser_slack_id="U_ALICE",
            participant_slack_ids=["U_ALICE", "U_BOB"],
            duration=timedelta(minutes=30),
            search_window_start_utc=datetime(2026, 9, 7, 9, 0, tzinfo=UTC),
            search_window_end_utc=datetime(2026, 9, 7, 17, 0, tzinfo=UTC),
        )
        await db_session.commit()

        assert result.unavailable_participants == ["U_BOB"]

        from app.repositories.user_repository import UserRepository

        bob = await UserRepository(db_session).get("U_BOB")
        assert bob.google_link_broken_at is not None


class TestBookMeeting:
    async def test_books_the_proposed_slot_and_updates_status(self, db_session):
        await _make_workspace(db_session, "team-A")
        key = Fernet.generate_key().decode()
        cipher = TokenCipher(keys={1: key}, current_version=1)
        await _make_linked_user(
            db_session, cipher, "U_ALICE", "team-A", "UTC", "tok-alice", "a@x.com"
        )
        await db_session.commit()

        provider = FakeProvider(busy_by_token={})
        result = await propose_meeting(
            db_session,
            provider,
            cipher,
            team_id="team-A",
            organiser_slack_id="U_ALICE",
            participant_slack_ids=["U_ALICE"],
            duration=timedelta(minutes=30),
            search_window_start_utc=datetime(2026, 9, 7, 9, 0, tzinfo=UTC),
            search_window_end_utc=datetime(2026, 9, 7, 17, 0, tzinfo=UTC),
        )
        await db_session.commit()
        assert result.meeting is not None

        booked = await book_meeting(
            db_session,
            provider,
            cipher,
            meeting=result.meeting,
            slot_start_utc=result.slot_start_utc,
            organiser_refresh_token="tok-alice",
            attendee_emails=["a@x.com"],
            title="Sync",
        )
        await db_session.commit()

        assert booked.google_event_id == "evt_999"
        from app.models.meeting import STATUS_BOOKED

        assert booked.status == STATUS_BOOKED
        assert provider.created_events[0]["attendees"] == ["a@x.com"]
