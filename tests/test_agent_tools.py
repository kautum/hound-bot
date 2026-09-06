from datetime import UTC, datetime

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.agent.tools import TOOLS, AgentContext, CreateTaskArgs, ProposeMeetingArgs
from app.calendar.provider import BusyBlock
from app.core.security import TokenCipher
from app.models import User, Workspace


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


class FakeProvider:
    def __init__(self, busy_by_token: dict[str, list[BusyBlock]] | None = None):
        self._busy = busy_by_token or {}

    async def get_busy_blocks(self, refresh_token, window_start_utc, window_end_utc):
        return self._busy.get(refresh_token, [])

    async def create_event(self, *args, **kwargs):
        raise NotImplementedError


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
    def test_exactly_three_tools_are_whitelisted(self):
        """The whitelist itself IS the injection defence — see
        app/agent/tools.py's module docstring."""
        assert set(TOOLS.keys()) == {"create_task", "list_tasks", "propose_meeting"}

    async def test_create_task_handler_writes_a_real_task(self, db_session):
        await _make_workspace(db_session, "team-A")
        ctx = AgentContext(session=db_session, team_id="team-A", slack_user_id="U_CREATOR")
        args = CreateTaskArgs(
            assignee_slack_id="U1", title="From the agent", due_at_utc="2026-09-10T17:00:00+00:00"
        )
        result = await TOOLS["create_task"].handler(ctx, args)
        assert "From the agent" in result

    async def test_list_tasks_handler_reflects_created_tasks(self, db_session):
        await _make_workspace(db_session, "team-A")
        ctx = AgentContext(session=db_session, team_id="team-A", slack_user_id="U1")
        args = CreateTaskArgs(
            assignee_slack_id="U1", title="Visible task", due_at_utc="2026-09-10T17:00:00+00:00"
        )
        await TOOLS["create_task"].handler(ctx, args)

        from app.agent.tools import ListTasksArgs

        result = await TOOLS["list_tasks"].handler(ctx, ListTasksArgs())
        assert "Visible task" in result


class TestProposeMeetingTool:
    async def test_degrades_gracefully_when_calendar_not_configured(self, db_session):
        await _make_workspace(db_session, "team-A")
        ctx = AgentContext(
            session=db_session, team_id="team-A", slack_user_id="U1"
        )  # no provider/cipher
        args = ProposeMeetingArgs(
            participant_slack_ids=["U2"],
            duration_minutes=30,
            search_window_start_utc=datetime(2026, 9, 7, 9, 0, tzinfo=UTC),
            search_window_end_utc=datetime(2026, 9, 7, 17, 0, tzinfo=UTC),
        )
        result = await TOOLS["propose_meeting"].handler(ctx, args)
        assert "isn't configured" in result

    async def test_finds_and_reports_a_slot(self, db_session):
        await _make_workspace(db_session, "team-A")
        key = Fernet.generate_key().decode()
        cipher = TokenCipher(keys={1: key}, current_version=1)
        token_enc, version = cipher.encrypt("tok-alice")
        db_session.add(
            User(
                slack_user_id="U_ALICE",
                team_id="team-A",
                tz="UTC",
                google_refresh_token_enc=token_enc,
                key_version=version,
                google_email="a@x.com",
            )
        )
        await db_session.commit()

        ctx = AgentContext(
            session=db_session,
            team_id="team-A",
            slack_user_id="U_ALICE",
            calendar_provider=FakeProvider(),
            cipher=cipher,
        )
        args = ProposeMeetingArgs(
            participant_slack_ids=["U_ALICE"],
            duration_minutes=30,
            search_window_start_utc=datetime(2026, 9, 7, 9, 0, tzinfo=UTC),
            search_window_end_utc=datetime(2026, 9, 7, 17, 0, tzinfo=UTC),
        )
        result = await TOOLS["propose_meeting"].handler(ctx, args)
        assert "Proposed 30 min" in result
        assert "2026-09-07T09:00:00+00:00" in result
