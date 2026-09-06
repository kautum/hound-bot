"""Aggregate queries behind the weekly digest — the piece that serves the
project's data-analyst/data-scientist angle (Part 11). Every query is
tenant-scoped explicitly; this is read-only reporting, not a place to relax
the team_id rule just because nothing here is destructive.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.meeting import Meeting
from app.models.task import STATUS_DONE, STATUS_OPEN, Task


async def task_completion_rate(session: AsyncSession, team_id: str, since: datetime) -> float:
    """Of tasks *created* since `since`, what fraction are done? Returns 1.0
    if there were no tasks in the window — an empty period isn't a 0% rate."""
    total = await session.scalar(
        select(func.count(Task.id)).where(Task.team_id == team_id, Task.created_at >= since)
    )
    done = await session.scalar(
        select(func.count(Task.id)).where(
            Task.team_id == team_id, Task.created_at >= since, Task.status == STATUS_DONE
        )
    )
    if not total:
        return 1.0
    return done / total


async def overdue_task_count(session: AsyncSession, team_id: str) -> int:
    now = datetime.now(UTC)
    count = await session.scalar(
        select(func.count(Task.id)).where(
            Task.team_id == team_id, Task.status == STATUS_OPEN, Task.due_at_utc < now
        )
    )
    return count or 0


async def meeting_count_since(session: AsyncSession, team_id: str, since: datetime) -> int:
    count = await session.scalar(
        select(func.count(Meeting.id)).where(
            Meeting.team_id == team_id, Meeting.created_at >= since
        )
    )
    return count or 0


async def build_weekly_digest(session: AsyncSession, team_id: str) -> str:
    since = datetime.now(UTC) - timedelta(days=7)
    rate = await task_completion_rate(session, team_id, since)
    overdue = await overdue_task_count(session, team_id)
    meetings = await meeting_count_since(session, team_id, since)

    return (
        "Weekly digest\n"
        f"- Task completion rate: {rate:.0%}\n"
        f"- Overdue tasks: {overdue}\n"
        f"- Meetings this week: {meetings}"
    )
