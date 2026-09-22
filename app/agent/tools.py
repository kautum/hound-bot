"""The whitelist. This IS the injection defence, not a cleverer prompt — see
ARCHITECTURE.md's security boundary. The model may only ever request a tool
that exists in TOOLS below, with arguments matching that tool's Pydantic
schema; `team_id` and `slack_user_id` are always supplied by the server from
the authenticated Slack context (via AgentContext), never accepted from the
model's arguments.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.calendar.provider import CalendarProvider
from app.core.security import TokenCipher
from app.services import analytics_service, meeting_service, task_service


@dataclass
class AgentContext:
    """Everything a tool handler needs that must never come from the model.
    `calendar_provider`/`cipher` are None when Google/calendar features
    aren't configured — tools that need them degrade to a clear message
    rather than crashing the whole turn."""

    session: AsyncSession
    team_id: str
    slack_user_id: str
    calendar_provider: CalendarProvider | None = None
    cipher: TokenCipher | None = None


class CreateTaskArgs(BaseModel):
    assignee_slack_id: str
    title: str
    due_at_utc: datetime


class ListTasksArgs(BaseModel):
    pass


class GetDigestArgs(BaseModel):
    pass


class ProposeMeetingArgs(BaseModel):
    participant_slack_ids: list[str]
    duration_minutes: int
    search_window_start_utc: datetime
    search_window_end_utc: datetime


async def _handle_create_task(ctx: AgentContext, args: CreateTaskArgs) -> str:
    task = await task_service.create_task(
        ctx.session,
        team_id=ctx.team_id,
        creator_slack_id=ctx.slack_user_id,
        assignee_slack_id=args.assignee_slack_id,
        title=args.title,
        due_at_utc=args.due_at_utc,
        channel_id="agent",
    )
    await ctx.session.commit()
    return f'Created task "{task.title}" (id {task.id}), due {args.due_at_utc.isoformat()}.'


async def _handle_list_tasks(ctx: AgentContext, args: ListTasksArgs) -> str:
    tasks = await task_service.list_open_tasks(
        ctx.session, team_id=ctx.team_id, assignee_slack_id=ctx.slack_user_id
    )
    if not tasks:
        return "No open tasks."
    return "\n".join(f"- {t.title} (due {t.due_at_utc.isoformat()})" for t in tasks)


async def _handle_get_digest(ctx: AgentContext, args: GetDigestArgs) -> str:
    return await analytics_service.build_weekly_digest(ctx.session, ctx.team_id)


async def _handle_propose_meeting(ctx: AgentContext, args: ProposeMeetingArgs) -> str:
    if ctx.calendar_provider is None or ctx.cipher is None:
        return "Calendar scheduling isn't configured on this workspace yet."

    result = await meeting_service.propose_meeting(
        ctx.session,
        ctx.calendar_provider,
        ctx.cipher,
        team_id=ctx.team_id,
        organiser_slack_id=ctx.slack_user_id,
        participant_slack_ids=args.participant_slack_ids,
        duration=timedelta(minutes=args.duration_minutes),
        search_window_start_utc=args.search_window_start_utc,
        search_window_end_utc=args.search_window_end_utc,
    )
    await ctx.session.commit()

    if result.meeting is None:
        return "No mutual free slot exists in that window for the linked calendars."

    note = ""
    if result.unavailable_participants:
        names = ", ".join(result.unavailable_participants)
        note = f" (couldn't check {names} — they haven't linked a calendar yet)"

    return (
        f"Proposed {args.duration_minutes} min at {result.slot_start_utc.isoformat()} UTC"
        f"{note}. Not booked yet — run `/meet book {result.meeting.id}` to confirm it."
    )


@dataclass
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[[AgentContext, BaseModel], Awaitable[str]]

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
    "get_digest": Tool(
        name="get_digest",
        description=(
            "Get a weekly digest of task completion rate, overdue tasks, and meetings. "
            "The digest always covers the last 7 days regardless of when it is asked."
        ),
        args_model=GetDigestArgs,
        handler=_handle_get_digest,
    ),
    "propose_meeting": Tool(
        name="propose_meeting",
        description=(
            "Find the earliest mutual free slot for 1-7 other Slack user IDs plus the "
            "requester, within an explicit ISO 8601 UTC search window (both bounds "
            "required, with timezone offsets). Does not book the meeting."
        ),
        args_model=ProposeMeetingArgs,
        handler=_handle_propose_meeting,
    ),
}
