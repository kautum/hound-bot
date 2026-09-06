"""Not a TenantScopedRepository — Slack user IDs are looked up directly by
their own ID (the primary key), same shape as WorkspaceRepository.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, slack_user_id: str) -> User | None:
        result = await self._session.execute(select(User).filter_by(slack_user_id=slack_user_id))
        return result.scalar_one_or_none()

    async def get_or_create(self, slack_user_id: str, team_id: str, tz: str) -> User:
        user = await self.get(slack_user_id)
        if user is not None:
            return user
        user = User(slack_user_id=slack_user_id, team_id=team_id, tz=tz, key_version=1)
        self._session.add(user)
        await self._session.flush()
        return user

    async def store_google_link(
        self, slack_user_id: str, *, refresh_token_enc: bytes, key_version: int, google_email: str
    ) -> None:
        user = await self.get(slack_user_id)
        if user is None:
            raise ValueError(f"no user row for {slack_user_id} — must exist before linking")
        user.google_refresh_token_enc = refresh_token_enc
        user.key_version = key_version
        user.google_email = google_email
        user.google_link_broken_at = None  # a fresh link clears any prior broken flag
        await self._session.flush()

    async def mark_google_link_broken(self, slack_user_id: str) -> None:
        user = await self.get(slack_user_id)
        if user is not None:
            user.google_link_broken_at = datetime.now(UTC)
            await self._session.flush()
