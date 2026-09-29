"""Tables that support the mechanics of the system, not its domain data.

`ProcessedEvent` dedupes Slack's retried deliveries (up to 3x on a missed 3-second ack)
so one slow response can't create the same task three times. `OAuthState` holds a
pending OAuth `state` value with a TTL — checked on callback as a CSRF control, for
both the Slack install flow and the Google account-linking flow.
"""

from datetime import datetime

from sqlalchemy import DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

PURPOSE_SLACK_INSTALL = "slack_install"
PURPOSE_GOOGLE_LINK = "google_link"


class ProcessedEvent(Base):
    __tablename__ = "processed_events"
    __table_args__ = (Index("ix_processed_events_processed_at", "processed_at"),)

    event_id: Mapped[str] = mapped_column(primary_key=True)
    team_id: Mapped[str] = mapped_column(nullable=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OAuthState(Base):
    __tablename__ = "oauth_states"

    state: Mapped[str] = mapped_column(primary_key=True)
    purpose: Mapped[str] = mapped_column(nullable=False)
    # Both nullable: an in-flight Slack install has no team_id yet; a Slack-only
    # state (install) has no slack_user_id yet either.
    team_id: Mapped[str | None] = mapped_column()
    slack_user_id: Mapped[str | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
