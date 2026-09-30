"""Range/length validation in the command parsers and the agent tool schemas.
Out-of-range input must fail loudly with a clear message, never be clamped."""

import json

import pytest
from pydantic import ValidationError

from app.agent.loop import _execute_tool_call
from app.agent.tools import AgentContext, CreateTaskArgs, ProposeMeetingArgs
from app.services.meet_command_parser import MeetCommandError, parse_meet_command
from app.services.task_command_parser import TaskCommandError, parse_task_add

DUE = "2026-09-10T17:00+00:00"


def _task(title="Ship it", due=DUE, repeat=None) -> str:
    text = f"<@U1> {title} | {due}"
    return text + (f" | repeat:{repeat}" if repeat is not None else "")


class TestTaskParserLimits:
    def test_title_of_200_chars_is_accepted(self):
        assert parse_task_add(_task(title="a" * 200))[1] == "a" * 200

    def test_title_of_201_chars_is_rejected(self):
        with pytest.raises(TaskCommandError, match="200"):
            parse_task_add(_task(title="a" * 201))

    def test_nul_in_title_is_rejected(self):
        with pytest.raises(TaskCommandError):
            parse_task_add(_task(title="bad\x00title"))

    @pytest.mark.parametrize(
        "due", ["1999-12-31T23:59+00:00", "2100-01-01T00:01+00:00", "0001-01-01T00:00+05:00"]
    )
    def test_due_outside_2000_to_2100_is_rejected(self, due):
        with pytest.raises(TaskCommandError, match="2000"):
            parse_task_add(_task(due=due))

    @pytest.mark.parametrize("due", ["2000-01-01T00:00+00:00", "2001-05-05T00:00+00:00"])
    def test_past_dates_inside_range_stay_allowed(self, due):
        assert parse_task_add(_task(due=due))[2].year in (2000, 2001)

    @pytest.mark.parametrize("repeat", [0, 366, 99999])
    def test_repeat_out_of_range_is_rejected(self, repeat):
        with pytest.raises(TaskCommandError):
            parse_task_add(_task(repeat=repeat))

    def test_repeat_with_thousands_of_digits_is_a_clean_error(self):
        with pytest.raises(TaskCommandError):
            parse_task_add(_task(repeat="9" * 5000))

    @pytest.mark.parametrize("repeat", [1, 365])
    def test_repeat_bounds_are_accepted(self, repeat):
        assert parse_task_add(_task(repeat=repeat))[3] == repeat


START = "2026-09-10T09:00+00:00"
END = "2026-09-10T17:00+00:00"


def _meet(head="<@U2> 30", start=START, end=END) -> str:
    return f"{head} | {start} | {end}"


class TestMeetParserLimits:
    @pytest.mark.parametrize("minutes", [0, 4, 481, 100000])
    def test_duration_out_of_range_is_rejected(self, minutes):
        with pytest.raises(MeetCommandError, match="5"):
            parse_meet_command(_meet(head=f"<@U2> {minutes}"))

    @pytest.mark.parametrize("minutes", [5, 480])
    def test_duration_bounds_are_accepted(self, minutes):
        assert parse_meet_command(_meet(head=f"<@U2> {minutes}"))[1] == minutes

    def test_window_over_31_days_is_rejected(self):
        with pytest.raises(MeetCommandError, match="31"):
            parse_meet_command(_meet(end="2026-10-11T09:01+00:00"))

    def test_window_of_exactly_31_days_is_accepted(self):
        # 2026-09-10T09:00 + 31 days == 2026-10-11T09:00
        parse_meet_command(_meet(end="2026-10-11T09:00+00:00"))

    def test_window_end_not_after_start_is_rejected(self):
        with pytest.raises(MeetCommandError):
            parse_meet_command(_meet(end=START))

    def test_year_one_offset_timestamp_is_a_clean_error(self):
        with pytest.raises(MeetCommandError):
            parse_meet_command(_meet(start="0001-01-01T00:00+05:00"))

    def test_duplicate_participants_are_collapsed(self):
        participants = parse_meet_command(_meet(head="<@U2> <@U2> <@U3> 30"))[0]
        assert participants == ["U2", "U3"]

    def test_eight_mentions_of_one_user_count_once(self):
        assert parse_meet_command(_meet(head="<@U2> " * 8 + "30"))[0] == ["U2"]

    def test_more_than_seven_distinct_participants_still_rejected(self):
        head = " ".join(f"<@U{i}>" for i in range(8)) + " 30"
        with pytest.raises(MeetCommandError, match="2-8"):
            parse_meet_command(_meet(head=head))


