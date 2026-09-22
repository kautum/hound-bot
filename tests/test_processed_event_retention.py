"""Tests for the 7-day retention sweep on processed_events.

The sweep deletes rows older than 7 days while preserving recent rows to
maintain Slack's dedupe window. This test verifies:
1. Old rows (older than 7 days) are deleted
2. Recent rows (within 7 days) are preserved
3. The dedupe logic still works after a sweep (a replayed event_id is rejected)
"""

from datetime import UTC, datetime, timedelta

from app.models.operational import ProcessedEvent
from app.repositories.processed_event_repository import ProcessedEventRepository


class TestProcessedEventRetention:
    async def test_sweep_deletes_old_rows_but_preserves_recent(self, db_session):
        """Rows older than 7 days are deleted, rows within 7 days are preserved."""
        repo = ProcessedEventRepository(db_session)

        # Add an old event (8 days ago)
        old_event = ProcessedEvent(
            event_id="old-event-123",
            team_id="team-A",
            processed_at=datetime.now(UTC) - timedelta(days=8),
        )
        db_session.add(old_event)

        # Add a recent event (1 hour ago)
        recent_event = ProcessedEvent(
            event_id="recent-event-456",
            team_id="team-A",
            processed_at=datetime.now(UTC) - timedelta(hours=1),
        )
        db_session.add(recent_event)
        await db_session.commit()

        # Run the sweep
        deleted = await repo.sweep_old_processed_events()
        await db_session.commit()

        # Should have deleted exactly 1 row (the old one)
        assert deleted == 1

        # Verify old event is gone
        from sqlalchemy import select

        old_result = await db_session.execute(
            select(ProcessedEvent).where(ProcessedEvent.event_id == "old-event-123")
        )
        assert old_result.scalar_one_or_none() is None

        # Verify recent event still exists
        recent_result = await db_session.execute(
            select(ProcessedEvent).where(ProcessedEvent.event_id == "recent-event-456")
        )
        assert recent_result.scalar_one_or_none() is not None

    async def test_dedupe_still_works_after_sweep(self, db_session):
        """After a sweep, the dedupe logic still rejects replayed event_ids."""
        repo = ProcessedEventRepository(db_session)

        # Mark an event as processed
        event_id = "replay-test-789"
        is_new = await repo.mark_processed_if_new(event_id, "team-A")
        assert is_new is True
        await db_session.commit()

        # Re-marking the same event_id should return False (it's a replay)
        is_new_again = await repo.mark_processed_if_new(event_id, "team-A")
        assert is_new_again is False
        await db_session.commit()

        # Now add an old event and sweep it
        old_event = ProcessedEvent(
            event_id="old-sweep-event",
            team_id="team-A",
            processed_at=datetime.now(UTC) - timedelta(days=8),
        )
        db_session.add(old_event)
        await db_session.commit()

        deleted = await repo.sweep_old_processed_events()
        assert deleted == 1
        await db_session.commit()

        # After the sweep, the original event_id should still be deduped
        is_new_after_sweep = await repo.mark_processed_if_new(event_id, "team-A")
        assert is_new_after_sweep is False

    async def test_sweep_returns_zero_when_no_old_rows(self, db_session):
        """If all rows are recent, sweep deletes nothing and returns 0."""
        repo = ProcessedEventRepository(db_session)

        # Add only recent events
        for i in range(5):
            event = ProcessedEvent(
                event_id=f"recent-{i}",
                team_id="team-A",
                processed_at=datetime.now(UTC) - timedelta(hours=i),
            )
            db_session.add(event)
        await db_session.commit()

        deleted = await repo.sweep_old_processed_events()
        assert deleted == 0
