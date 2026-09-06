"""Workspace is the tenant root — it doesn't go through TenantScopedRepository
(there's no parent tenant to scope it by; team_id *is* the identity here).
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace import Workspace


class WorkspaceRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, team_id: str) -> Workspace | None:
        result = await self._session.execute(
            select(Workspace).filter_by(team_id=team_id)
        )
        return result.scalar_one_or_none()

    async def upsert_on_install(
        self, team_id: str, bot_token_enc: bytes, key_version: int
    ) -> Workspace:
        """Called from the OAuth callback. Re-installing an existing workspace
        (token rotated, scopes changed) updates in place rather than erroring."""
        existing = await self.get(team_id)
        if existing is not None:
            existing.bot_token_enc = bot_token_enc
            existing.key_version = key_version
            existing.uninstalled_at = None
            await self._session.flush()
            return existing

        workspace = Workspace(
            team_id=team_id,
            bot_token_enc=bot_token_enc,
            key_version=key_version,
            installed_at=datetime.now(UTC),
        )
        self._session.add(workspace)
        await self._session.flush()
        return workspace

    async def mark_uninstalled(self, team_id: str) -> None:
        """Handles `app_uninstalled` / `tokens_revoked` — see ARCHITECTURE.md's
        lifecycle notes. Does not delete the row (keeps the audit trail); the
        bot simply refuses to act for a workspace with uninstalled_at set."""
        workspace = await self.get(team_id)
        if workspace is not None:
            workspace.uninstalled_at = datetime.now(UTC)
            await self._session.flush()
