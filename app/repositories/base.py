"""Base repository enforcing `team_id` isolation on every query.

Every tenant-scoped table (every table except `workspaces` itself, which IS the
tenant) goes through a subclass of this rather than a hand-written query. See
ARCHITECTURE.md: "every query filters on team_id, in the data-access layer, not
by remembering it at each call site." A leak here is the most serious bug this
product could ship — see tests/test_tenant_isolation.py for the check that matters.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class TenantScopedRepository[ModelT]:
    model: type[ModelT]

    def __init__(self, session: AsyncSession, team_id: str):
        if not team_id:
            raise ValueError(
                "team_id is required — a repository with no tenant scope is a bug, not a feature"
            )
        self._session = session
        self._team_id = team_id

    async def get(self, **filters: object) -> ModelT | None:
        stmt = select(self.model).filter_by(team_id=self._team_id, **filters)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(self, **filters: object) -> list[ModelT]:
        stmt = select(self.model).filter_by(team_id=self._team_id, **filters)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def add(self, **fields: object) -> ModelT:
        obj = self.model(team_id=self._team_id, **fields)
        self._session.add(obj)
        await self._session.flush()
        return obj
