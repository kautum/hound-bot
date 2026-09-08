"""A meeting, and the 2-8 Slack users being scheduled into it.

`Meeting.organiser_slack_id` is who requested the meeting; `MeetingParticipant` rows
are everyone the availability-intersection function must find a slot for, organiser
included. Unit 4.6 (slot proposal + book-on-click) is what writes the participant rows.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

STATUS_PROPOSED = "proposed"
STATUS_BOOKED = "booked"


class Meeting(Base):
    __tablename__ = "meetings"
    __table_args__ = (Index("ix_meetings_team_id", "team_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[str] = mapped_column(ForeignKey("workspaces.team_id"), nullable=False)
    organiser_slack_id: Mapped[str] = mapped_column(nullable=False)
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(nullable=False, default=STATUS_PROPOSED)
    google_event_id: Mapped[str | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    # Persisted so a later "book this" action can retrieve the slot without
    # re-running the whole availability search — without this, propose and
    # book would need to happen in the same request, which they don't.
    proposed_start_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MeetingParticipant(Base):
    __tablename__ = "meeting_participants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["team_id", "slack_user_id"], ["users.team_id", "users.slack_user_id"]
        ),
    )

    meeting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meetings.id"), primary_key=True
    )
    slack_user_id: Mapped[str] = mapped_column(primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("workspaces.team_id"), nullable=False)
