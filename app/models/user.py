"""A Slack user, optionally linked to a Google Calendar.

`google_refresh_token_enc` is null until the person runs `/link-calendar` — that's a
normal, expected state, not an error. See ARCHITECTURE.md on the two OAuth flows.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, LargeBinary
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (Index("ix_users_team_id", "team_id"),)

    # Composite primary key: a Slack user ID is unique only within its own
    # workspace, not globally, so slack_user_id alone cannot be the key —
    # see migration 0006 and tests/test_tenant_isolation.py.
    slack_user_id: Mapped[str] = mapped_column(primary_key=True)
    team_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.team_id"), primary_key=True, nullable=False
    )
    tz: Mapped[str] = mapped_column(nullable=False)
    google_refresh_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    # Set instead of silently retrying or deleting the row when Google's
    # refresh token stops working — see app/calendar/provider.py's
    # InvalidGrantError and ARCHITECTURE.md's OAuth flows section.
    google_link_broken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Captured at link time — needed to invite this person as a calendar
    # attendee when booking a meeting.
    google_email: Mapped[str | None] = mapped_column()
