"""Reminders are drained by the cron tick, cross-tenant, same SKIP LOCKED
claim pattern as inbound_jobs — see ARCHITECTURE.md's job queue section.
Not a TenantScopedRepository for the same reason InboundJobRepository isn't.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reminder import Reminder


class ReminderRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(
        self, task_id: uuid.UUID, team_id: str, fire_at_utc: datetime, escalation_level: int
    ) -> Reminder:
        reminder = Reminder(
            task_id=task_id,
            team_id=team_id,
            fire_at_utc=fire_at_utc,
            escalation_level=escalation_level,
        )
        self._session.add(reminder)
        await self._session.flush()
        return reminder

    async def claim_due_batch(self, limit: int = 10) -> list[Reminder]:
        stmt = (
            select(Reminder)
            .where(
                Reminder.fire_at_utc <= datetime.now(UTC),
                Reminder.sent_at.is_(None),
                Reminder.claimed_at.is_(None),
            )
            .order_by(Reminder.fire_at_utc)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self._session.execute(stmt)
        reminders = list(result.scalars().all())
        now = datetime.now(UTC)
        for reminder in reminders:
            # S8: marked claimed inside this same locking transaction, so a
            # per-reminder commit later in the batch can't let a second
            # worker re-claim rows that are still technically "unsent".
            reminder.claimed_at = now
        await self._session.flush()
        return reminders

    async def mark_sent(self, reminder_id: uuid.UUID) -> None:
        reminder = await self._session.get(Reminder, reminder_id)
        if reminder is not None:
            reminder.sent_at = datetime.now(UTC)
            await self._session.flush()

    async def cancel_pending_for_task(self, task_id: uuid.UUID) -> None:
        """Deletes every not-yet-sent reminder for a task — called when the
        task is completed, so a finished task stops nagging its assignee. See
        S3: this and the scheduler's own status check (belt and braces,
        because the scheduler is the last line before a DM actually goes
        out) are both needed, not either alone."""
        stmt = select(Reminder).where(
            Reminder.task_id == task_id, Reminder.sent_at.is_(None)
        )
        result = await self._session.execute(stmt)
        for reminder in result.scalars().all():
            await self._session.delete(reminder)
        await self._session.flush()
