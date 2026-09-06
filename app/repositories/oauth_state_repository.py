"""Issues and consumes single-use OAuth `state` values — the CSRF control for
both OAuth flows (Slack install, Google account link). See ARCHITECTURE.md.
"""

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.operational import OAuthState

DEFAULT_TTL_SECONDS = 10 * 60


class OAuthStateRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def issue(
        self,
        purpose: str,
        *,
        team_id: str | None = None,
        slack_user_id: str | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> str:
        state = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        self._session.add(
            OAuthState(
                state=state,
                purpose=purpose,
                team_id=team_id,
                slack_user_id=slack_user_id,
                created_at=now,
                expires_at=now + timedelta(seconds=ttl_seconds),
            )
        )
        await self._session.flush()
        return state

    async def consume(self, state: str, *, expected_purpose: str) -> OAuthState | None:
        """Single-use: the row is deleted whether or not it's valid, so a
        captured state value can't be replayed even within its TTL."""
        result = await self._session.execute(select(OAuthState).filter_by(state=state))
        row = result.scalar_one_or_none()
        if row is None:
            return None

        await self._session.delete(row)
        await self._session.flush()

        if row.purpose != expected_purpose:
            return None
        if row.expires_at < datetime.now(UTC):
            return None
        return row
