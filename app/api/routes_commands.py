"""The `/task` slash command. Fast, deterministic CRUD — no LLM, no ack-and-
enqueue needed, since these operations are quick enough to answer inline
within Slack's 3-second budget. See ARCHITECTURE.md's tools-first section.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.core.security import verify_slack_signature
from app.services import task_service
from app.services.task_command_parser import TaskCommandError, parse_task_add

router = APIRouter()


def _ephemeral(text: str) -> dict:
    return {"response_type": "ephemeral", "text": text}


@router.post("/slack/commands")
async def slack_commands(
    request: Request, session: AsyncSession = Depends(get_session)
) -> dict:
    raw_body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not settings.slack_signing_secret:
        raise HTTPException(status_code=500, detail="SLACK_SIGNING_SECRET is not configured")
    if not verify_slack_signature(raw_body, timestamp, signature, settings.slack_signing_secret):
        raise HTTPException(status_code=401, detail="invalid Slack signature")

    form = await request.form()
    text = str(form.get("text", "")).strip()
    team_id = str(form.get("team_id", ""))
    user_id = str(form.get("user_id", ""))
    channel_id = str(form.get("channel_id", ""))

    if text.startswith("add "):
        try:
            assignee, title, due_at_utc = parse_task_add(text.removeprefix("add ").strip())
        except TaskCommandError as exc:
            return _ephemeral(str(exc))

        task = await task_service.create_task(
            session,
            team_id=team_id,
            creator_slack_id=user_id,
            assignee_slack_id=assignee,
            title=title,
            due_at_utc=due_at_utc,
            channel_id=channel_id,
        )
        await session.commit()
        return _ephemeral(f'Created task "{task.title}", due {due_at_utc.isoformat()}.')

    if text == "list":
        tasks = await task_service.list_open_tasks(
            session, team_id=team_id, assignee_slack_id=user_id
        )
        if not tasks:
            return _ephemeral("No open tasks assigned to you.")
        lines = [f"• {t.title} — due {t.due_at_utc.isoformat()} ({t.id})" for t in tasks]
        return _ephemeral("\n".join(lines))

    if text.startswith("done "):
        raw_id = text.removeprefix("done ").strip()
        try:
            task_id = uuid.UUID(raw_id)
        except ValueError:
            return _ephemeral(f"{raw_id!r} isn't a valid task ID.")

        task = await task_service.mark_task_done(session, team_id=team_id, task_id=task_id)
        await session.commit()
        if task is None:
            return _ephemeral("No task found with that ID in this workspace.")
        return _ephemeral(f'Marked "{task.title}" done.')

    return _ephemeral(
        "Usage:\n"
        "`/task add @assignee Title | 2026-09-10T17:00+00:00`\n"
        "`/task list`\n"
        "`/task done <task-id>`"
    )
