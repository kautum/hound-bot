"""A Slack user, optionally linked to a Google Calendar.

`google_refresh_token_enc` is null until the person runs `/link-calendar` — that's a
normal, expected state, not an error. See ARCHITECTURE.md on the two OAuth flows.
"""

from sqlalchemy import ForeignKey, Index, Integer, LargeBinary
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (Index("ix_users_team_id", "team_id"),)

    slack_user_id: Mapped[str] = mapped_column(primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("workspaces.team_id"), nullable=False)
    tz: Mapped[str] = mapped_column(nullable=False)
    google_refresh_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False)
