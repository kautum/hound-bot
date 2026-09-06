from datetime import UTC

import pytest

from app.services.task_command_parser import TaskCommandError, parse_task_add


class TestParseTaskAdd:
    def test_parses_a_well_formed_command(self):
        assignee, title, due_at = parse_task_add(
            "<@U12345|bob> Write the report | 2026-09-10T17:00+00:00"
        )
        assert assignee == "U12345"
        assert title == "Write the report"
        assert due_at.tzinfo is UTC
        assert due_at.isoformat() == "2026-09-10T17:00:00+00:00"

    def test_converts_non_utc_offset_to_utc(self):
        _, _, due_at = parse_task_add("<@U1> Title | 2026-09-10T17:00+05:30")
        assert due_at.isoformat() == "2026-09-10T11:30:00+00:00"

    def test_rejects_missing_mention(self):
        with pytest.raises(TaskCommandError, match="Usage"):
            parse_task_add("Write the report | 2026-09-10T17:00+00:00")

    def test_rejects_missing_pipe(self):
        with pytest.raises(TaskCommandError, match="due date"):
            parse_task_add("<@U1> Write the report by Friday")

    def test_rejects_empty_title(self):
        with pytest.raises(TaskCommandError, match="empty"):
            parse_task_add("<@U1>  | 2026-09-10T17:00+00:00")

    def test_rejects_unparseable_date(self):
        with pytest.raises(TaskCommandError, match="ISO 8601"):
            parse_task_add("<@U1> Title | next friday")

    def test_rejects_timezone_naive_date_rather_than_guessing(self):
        """The exact class of bug ENGINEERING.md's quality floor calls out:
        never silently assume a timezone for ambiguous input."""
        with pytest.raises(TaskCommandError, match="no timezone"):
            parse_task_add("<@U1> Title | 2026-09-10T17:00")
