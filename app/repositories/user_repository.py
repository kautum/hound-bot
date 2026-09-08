"""Tenant-scoped like every other entity repository. Slack user IDs are
unique only *within* a workspace, not globally — two different organisations
can and do have members with the same Slack user ID — so looking one up by
that ID alone can return another tenant's row entirely. See
tests/test_tenant_isolation.py for the check that proves this.
"""

from datetime import UTC, datetime

from app.models.user import User
from app.repositories.base import TenantScopedRepository


class UserRepository(TenantScopedRepository[User]):
    model = User

    async def get(self, slack_user_id: str) -> User | None:
        return await super().get(slack_user_id=slack_user_id)

    async def get_or_create(self, slack_user_id: str, tz: str) -> User:
        user = await self.get(slack_user_id)
        if user is not None:
            return user
        return await self.add(slack_user_id=slack_user_id, tz=tz, key_version=1)

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
