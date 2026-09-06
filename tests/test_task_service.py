from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import Reminder, Workspace
from app.services.task_service import (
    ESCALATION_LEVEL_DUE_TODAY,
    ESCALATION_LEVEL_OVERDUE,
    ESCALATION_LEVEL_UPCOMING,
    create_task,
    list_open_tasks,
    mark_task_done,
)


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


class TestCreateTask:
    async def test_schedules_all_three_reminders_when_due_date_is_far_out(self, db_session):
        await _make_workspace(db_session, "team-A")
        due = datetime.now(UTC) + timedelta(days=7)

        task = await create_task(
            db_session,
            team_id="team-A",
            creator_slack_id="U_CREATOR",
            assignee_slack_id="U_ASSIGNEE",
            title="Write the report",
            due_at_utc=due,
            channel_id="C1",
        )
        await db_session.commit()

        result = await db_session.execute(select(Reminder).filter_by(task_id=task.id))
        levels = sorted(r.escalation_level for r in result.scalars().all())
        assert levels == [
            ESCALATION_LEVEL_UPCOMING,
            ESCALATION_LEVEL_DUE_TODAY,
            ESCALATION_LEVEL_OVERDUE,
        ]

    async def test_skips_the_upcoming_reminder_when_due_within_24h(self, db_session):
        """A task due in 2 hours has no meaningful '24h before' moment —
        that reminder must not be scheduled in the past."""
        await _make_workspace(db_session, "team-A")
        due = datetime.now(UTC) + timedelta(hours=2)

        task = await create_task(
            db_session,
            team_id="team-A",
            creator_slack_id="U_CREATOR",
            assignee_slack_id="U_ASSIGNEE",
            title="Urgent thing",
            due_at_utc=due,
            channel_id="C1",
        )
        await db_session.commit()

        result = await db_session.execute(select(Reminder).filter_by(task_id=task.id))
        levels = sorted(r.escalation_level for r in result.scalars().all())
        assert levels == [ESCALATION_LEVEL_DUE_TODAY, ESCALATION_LEVEL_OVERDUE]


class TestListAndMarkDone:
    async def test_list_open_tasks_filters_by_assignee_and_excludes_done(self, db_session):
        await _make_workspace(db_session, "team-A")
        due = datetime.now(UTC) + timedelta(days=1)

        mine = await create_task(
            db_session,
            team_id="team-A",
            creator_slack_id="U_X",
            assignee_slack_id="U_ME",
            title="Mine",
            due_at_utc=due,
            channel_id="C1",
        )
        await create_task(
            db_session,
            team_id="team-A",
            creator_slack_id="U_X",
            assignee_slack_id="U_OTHER",
            title="Not mine",
            due_at_utc=due,
            channel_id="C1",
        )
        await db_session.commit()

        await mark_task_done(db_session, team_id="team-A", task_id=mine.id)
        await db_session.commit()

        mine_tasks = await list_open_tasks(db_session, team_id="team-A", assignee_slack_id="U_ME")
        assert mine_tasks == []  # done, so excluded

        other_tasks = await list_open_tasks(
            db_session, team_id="team-A", assignee_slack_id="U_OTHER"
        )
        assert len(other_tasks) == 1

    async def test_mark_task_done_is_tenant_scoped(self, db_session):
        await _make_workspace(db_session, "team-A")
        await _make_workspace(db_session, "team-B")
        due = datetime.now(UTC) + timedelta(days=1)

        task = await create_task(
            db_session,
            team_id="team-A",
            creator_slack_id="U_X",
            assignee_slack_id="U_Y",
            title="Team A's task",
            due_at_utc=due,
            channel_id="C1",
        )
        await db_session.commit()

        result = await mark_task_done(db_session, team_id="team-B", task_id=task.id)
        assert result is None