class TestAgentArgs:
    def test_create_task_title_length_and_nul(self):
        for bad in ["", "   ", "a" * 201, "x\x00y"]:
            with pytest.raises(ValidationError):
                CreateTaskArgs(assignee_slack_id="U1", title=bad, due_at_utc=DUE)

    def test_create_task_title_is_stripped(self):
        args = CreateTaskArgs(assignee_slack_id="U1", title="  hi  ", due_at_utc=DUE)
        assert args.title == "hi"

    @pytest.mark.parametrize(
        "due", ["1999-12-31T23:59:00+00:00", "2100-01-01T00:01:00+00:00", "2026-09-10T17:00:00"]
    )
    def test_create_task_due_range_and_timezone(self, due):
        with pytest.raises(ValidationError):
            CreateTaskArgs(assignee_slack_id="U1", title="t", due_at_utc=due)

    def test_create_task_has_no_identity_fields(self):
        assert set(CreateTaskArgs.model_fields) == {"assignee_slack_id", "title", "due_at_utc"}

    def _meeting(self, **overrides):
        base = {
            "participant_slack_ids": ["U2"],
            "duration_minutes": 30,
            "search_window_start_utc": START,
            "search_window_end_utc": END,
        }
        base.update(overrides)
        return ProposeMeetingArgs(**base)

    @pytest.mark.parametrize(
        "overrides",
        [
            {"duration_minutes": 4},
            {"duration_minutes": 481},
            {"search_window_end_utc": START},
            {"search_window_end_utc": "2026-10-12T09:00:00+00:00"},
            {"participant_slack_ids": []},
            {"participant_slack_ids": [f"U{i}" for i in range(8)]},
            {"search_window_start_utc": "2026-09-10T09:00:00"},
        ],
    )
    def test_propose_meeting_rejects_out_of_range(self, overrides):
        with pytest.raises(ValidationError):
            self._meeting(**overrides)

    def test_propose_meeting_collapses_duplicate_participants(self):
        args = self._meeting(participant_slack_ids=["U2", "U2", "U3"])
        assert args.participant_slack_ids == ["U2", "U3"]

    def test_propose_meeting_has_no_identity_fields(self):
        assert "team_id" not in ProposeMeetingArgs.model_fields
        assert "slack_user_id" not in ProposeMeetingArgs.model_fields


class TestAgentLoopSurvivesBadArgs:
    @pytest.mark.parametrize(
        ("name", "args"),
        [
            (
                "create_task",
                {"assignee_slack_id": "U1", "title": "x" * 500, "due_at_utc": DUE},
            ),
            (
                "create_task",
                {"assignee_slack_id": "U1", "title": "ok", "due_at_utc": "1850-01-01T00:00:00Z"},
            ),
            (
                "propose_meeting",
                {
                    "participant_slack_ids": ["U2"],
                    "duration_minutes": 100000,
                    "search_window_start_utc": START,
                    "search_window_end_utc": END,
                },
            ),
        ],
    )
    async def test_out_of_range_tool_args_return_error_text(self, db_session, name, args):
        ctx = AgentContext(session=db_session, team_id="T1", slack_user_id="U1")
        call = {"id": "c1", "function": {"name": name, "arguments": json.dumps(args)}}
        result = await _execute_tool_call(ctx, call)
        assert result.startswith(f"Invalid arguments for {name}")
