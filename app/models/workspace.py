"""A Slack workspace that has installed the bot.

`team_id` is the tenant key referenced by every other table in this schema.
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, LargeBinary
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Workspace(Base):
    __tablename__ = "workspaces"

    team_id: Mapped[str] = mapped_column(primary_key=True)
    bot_token_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    uninstalled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
