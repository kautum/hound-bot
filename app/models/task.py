"""A task, assignable by anyone in the workspace to anyone in the workspace.

`creator_slack_id` and `assignee_slack_id` are deliberately separate columns — when a
task goes overdue, escalation notifies the creator, and that's not derivable from the
assignee once assignment is open to anyone. See ARCHITECTURE.md's data model section.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# escalation_level on the linked Reminder walks: 0 = upcoming reminder,
# 1 = due today, 2 = overdue (also notifies creator_slack_id).
STATUS_OPEN = "open"
STATUS_DONE = "done"


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_team_id_status", "team_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[str] = mapped_column(ForeignKey("workspaces.team_id"), nullable=False)
    creator_slack_id: Mapped[str] = mapped_column(nullable=False)
    assignee_slack_id: Mapped[str] = mapped_column(nullable=False)
    title: Mapped[str] = mapped_column(nullable=False)
    due_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(nullable=False, default=STATUS_OPEN)
    channel_id: Mapped[str] = mapped_column(nullable=False)
    recurrence_interval_days: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
