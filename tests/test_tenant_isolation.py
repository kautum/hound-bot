from datetime import UTC, datetime

import pytest

from app.models import Workspace
from app.repositories.task_repository import TaskRepository


async def _make_workspace(session, team_id: str) -> None:
    session.add(
        Workspace(
            team_id=team_id,
            bot_token_enc=b"irrelevant-for-this-test",
            key_version=1,
            installed_at=datetime.now(UTC),
        )
    )
    await session.flush()


class TestTenantIsolation:
    async def test_repository_never_returns_another_tenants_rows(self, db_session):
        """The most serious bug this product could ship: one workspace seeing
        another's data. See ARCHITECTURE.md's security boundary section."""
        await _make_workspace(db_session, "team-A")
        await _make_workspace(db_session, "team-B")

        repo_a = TaskRepository(db_session, team_id="team-A")
        repo_b = TaskRepository(db_session, team_id="team-B")

        await repo_a.add(
            creator_slack_id="U1",
            assignee_slack_id="U2",
            title="Team A's confidential task",
            due_at_utc=datetime.now(UTC),
            channel_id="C1",
        )
        await db_session.commit()

        team_a_tasks = await repo_a.list()
        assert len(team_a_tasks) == 1

        # Team B's repository must not see team A's task, by direct lookup or listing.
        assert await repo_b.get(id=team_a_tasks[0].id) is None
        assert await repo_b.list() == []

    async def test_repository_refuses_to_construct_without_a_team_id(self, db_session):
        with pytest.raises(ValueError):
            TaskRepository(db_session, team_id="")
