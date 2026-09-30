from datetime import UTC

import pytest

from app.services.meet_command_parser import MeetCommandError, parse_meet_command


class TestParseMeetCommand:
    def test_parses_a_well_formed_command(self):
        participants, duration, start, end = parse_meet_command(
            "<@U1> <@U2> 30 | 2026-09-10T09:00+00:00 | 2026-09-10T17:00+00:00"
        )
        assert participants == ["U1", "U2"]
        assert duration == 30
        assert start.tzinfo is UTC
        assert end.isoformat() == "2026-09-10T17:00:00+00:00"

    def test_converts_non_utc_offsets(self):
        _, _, start, _ = parse_meet_command(
            "<@U1> 30 | 2026-09-10T09:00+05:30 | 2026-09-10T17:00+05:30"
        )
        assert start.isoformat() == "2026-09-10T03:30:00+00:00"

    def test_rejects_wrong_number_of_pipe_segments(self):
        with pytest.raises(MeetCommandError, match="Usage"):
            parse_meet_command("<@U1> 30 | 2026-09-10T09:00+00:00")

    def test_rejects_no_participants(self):
        with pytest.raises(MeetCommandError, match="Mention at least one"):
            parse_meet_command("30 | 2026-09-10T09:00+00:00 | 2026-09-10T17:00+00:00")

    def test_rejects_unparseable_duration(self):
        with pytest.raises(MeetCommandError, match="duration"):
            parse_meet_command(
                "<@U1> half an hour | 2026-09-10T09:00+00:00 | 2026-09-10T17:00+00:00"
            )

    def test_rejects_more_than_7_other_participants(self):
        mentions = " ".join(f"<@U{i}>" for i in range(8))
        with pytest.raises(MeetCommandError, match="2-8 participants"):
            parse_meet_command(f"{mentions} 30 | 2026-09-10T09:00+00:00 | 2026-09-10T17:00+00:00")

    def test_rejects_timezone_naive_window(self):
        with pytest.raises(MeetCommandError, match="no timezone"):
            parse_meet_command("<@U1> 30 | 2026-09-10T09:00 | 2026-09-10T17:00+00:00")

    def test_rejects_end_before_start(self):
        with pytest.raises(MeetCommandError, match="after"):
            parse_meet_command("<@U1> 30 | 2026-09-10T17:00+00:00 | 2026-09-10T09:00+00:00")


class TestAbsoluteWindowBounds:
    @pytest.mark.parametrize(
        ("start", "end"),
        [
            ("2000-01-01T00:00:00+00:00", "2000-01-01T01:00:00+00:00"),
            ("2099-12-31T23:00:00+00:00", "2100-01-01T00:00:00+00:00"),
        ],
    )
    def test_window_inside_the_bounds_parses(self, start, end):
        _, _, parsed_start, parsed_end = parse_meet_command(f"<@U2> 30 | {start} | {end}")
        assert parsed_start.isoformat() == start
        assert parsed_end.isoformat() == end

    @pytest.mark.parametrize(
        ("start", "end"),
        [
            ("1999-12-31T23:00:00+00:00", "2000-01-01T00:30:00+00:00"),
            ("2099-12-31T23:30:00+00:00", "2100-01-01T00:00:01+00:00"),
            ("9999-12-31T18:00:00+00:00", "9999-12-31T23:59:59+00:00"),
        ],
    )
    def test_window_outside_the_bounds_is_rejected(self, start, end):
        with pytest.raises(MeetCommandError, match="2000-01-01 and 2100-01-01"):
            parse_meet_command(f"<@U2> 30 | {start} | {end}")
