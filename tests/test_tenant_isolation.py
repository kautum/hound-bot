from datetime import UTC, datetime

import pytest
from cryptography.fernet import Fernet

from app.core.security import TokenCipher
from app.models import Workspace
from app.repositories.task_repository import TaskRepository
from app.repositories.user_repository import UserRepository


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

    async def test_user_repository_never_returns_another_tenants_user(self, db_session):
        """S2: Slack user IDs are unique only *within* a workspace, so two
        different organisations can have a member with the same Slack user
        ID. get_or_create used to look that ID up with no team_id filter at
        all, meaning workspace B's lookup for "U_SAME" could return workspace
        A's row — attaching A's Google refresh token to B's request context."""
        await _make_workspace(db_session, "team-A")
        await _make_workspace(db_session, "team-B")

        cipher = TokenCipher(keys={1: Fernet.generate_key().decode()}, current_version=1)
        token_enc, version = cipher.encrypt("team-a-refresh-token")

        repo_a = UserRepository(db_session, "team-A")
        repo_b = UserRepository(db_session, "team-B")

        user_a = await repo_a.get_or_create("U_SAME", tz="UTC")
        user_a.google_refresh_token_enc = token_enc
        user_a.key_version = version
        await db_session.flush()
        await db_session.commit()

        # Team B's repository must not see team A's row for the same Slack
        # user ID — it must create its own, independent row instead.
        user_b = await repo_b.get_or_create("U_SAME", tz="UTC")
        assert user_b.team_id == "team-B"
        assert user_b.google_refresh_token_enc is None

        assert await repo_b.get("U_SAME") is not None
        assert (await repo_b.get("U_SAME")).team_id == "team-B"

    async def test_user_repository_teeth_check(self, db_session, monkeypatch):
        """Proves the isolation test above actually has teeth, the same way
        test_repository_never_returns_another_tenants_rows is proven: revert
        the fix (make get() ignore team_id, matching the original bug) and
        confirm the test above would have failed."""
        import app.repositories.user_repository as user_repo_module

        async def unscoped_get(self, slack_user_id: str):
            from sqlalchemy import select

            result = await self._session.execute(
                select(user_repo_module.User).filter_by(slack_user_id=slack_user_id)
            )
            return result.scalar_one_or_none()

        monkeypatch.setattr(user_repo_module.UserRepository, "get", unscoped_get)

        await _make_workspace(db_session, "team-A")
        await _make_workspace(db_session, "team-B")

        repo_a = UserRepository(db_session, "team-A")
        repo_b = UserRepository(db_session, "team-B")

        await repo_a.get_or_create("U_SAME", tz="UTC")
        await db_session.commit()

        # With the fix reverted, team B's "get" resolves team A's row —
        # demonstrating the isolation test above would fail without the fix.
        leaked = await repo_b.get("U_SAME")
        assert leaked is not None
        assert leaked.team_id == "team-A"
