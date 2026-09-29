"""Slack interactivity (Block Kit button clicks, etc.).

The payload arrives as form-encoded data with a single field `payload`
containing a JSON string. Signature verification is over the RAW request
body exactly as with the other endpoints — the JSON-inside-a-form-field
detail only affects what we do AFTER verifying the signature.
"""

import json
import logging
import uuid

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.core.security import verify_slack_signature
from app.models.meeting import Meeting
from app.repositories.inbound_job_repository import InboundJobRepository
from app.services import task_service

logger = logging.getLogger(__name__)

router = APIRouter()


async def _send_slack_response(response_url: str, text: str) -> None:
    """Fire-and-forget POST to Slack's response_url — the request has
    already been acknowledged, so a failure here has no user-facing retry
    path. Logged rather than swallowed (never `except: pass`), matching
    every other response_url reply in this codebase (see app/worker.py)."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(response_url, json={"response_type": "ephemeral", "text": text})
    except httpx.HTTPError:
        logger.warning("failed to post interaction response to %s", response_url, exc_info=True)


@router.post("/slack/interactions")
async def slack_interactions(
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> dict:
    raw_body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not settings.slack_signing_secret:
        raise HTTPException(status_code=500, detail="SLACK_SIGNING_SECRET is not configured")
    if not verify_slack_signature(raw_body, timestamp, signature, settings.slack_signing_secret):
        raise HTTPException(status_code=401, detail="invalid Slack signature")

    form = await request.form()
    payload_json = form.get("payload")
    if not payload_json:
        raise HTTPException(status_code=400, detail="missing payload")

    payload = json.loads(payload_json)

    actions = payload.get("actions", [])
    if not actions:
        return {"status": "ignored"}

    action = actions[0]
    action_id = action.get("action_id")
    value = action.get("value")

    team_id = payload.get("team", {}).get("id")
    user_id = payload.get("user", {}).get("id")
    response_url = payload.get("response_url")

    if action_id == "task_done":
        try:
            task_id = uuid.UUID(value)
        except ValueError:
            if response_url:
                background_tasks.add_task(_send_slack_response, response_url, "Invalid task ID.")
            return {"status": "ok"}

        try:
            task = await task_service.mark_task_done(
                session, team_id=team_id, task_id=task_id, requesting_slack_user_id=user_id
            )
        except task_service.TaskAuthorizationError as exc:
            if response_url:
                background_tasks.add_task(_send_slack_response, response_url, str(exc))
            await session.commit()
            return {"status": "ok"}

        await session.commit()
        if task is None:
            if response_url:
                background_tasks.add_task(
                    _send_slack_response,
                    response_url,
                    "No task found with that ID in this workspace.",
                )
            return {"status": "ok"}

        if response_url:
            confirmation = f'Marked "{task.title}" done.'
            background_tasks.add_task(_send_slack_response, response_url, confirmation)
        return {"status": "ok"}

    if action_id == "meet_book":
        try:
            meeting_id = uuid.UUID(value)
        except ValueError:
            if response_url:
                background_tasks.add_task(_send_slack_response, response_url, "Invalid meeting ID.")
            return {"status": "ok"}

        await InboundJobRepository(session).enqueue(
            team_id=team_id,
            event_type="meet_book",
            payload={
                "meeting_id": str(meeting_id),
                "requesting_user_id": user_id,
                "response_url": response_url,
            },
        )
        await session.commit()

        if response_url:
            background_tasks.add_task(
                _send_slack_response,
                response_url,
                "Booking that meeting — I'll follow up here shortly.",
            )
        return {"status": "ok"}

    if action_id == "meet_cancel":
        try:
            meeting_id = uuid.UUID(value)
        except ValueError:
            if response_url:
                background_tasks.add_task(_send_slack_response, response_url, "Invalid meeting ID.")
            return {"status": "ok"}

        result = await session.execute(select(Meeting).filter_by(id=meeting_id, team_id=team_id))
        meeting = result.scalar_one_or_none()
        if meeting is None:
            if response_url:
                background_tasks.add_task(
                    _send_slack_response, response_url, "No meeting with that ID in this workspace."
                )
            return {"status": "ok"}

        if meeting.organiser_slack_id != user_id:
            if response_url:
                background_tasks.add_task(
                    _send_slack_response, response_url, "Only the meeting organiser can cancel it."
                )
            return {"status": "ok"}

        await InboundJobRepository(session).enqueue(
            team_id=team_id,
            event_type="meet_cancel",
            payload={
                "meeting_id": str(meeting_id),
                "requesting_user_id": user_id,
                "response_url": response_url,
            },
        )
        await session.commit()

        if response_url:
            background_tasks.add_task(
                _send_slack_response,
                response_url,
                "Cancelling that meeting — I'll follow up here shortly.",
            )
        return {"status": "ok"}

    if response_url:
        background_tasks.add_task(_send_slack_response, response_url, "Unknown action.")
    return {"status": "ok"}
