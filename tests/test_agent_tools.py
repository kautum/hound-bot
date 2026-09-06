from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.agent.tools import TOOLS, CreateTaskArgs
from app.models import Workspace


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
    await session.commit()


class TestCreateTaskArgs:
    def test_parses_valid_args(self):
        args = CreateTaskArgs(
            assignee_slack_id="U1", title="Write report", due_at_utc="2026-09-10T17:00:00+00:00"
        )
        assert args.title == "Write report"

    def test_rejects_missing_required_field(self):
        with pytest.raises(ValidationError):
            CreateTaskArgs(assignee_slack_id="U1", due_at_utc="2026-09-10T17:00:00+00:00")


class TestToolRegistry:
    def test_only_two_tools_are_whitelisted(self):
        """The whitelist itself IS the injection defence — see
        app/agent/tools.py's module docstring."""
        assert set(TOOLS.keys()) == {"create_task", "list_tasks"}

    async def test_create_task_handler_writes_a_real_task(self, db_session):
        await _make_workspace(db_session, "team-A")
        args = CreateTaskArgs(
            assignee_slack_id="U1", title="From the agent", due_at_utc="2026-09-10T17:00:00+00:00"
        )
        result = await TOOLS["create_task"].handler(db_session, "team-A", "U_CREATOR", args)
        assert "From the agent" in result

    async def test_list_tasks_handler_reflects_created_tasks(self, db_session):
        await _make_workspace(db_session, "team-A")
        args = CreateTaskArgs(
            assignee_slack_id="U1", title="Visible task", due_at_utc="2026-09-10T17:00:00+00:00"
        )
        await TOOLS["create_task"].handler(db_session, "team-A", "U1", args)

        from app.agent.tools import ListTasksArgs

        result = await TOOLS["list_tasks"].handler(db_session, "team-A", "U1", ListTasksArgs())
        assert "Visible task" in result
