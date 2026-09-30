from sqlalchemy import func, select

from app.models.task import STATUS_OPEN, Task
from app.repositories.base import TenantScopedRepository


class TaskRepository(TenantScopedRepository[Task]):
    model = Task

    def _open_filters(self, assignee_slack_id: str | None) -> list:
        filters = [Task.team_id == self._team_id, Task.status == STATUS_OPEN]
        if assignee_slack_id is not None:
            filters.append(Task.assignee_slack_id == assignee_slack_id)
        return filters

    async def list_open_page(
        self, *, limit: int, assignee_slack_id: str | None = None
    ) -> list[Task]:
        """At most `limit` open tasks, earliest due first (id breaks ties so
        the order is stable)."""
        stmt = (
            select(Task)
            .where(*self._open_filters(assignee_slack_id))
            .order_by(Task.due_at_utc, Task.id)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_open(self, *, assignee_slack_id: str | None = None) -> int:
        stmt = select(func.count()).select_from(Task).where(
            *self._open_filters(assignee_slack_id)
        )
        return int(await self._session.scalar(stmt) or 0)
