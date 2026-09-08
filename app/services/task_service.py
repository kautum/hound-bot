"""Task CRUD plus the reminder schedule that goes with every task.

Escalation ladder (see Part 0.5's decided chasing behaviour): a reminder DM to
the assignee 24h before the deadline, another on the day, then when overdue a
DM to both the assignee and whoever created the task — the creator notification
is why `Task.creator_slack_id` exists as its own column, not derived from
`assignee_slack_id`.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import STATUS_DONE, STATUS_OPEN, Task
from app.repositories.reminder_repository import ReminderRepository
from app.repositories.task_repository import TaskRepository

REMINDER_BEFORE_DUE = timedelta(hours=24)
ESCALATION_LEVEL_UPCOMING = 0
ESCALATION_LEVEL_DUE_TODAY = 1
ESCALATION_LEVEL_OVERDUE = 2


async def create_task(
    session: AsyncSession,
    *,
    team_id: str,
    creator_slack_id: str,
    assignee_slack_id: str,
    title: str,
    due_at_utc,
    channel_id: str,
) -> Task:
    task = await TaskRepository(session, team_id).add(
        creator_slack_id=creator_slack_id,
        assignee_slack_id=assignee_slack_id,
        title=title,
        due_at_utc=due_at_utc,
        channel_id=channel_id,
    )

    reminders = ReminderRepository(session)
    upcoming_fire_at = due_at_utc - REMINDER_BEFORE_DUE
    # If the deadline is less than 24h out, there's no meaningful "24h before"
    # moment left — skip that rung rather than scheduling a reminder in the past.
    if upcoming_fire_at > datetime.now(UTC):
        await reminders.create(task.id, team_id, upcoming_fire_at, ESCALATION_LEVEL_UPCOMING)
    await reminders.create(task.id, team_id, due_at_utc, ESCALATION_LEVEL_DUE_TODAY)
    await reminders.create(
        task.id, team_id, due_at_utc + REMINDER_BEFORE_DUE, ESCALATION_LEVEL_OVERDUE
    )

    return task


async def list_open_tasks(
    session: AsyncSession, *, team_id: str, assignee_slack_id: str | None = None
) -> list[Task]:
    filters = {"status": STATUS_OPEN}
    if assignee_slack_id is not None:
        filters["assignee_slack_id"] = assignee_slack_id
    return await TaskRepository(session, team_id).list(**filters)


class TaskAuthorizationError(Exception):
    """Raised when the requester is neither the task's assignee nor its
    creator — mirrors meeting_service.MeetingConfirmationError's shape, so
    there's one authorization pattern in the codebase, not two."""


async def mark_task_done(
    session: AsyncSession, *, team_id: str, task_id: uuid.UUID, requesting_slack_user_id: str
) -> Task | None:
    task = await TaskRepository(session, team_id).get(id=task_id)
    if task is None:
        return None
    if requesting_slack_user_id not in (task.assignee_slack_id, task.creator_slack_id):
        raise TaskAuthorizationError("Only the assignee or the creator can mark this task done.")
    task.status = STATUS_DONE
    await session.flush()
    # A finished task must stop nagging its assignee — see S3. Cancelling
    # here and having the scheduler independently skip non-open tasks are
    # both needed: this closes the gap immediately; the scheduler check is
    # the last line before a DM actually goes out, for anything already
    # claimed by a worker mid-flight.
    await ReminderRepository(session).cancel_pending_for_task(task_id)
    await session.flush()
    return task
