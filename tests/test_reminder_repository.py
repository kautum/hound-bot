from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Workspace
from app.repositories.reminder_repository import ReminderRepository
from app.services.task_service import create_task


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


class TestReminderRepositoryClaiming:
    async def test_claim_due_batch_marks_claimed_at(self, db_session):
        await _make_workspace(db_session, "team-A")
        task = await create_task(
            db_session,
            team_id="team-A",
            creator_slack_id="U_X",
            assignee_slack_id="U_Y",
            title="Due now",
            due_at_utc=datetime.now(UTC) - timedelta(minutes=1),
            channel_id="C1",
        )
        await db_session.commit()

        claimed = await ReminderRepository(db_session).claim_due_batch()
        assert len(claimed) >= 1
        assert all(r.claimed_at is not None for r in claimed if r.task_id == task.id)

    async def test_claimed_reminder_is_not_reclaimed_after_a_commit_mid_batch(
        self, db_session, db_engine
    ):
        """S8, same shape as the inbound_jobs test: a per-reminder commit
        inside the worker's drain loop must not let a second worker
        re-claim (and re-send) a reminder that's already been claimed."""
        await _make_workspace(db_session, "team-A")
        await create_task(
            db_session,
            team_id="team-A",
            creator_slack_id="U_X",
            assignee_slack_id="U_Y",
            title="Due now",
            due_at_utc=datetime.now(UTC) - timedelta(minutes=1),
            channel_id="C1",
        )
        await db_session.commit()

        session_factory = async_sessionmaker(db_engine, expire_on_commit=False)

        async with session_factory() as worker_a_session:
            claimed_by_a = await ReminderRepository(worker_a_session).claim_due_batch()
            await worker_a_session.commit()

        assert len(claimed_by_a) >= 1

        async with session_factory() as worker_b_session:
            claimed_by_b = await ReminderRepository(worker_b_session).claim_due_batch()

        assert claimed_by_b == []
