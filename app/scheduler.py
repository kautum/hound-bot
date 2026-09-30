"""Drains due reminders — triggered by the `/internal/tick` endpoint in prod
(cron-job.org, every 5 min); in dev, POST /internal/tick manually. There is no
polling loop; reminders are drained only by that endpoint. See
ARCHITECTURE.md's job queue section and Part 0.5's decided chasing behaviour:
DM the assignee on the way to the deadline; when overdue, also DM the creator.
"""

import logging
import uuid

from slack_sdk.errors import SlackApiError

from app.core.security import TokenCipher
from app.core.slack_client import build_client_for_workspace
from app.models.task import STATUS_OPEN, Task
from app.repositories.reminder_repository import ReminderRepository
from app.repositories.workspace_repository import WorkspaceRepository
from app.services.task_service import ESCALATION_LEVEL_OVERDUE
from app.ui.blocks import _build_task_block, escape_mrkdwn

logger = logging.getLogger(__name__)

# Slack errors that will fail identically on every retry: the assignee or
# channel is gone, the token is dead, or the message itself is invalid. The
# reminder is marked sent so it can never block the queue again.
PERMANENT_SLACK_ERRORS = frozenset(
    {
        "user_not_found",
        "channel_not_found",
        "is_archived",
        "account_inactive",
        "user_disabled",
        "token_revoked",
        "token_expired",
        "invalid_auth",
        "not_authed",
        "cannot_dm_bot",
        "not_in_channel",
        "missing_scope",
        "invalid_blocks",
        "msg_too_long",
    }
)


def _reminder_text(escalation_level: int, task: Task) -> str:
    title = escape_mrkdwn(task.title)
    if escalation_level == ESCALATION_LEVEL_OVERDUE:
        return f'Overdue: "{title}"'
    return f'Reminder: "{title}" is due soon.'


def _slack_error_code(exc: SlackApiError) -> str:
    """Slack's error code, or "" when the response has none (e.g. an HTTP 5xx
    with a non-JSON body). Never raises."""
    try:
        return str(exc.response.get("error", ""))
    except (AttributeError, TypeError):
        return ""


async def _deliver(
    client, reminder_id: uuid.UUID, reminder, task: Task
) -> tuple[list[str], Exception | None]:
    """Sends the DM(s) for one reminder: assignee first, then (when overdue)
    the creator, de-duplicated with order preserved.

    Returns (recipients already DMed, the failure that stopped delivery or
    None). A permanent Slack error for one recipient is logged and skipped so
    the next recipient still gets their DM; a transient failure stops the loop
    so the caller decides whether a retry is safe."""
    text = _reminder_text(reminder.escalation_level, task)

    recipients = [task.assignee_slack_id]
    if reminder.escalation_level == ESCALATION_LEVEL_OVERDUE:
        recipients.append(task.creator_slack_id)

    delivered: list[str] = []
    for slack_user_id in dict.fromkeys(recipients):
        try:
            dm = await client.conversations_open(users=slack_user_id)
            await client.chat_postMessage(
                channel=dm["channel"]["id"], text=text, blocks=_build_task_block(task)
            )
        except SlackApiError as exc:
            code = _slack_error_code(exc)
            if code not in PERMANENT_SLACK_ERRORS:
                return delivered, exc
            logger.warning(
                "reminder %s undeliverable to one recipient (%s); skipping them",
                reminder_id, code, exc_info=True,
            )  # fmt: skip
        except Exception as exc:
            return delivered, exc
        else:
            delivered.append(slack_user_id)
    return delivered, None


async def _finish(repo: ReminderRepository, session, reminder_id: uuid.UUID) -> None:
    """Marks the reminder sent (delivered, skipped, or permanently
    undeliverable) and commits it on its own."""
    await repo.mark_sent(reminder_id)
    await session.commit()


async def _retry_later(repo: ReminderRepository, session, reminder_id: uuid.UUID) -> None:
    await repo.release_claim(reminder_id)
    await session.commit()


async def process_due_reminders(session, cipher: TokenCipher) -> int:
    """Sends due reminders and returns how many were actually delivered.

    A failure on one reminder never escapes: permanent failures mark that
    reminder sent (never retried), transient ones release its claim so a later
    tick retries it. Each outcome is committed on its own, so earlier
    successes and the still-claimed rest of the batch are unaffected.
    """
    repo = ReminderRepository(session)
    due = await repo.claim_due_batch()
    sent = 0

    for reminder in due:
        reminder_id = reminder.id
        task = await session.get(Task, reminder.task_id)
        # Belt and braces with mark_task_done's own cancellation (S3): a
        # reminder can still be claimed here if it was already picked up by
        # a worker before the task was completed, or if cancellation ever
        # has a gap — the scheduler is the last line before a DM actually
        # goes out, so it re-checks status itself rather than trusting the
        # cancellation to have always run.
        if task is None or task.status != STATUS_OPEN:
            await _finish(repo, session, reminder_id)
            continue

        workspace = await WorkspaceRepository(session).get(reminder.team_id)
        if workspace is None or workspace.uninstalled_at is not None:
            await _finish(repo, session, reminder_id)
            continue

        try:
            # Decrypts the token; ValueError means an unknown key version or
            # an undecryptable token. The token is never in the message.
            client = build_client_for_workspace(workspace, cipher)
        except ValueError:
            logger.warning(
                "reminder %s: cannot decrypt token for team %s; marking sent",
                reminder_id, reminder.team_id, exc_info=True,
            )  # fmt: skip
            await _finish(repo, session, reminder_id)
            continue

        delivered, failure = await _deliver(client, reminder_id, reminder, task)
        if failure is not None and not delivered:
            # Nobody has been DMed yet, so a retry can't duplicate anything.
            code = _slack_error_code(failure) if isinstance(failure, SlackApiError) else ""
            logger.warning(
                "reminder %s delivery failed (%s); will retry",
                reminder_id, code or type(failure).__name__, exc_info=failure,
            )  # fmt: skip
            await _retry_later(repo, session, reminder_id)
            continue
        if failure is not None:
            # Someone already got this reminder. Retrying would DM them
            # again, so accept the lost copy for the remaining recipient.
            logger.warning(
                "reminder %s delivered to %d recipient(s) then failed; "
                "marking sent rather than re-sending",
                reminder_id, len(delivered), exc_info=failure,
            )  # fmt: skip
        await _finish(repo, session, reminder_id)
        if delivered:
            sent += 1

    return sent
