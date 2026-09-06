"""The whitelist. This IS the injection defence, not a cleverer prompt — see
ARCHITECTURE.md's security boundary. The model may only ever request a tool
that exists in TOOLS below, with arguments matching that tool's Pydantic
schema; `team_id` and `slack_user_id` are always supplied by the server from
the authenticated Slack context, never accepted from the model's arguments.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import task_service


class CreateTaskArgs(BaseModel):
    assignee_slack_id: str
    title: str
    due_at_utc: datetime


class ListTasksArgs(BaseModel):
    pass


async def _handle_create_task(
    session: AsyncSession, team_id: str, slack_user_id: str, args: CreateTaskArgs
) -> str:
    task = await task_service.create_task(
        session,
        team_id=team_id,
        creator_slack_id=slack_user_id,
        assignee_slack_id=args.assignee_slack_id,
        title=args.title,
        due_at_utc=args.due_at_utc,
        channel_id="agent",
    )
    await session.commit()
    return f'Created task "{task.title}" (id {task.id}), due {args.due_at_utc.isoformat()}.'


async def _handle_list_tasks(
    session: AsyncSession, team_id: str, slack_user_id: str, args: ListTasksArgs
) -> str:
    tasks = await task_service.list_open_tasks(
        session, team_id=team_id, assignee_slack_id=slack_user_id
    )
    if not tasks:
        return "No open tasks."
    return "\n".join(f"- {t.title} (due {t.due_at_utc.isoformat()})" for t in tasks)


@dataclass
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[[AsyncSession, str, str, BaseModel], Awaitable[str]]

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args_model.model_json_schema(),
            },
        }


TOOLS: dict[str, Tool] = {
    "create_task": Tool(
        name="create_task",
        description=(
            "Create a task assigned to a Slack user, with a title and an ISO 8601 "
            "due date/time that MUST include a timezone offset."
        ),
        args_model=CreateTaskArgs,
        handler=_handle_create_task,
    ),
    "list_tasks": Tool(
        name="list_tasks",
        description="List the current user's own open tasks.",
        args_model=ListTasksArgs,
        handler=_handle_list_tasks,
    ),
}
