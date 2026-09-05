from app.models.task import Task
from app.repositories.base import TenantScopedRepository


class TaskRepository(TenantScopedRepository[Task]):
    model = Task
