"""Dedupes Slack's retried event deliveries (up to 3x on a missed 3-second ack).

Uses INSERT ... ON CONFLICT DO NOTHING so the check-and-insert is atomic — two
concurrent requests for the same event_id can't both see "not yet processed"
and both proceed. See ARCHITECTURE.md's ack-and-enqueue section.
"""

from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.operational import ProcessedEvent


class ProcessedEventRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def mark_processed_if_new(self, event_id: str, team_id: str) -> bool:
        """Returns True if this is the first time we've seen `event_id` (the
        caller should proceed), False if it's a retry (the caller should ack
        and do nothing else)."""
        stmt = (
            insert(ProcessedEvent)
            .values(event_id=event_id, team_id=team_id, processed_at=datetime.now(UTC))
            .on_conflict_do_nothing(index_elements=["event_id"])
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount == 1
