"""Drains due reminders — triggered by the `/internal/tick` endpoint in prod
(cron-job.org, every 5 min) or a local polling loop in dev. See
ARCHITECTURE.md's job queue section and Part 0.5's decided chasing behaviour:
DM the assignee on the way to the deadline; when overdue, also DM the creator.
"""

from app.core.security import TokenCipher
from app.core.slack_client import build_client_for_workspace
from app.models.task import Task
from app.repositories.reminder_repository import ReminderRepository
from app.repositories.workspace_repository import WorkspaceRepository
from app.services.task_service import ESCALATION_LEVEL_OVERDUE


def _reminder_text(escalation_level: int, task: Task) -> str:
    if escalation_level == ESCALATION_LEVEL_OVERDUE:
        return f'Overdue: "{task.title}"'
    return f'Reminder: "{task.title}" is due soon.'


async def process_due_reminders(session, cipher: TokenCipher) -> int:
    repo = ReminderRepository(session)
    due = await repo.claim_due_batch()

    for reminder in due:
        task = await session.get(Task, reminder.task_id)
        if task is None:
            await repo.mark_sent(reminder.id)
            await session.commit()
            continue

        workspace = await WorkspaceRepository(session).get(reminder.team_id)
        if workspace is None or workspace.uninstalled_at is not None:
            await repo.mark_sent(reminder.id)
            await session.commit()
            continue

        client = build_client_for_workspace(workspace, cipher)
        text = _reminder_text(reminder.escalation_level, task)

        recipients = {task.assignee_slack_id}
        if reminder.escalation_level == ESCALATION_LEVEL_OVERDUE:
            recipients.add(task.creator_slack_id)

        for slack_user_id in recipients:
            dm = await client.conversations_open(users=slack_user_id)
            await client.chat_postMessage(channel=dm["channel"]["id"], text=text)

        await repo.mark_sent(reminder.id)
        await session.commit()

    return len(due)
