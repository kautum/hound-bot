from datetime import UTC, datetime, timedelta

from app.calendar.intersection import find_earliest_slot
from app.calendar.provider import BusyBlock


class TestFindEarliestSlot:
    def test_finds_the_earliest_free_slot_when_everyone_is_free(self):
        # Monday 2026-09-07 09:00 UTC — a Monday, both participants in UTC.
        window_start = datetime(2026, 9, 7, 9, 0, tzinfo=UTC)
        window_end = datetime(2026, 9, 7, 17, 0, tzinfo=UTC)

        slot = find_earliest_slot(
            busy_by_participant={"alice": [], "bob": []},
            timezone_by_participant={"alice": "UTC", "bob": "UTC"},
            duration=timedelta(minutes=30),
            search_window_start_utc=window_start,
            search_window_end_utc=window_end,
        )
        assert slot == window_start

    def test_skips_a_busy_block(self):
        window_start = datetime(2026, 9, 7, 9, 0, tzinfo=UTC)
        window_end = datetime(2026, 9, 7, 17, 0, tzinfo=UTC)
        alice_busy = [
            BusyBlock(
                start_utc=datetime(2026, 9, 7, 9, 0, tzinfo=UTC),
                end_utc=datetime(2026, 9, 7, 10, 0, tzinfo=UTC),
            )
        ]

        slot = find_earliest_slot(
            busy_by_participant={"alice": alice_busy, "bob": []},
            timezone_by_participant={"alice": "UTC", "bob": "UTC"},
            duration=timedelta(minutes=30),
            search_window_start_utc=window_start,
            search_window_end_utc=window_end,
        )
        assert slot == datetime(2026, 9, 7, 10, 0, tzinfo=UTC)

    def test_respects_each_participants_own_working_hours_across_timezones(self):
        """The actual hard part: a slot that's 9-5 for one participant might
        be outside working hours for another in a different timezone."""
        # Search a full day in UTC. Alice is UTC (09-17 UTC). Bob is
        # America/New_York (UTC-4 in September, so his 09-17 local is 13-21 UTC).
        # The only mutually valid window is 13:00-17:00 UTC.
        window_start = datetime(2026, 9, 7, 0, 0, tzinfo=UTC)
        window_end = datetime(2026, 9, 8, 0, 0, tzinfo=UTC)

        slot = find_earliest_slot(
            busy_by_participant={"alice": [], "bob": []},
            timezone_by_participant={"alice": "UTC", "bob": "America/New_York"},
            duration=timedelta(minutes=30),
            search_window_start_utc=window_start,
            search_window_end_utc=window_end,
        )
        assert slot == datetime(2026, 9, 7, 13, 0, tzinfo=UTC)

    def test_skips_weekends(self):
        # 2026-09-05 is a Saturday.
        window_start = datetime(2026, 9, 5, 0, 0, tzinfo=UTC)
        window_end = datetime(2026, 9, 7, 17, 0, tzinfo=UTC)  # through Monday

        slot = find_earliest_slot(
            busy_by_participant={"alice": []},
            timezone_by_participant={"alice": "UTC"},
            duration=timedelta(minutes=30),
            search_window_start_utc=window_start,
            search_window_end_utc=window_end,
        )
        assert slot == datetime(2026, 9, 7, 9, 0, tzinfo=UTC)  # the following Monday

    def test_returns_none_when_no_slot_exists(self):
        """Everyone booked solid is a legitimate answer, not a bug."""
        window_start = datetime(2026, 9, 7, 9, 0, tzinfo=UTC)
        window_end = datetime(2026, 9, 7, 9, 30, tzinfo=UTC)  # only 30 min available
        busy = [
            BusyBlock(
                start_utc=datetime(2026, 9, 7, 9, 0, tzinfo=UTC),
                end_utc=datetime(2026, 9, 7, 9, 30, tzinfo=UTC),
            )
        ]

        slot = find_earliest_slot(
            busy_by_participant={"alice": busy},
            timezone_by_participant={"alice": "UTC"},
            duration=timedelta(minutes=30),
            search_window_start_utc=window_start,
            search_window_end_utc=window_end,
        )
        assert slot is None

    def test_handles_2_to_8_participants(self):
        window_start = datetime(2026, 9, 7, 9, 0, tzinfo=UTC)
        window_end = datetime(2026, 9, 7, 17, 0, tzinfo=UTC)
        participants = [f"user{i}" for i in range(8)]

        slot = find_earliest_slot(
            busy_by_participant={p: [] for p in participants},
            timezone_by_participant={p: "UTC" for p in participants},
            duration=timedelta(minutes=30),
            search_window_start_utc=window_start,
            search_window_end_utc=window_end,
        )
        assert slot == window_start
