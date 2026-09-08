"""A scheduled nudge for a task, drained by the cron tick every 5 minutes.

`team_id` is denormalised here even though it's reachable via `task_id` — see
ARCHITECTURE.md's data model section for why: it lets the tenant-scoped repository
apply one uniform `team_id` filter instead of a join, on every table, without exception.

The claim query this table is built for (see ARCHITECTURE.md's job queue pattern):

    SELECT * FROM reminders
    WHERE fire_at_utc <= now() AND sent_at IS NULL
    FOR UPDATE SKIP LOCKED
    LIMIT 10;
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Reminder(Base):
    __tablename__ = "reminders"
    __table_args__ = (Index("ix_reminders_fire_at_sent_at", "fire_at_utc", "sent_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    team_id: Mapped[str] = mapped_column(ForeignKey("workspaces.team_id"), nullable=False)
    fire_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # S8: without this, the worker's per-job commit ends the claiming
    # transaction early and releases FOR UPDATE SKIP LOCKED on the rest of
    # a claimed batch while those rows are still "unsent" — letting a second
    # worker re-claim and re-send them. See migration 0007.
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalation_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
