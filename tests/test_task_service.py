from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import Reminder, Task, Workspace
from app.models.task import STATUS_OPEN
from app.services.task_service import (
    ESCALATION_LEVEL_DUE_TODAY,
    ESCALATION_LEVEL_OVERDUE,
    ESCALATION_LEVEL_UPCOMING,
    TaskAuthorizationError,
    create_task,
    list_open_tasks,
    mark_task_done,
    reassign_task,
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

        await mark_task_done(
            db_session,
            team_id="team-A",
            task_id=mine.id,
            requesting_slack_user_id="U_ME",
        )
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

        result = await mark_task_done(
            db_session,
            team_id="team-B",
            task_id=task.id,
            requesting_slack_user_id="U_Y",
        )
        assert result is None

    async def test_mark_task_done_rejects_a_third_party(self, db_session):
        """S4: any workspace member could previously close anyone's task by
        ID. Only the assignee or the creator may mark it done."""
        await _make_workspace(db_session, "team-A")
        due = datetime.now(UTC) + timedelta(days=1)

        task = await create_task(
            db_session,
            team_id="team-A",
            creator_slack_id="U_CREATOR",
            assignee_slack_id="U_ASSIGNEE",
            title="Someone else's task",
            due_at_utc=due,
            channel_id="C1",
        )
        await db_session.commit()

        with pytest.raises(TaskAuthorizationError):
            await mark_task_done(
                db_session,
                team_id="team-A",
                task_id=task.id,
                requesting_slack_user_id="U_RANDOM",
            )

        result = await db_session.execute(select(Reminder).filter_by(task_id=task.id))
        assert result.scalars().first().task_id == task.id  # untouched: task stays open
        await db_session.refresh(task)
        assert task.status == STATUS_OPEN

    async def test_mark_task_done_allows_the_creator_not_just_the_assignee(self, db_session):
        await _make_workspace(db_session, "team-A")
        due = datetime.now(UTC) + timedelta(days=1)

        task = await create_task(
            db_session,
            team_id="team-A",
            creator_slack_id="U_CREATOR",
            assignee_slack_id="U_ASSIGNEE",
            title="Assigned to someone else",
            due_at_utc=due,
            channel_id="C1",
        )
        await db_session.commit()

        result = await mark_task_done(
            db_session,
            team_id="team-A",
            task_id=task.id,
            requesting_slack_user_id="U_CREATOR",
        )
        assert result is not None
        assert result.status == "done"

    async def test_mark_task_done_cancels_pending_reminders(self, db_session):
        """S3: a completed task must stop nagging its assignee."""
        await _make_workspace(db_session, "team-A")
        due = datetime.now(UTC) + timedelta(hours=2)

        task = await create_task(
            db_session,
            team_id="team-A",
            creator_slack_id="U_CREATOR",
            assignee_slack_id="U_ASSIGNEE",
            title="Finish before it fires",
            due_at_utc=due,
            channel_id="C1",
        )
        await db_session.commit()

        pending_before = await db_session.execute(select(Reminder).filter_by(task_id=task.id))
        assert len(pending_before.scalars().all()) > 0

        await mark_task_done(
            db_session,
            team_id="team-A",
            task_id=task.id,
            requesting_slack_user_id="U_ASSIGNEE",
        )
        await db_session.commit()

        remaining = await db_session.execute(
            select(Reminder).filter_by(task_id=task.id, sent_at=None)
        )
        assert remaining.scalars().all() == []


class TestReassignTask:
    async def test_reassign_task_is_tenant_scoped(self, db_session):
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

        result = await reassign_task(
            db_session,
            team_id="team-B",
            task_id=task.id,
            requesting_slack_user_id="U_Y",
            new_assignee_slack_id="U_Z",
        )
        assert result is None

    async def test_reassign_task_allows_current_assignee(self, db_session):
        await _make_workspace(db_session, "team-A")
        due = datetime.now(UTC) + timedelta(days=1)

        task = await create_task(
            db_session,
            team_id="team-A",
            creator_slack_id="U_CREATOR",
            assignee_slack_id="U_ASSIGNEE",
            title="Assigned to me",
            due_at_utc=due,
            channel_id="C1",
        )
        await db_session.commit()

        result = await reassign_task(
            db_session,
            team_id="team-A",
            task_id=task.id,
            requesting_slack_user_id="U_ASSIGNEE",
            new_assignee_slack_id="U_NEW",
        )
        assert result is not None
        assert result.assignee_slack_id == "U_NEW"

    async def test_reassign_task_allows_creator(self, db_session):
        await _make_workspace(db_session, "team-A")
        due = datetime.now(UTC) + timedelta(days=1)

        task = await create_task(
            db_session,
            team_id="team-A",
            creator_slack_id="U_CREATOR",
            assignee_slack_id="U_ASSIGNEE",
            title="I created this",
            due_at_utc=due,
            channel_id="C1",
        )
        await db_session.commit()

        result = await reassign_task(
            db_session,
            team_id="team-A",
            task_id=task.id,
            requesting_slack_user_id="U_CREATOR",
            new_assignee_slack_id="U_NEW",
        )
        assert result is not None
        assert result.assignee_slack_id == "U_NEW"

    async def test_reassign_task_rejects_third_party(self, db_session):
        await _make_workspace(db_session, "team-A")
        due = datetime.now(UTC) + timedelta(days=1)

        task = await create_task(
            db_session,
            team_id="team-A",
            creator_slack_id="U_CREATOR",
            assignee_slack_id="U_ASSIGNEE",
            title="Not my task",
            due_at_utc=due,
            channel_id="C1",
        )
        await db_session.commit()

        with pytest.raises(TaskAuthorizationError):
            await reassign_task(
                db_session,
                team_id="team-A",
                task_id=task.id,
                requesting_slack_user_id="U_RANDOM",
                new_assignee_slack_id="U_NEW",
            )

        await db_session.refresh(task)
        assert task.assignee_slack_id == "U_ASSIGNEE"  # unchanged


class TestRecurringTasks:
    async def test_create_task_with_recurrence(self, db_session):
        await _make_workspace(db_session, "team-A")
        due = datetime.now(UTC) + timedelta(days=1)

        task = await create_task(
            db_session,
            team_id="team-A",
            creator_slack_id="U_CREATOR",
            assignee_slack_id="U_ASSIGNEE",
            title="Weekly report",
            due_at_utc=due,
            channel_id="C1",
            recurrence_interval_days=7,
        )
        await db_session.commit()

        assert task.recurrence_interval_days == 7

    async def test_mark_recurring_task_done_spawns_next_occurrence(self, db_session):
        await _make_workspace(db_session, "team-A")
        due = datetime.now(UTC) + timedelta(days=1)

        task = await create_task(
            db_session,
            team_id="team-A",
            creator_slack_id="U_CREATOR",
            assignee_slack_id="U_ASSIGNEE",
            title="Weekly report",
            due_at_utc=due,
            channel_id="C1",
            recurrence_interval_days=7,
        )
        await db_session.commit()

        # Count tasks before marking done
        result_before = await db_session.execute(select(Task).filter_by(team_id="team-A"))
        count_before = len(result_before.scalars().all())
        assert count_before == 1

        # Mark the task done
        await mark_task_done(
            db_session,
            team_id="team-A",
            task_id=task.id,
            requesting_slack_user_id="U_ASSIGNEE",
        )
        await db_session.commit()

        # Count tasks after marking done - should be 2 (original + new occurrence)
        result_after = await db_session.execute(select(Task).filter_by(team_id="team-A"))
        tasks_after = result_after.scalars().all()
        assert len(tasks_after) == 2

        # Find the new task
        new_task = [t for t in tasks_after if t.id != task.id][0]
        assert new_task.title == "Weekly report"
        assert new_task.assignee_slack_id == "U_ASSIGNEE"
        assert new_task.creator_slack_id == "U_CREATOR"
        assert new_task.recurrence_interval_days == 7
        assert new_task.due_at_utc == due + timedelta(days=7)
        assert new_task.status == STATUS_OPEN

    async def test_mark_recurring_task_done_creates_third_occurrence(self, db_session):
        await _make_workspace(db_session, "team-A")
        due = datetime.now(UTC) + timedelta(days=1)

        task = await create_task(
            db_session,
            team_id="team-A",
            creator_slack_id="U_CREATOR",
            assignee_slack_id="U_ASSIGNEE",
            title="Weekly report",
            due_at_utc=due,
            channel_id="C1",
            recurrence_interval_days=7,
        )
        await db_session.commit()

        # Mark first task done - creates second
        await mark_task_done(
            db_session,
            team_id="team-A",
            task_id=task.id,
            requesting_slack_user_id="U_ASSIGNEE",
        )
        await db_session.commit()

        result = await db_session.execute(
            select(Task).filter_by(team_id="team-A", status=STATUS_OPEN)
        )
        second_task = result.scalar_one()

        # Mark second task done - creates third
        await mark_task_done(
            db_session,
            team_id="team-A",
            task_id=second_task.id,
            requesting_slack_user_id="U_ASSIGNEE",
        )
        await db_session.commit()

        # Should now have 3 tasks total (2 done, 1 open)
        result_final = await db_session.execute(select(Task).filter_by(team_id="team-A"))
        all_tasks = result_final.scalars().all()
        assert len(all_tasks) == 3

        # The third task should be open and due 14 days after original
        open_tasks = [t for t in all_tasks if t.status == STATUS_OPEN]
        assert len(open_tasks) == 1
        assert open_tasks[0].due_at_utc == due + timedelta(days=14)
        assert open_tasks[0].recurrence_interval_days == 7

    async def test_mark_non_recurring_task_done_does_not_spawn(self, db_session):
        await _make_workspace(db_session, "team-A")
        due = datetime.now(UTC) + timedelta(days=1)

        task = await create_task(
            db_session,
            team_id="team-A",
            creator_slack_id="U_CREATOR",
            assignee_slack_id="U_ASSIGNEE",
            title="One-time task",
            due_at_utc=due,
            channel_id="C1",
            recurrence_interval_days=None,
        )
        await db_session.commit()

        # Count tasks before marking done
        result_before = await db_session.execute(select(Task).filter_by(team_id="team-A"))
        count_before = len(result_before.scalars().all())
        assert count_before == 1

        # Mark the task done
        await mark_task_done(
            db_session,
            team_id="team-A",
            task_id=task.id,
            requesting_slack_user_id="U_ASSIGNEE",
        )
        await db_session.commit()

        # Count tasks after marking done - should still be 1
        result_after = await db_session.execute(select(Task).filter_by(team_id="team-A"))
        count_after = len(result_after.scalars().all())
        assert count_after == 1
