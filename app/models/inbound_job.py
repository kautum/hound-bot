"""The generic ack-and-enqueue job queue for anything triggered by a Slack event
that's too slow to handle within the 3-second ack window (mentions needing an LLM
call, meeting-scheduling requests needing calendar lookups).

Distinct from `Reminder`, which is time-triggered (drained by the cron tick).
This table is triggered immediately by an inbound event and claimed by the
in-process worker as soon as it's free — same FOR UPDATE SKIP LOCKED pattern.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

STATUS_PENDING = "pending"
STATUS_CLAIMED = "claimed"
STATUS_DONE = "done"
STATUS_FAILED = "failed"


class InboundJob(Base):
    __tablename__ = "inbound_jobs"
    __table_args__ = (Index("ix_inbound_jobs_status_created_at", "status", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[str] = mapped_column(ForeignKey("workspaces.team_id"), nullable=False)
    event_type: Mapped[str] = mapped_column(nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(nullable=False, default=STATUS_PENDING)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column()
