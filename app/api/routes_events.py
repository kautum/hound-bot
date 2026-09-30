"""Slack Events API ingestion. Ack-and-enqueue, always — see ARCHITECTURE.md's
3-second rule. The signature is verified against the *raw* body before any
JSON parsing; re-serialising the payload would silently break that check.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.request_guards import (
    MAX_EVENT_ID_LENGTH,
    bad_request,
    decode_utf8,
    is_workspace_installed,
    parse_json_object,
    read_limited_body,
)
from app.core.config import settings
from app.core.db import get_session
from app.core.security import verify_slack_signature
from app.repositories.inbound_job_repository import InboundJobRepository
from app.repositories.processed_event_repository import ProcessedEventRepository

logger = logging.getLogger(__name__)

router = APIRouter()

# Event types the in-process worker knows how to handle. Anything else is
# acked and dropped rather than silently queued forever.
HANDLED_EVENT_TYPES = {"app_mention", "app_uninstalled", "app_home_opened"}


@router.post("/slack/events")
async def slack_events(
    request: Request, session: AsyncSession = Depends(get_session)
) -> dict:
    raw_body = await read_limited_body(request)
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not settings.slack_signing_secret:
        raise HTTPException(status_code=500, detail="SLACK_SIGNING_SECRET is not configured")

    if not verify_slack_signature(
        raw_body, timestamp, signature, settings.slack_signing_secret
    ):
        raise HTTPException(status_code=401, detail="invalid Slack signature")

    body = parse_json_object(decode_utf8(raw_body), "event body")

    # Slack sends this once, when the Events API URL is first configured —
    # it must be echoed back verbatim, unrelated to the ack-and-enqueue path.
    if body.get("type") == "url_verification":
        challenge = body.get("challenge")
        if not isinstance(challenge, str):
            raise bad_request("url_verification without a string challenge")
        return {"challenge": challenge}

    if body.get("type") != "event_callback":
        return {"status": "ignored"}

    event = body.get("event")
    event_id = body.get("event_id")
    team_id = body.get("team_id")
    if not isinstance(event, dict):
        raise bad_request("event is not an object")
    if not isinstance(event.get("type"), str):
        raise bad_request("event type is missing or not a string")
    if not isinstance(event_id, str) or not 0 < len(event_id) <= MAX_EVENT_ID_LENGTH:
        raise bad_request("event_id is missing, not a string, or too long")
    if not isinstance(team_id, str) or not team_id:
        raise bad_request("team_id is missing or not a string")

    # Slack Connect: an event from a user of another org's workspace, delivered
    # under our team_id. Not ours to act on.
    if "user_team" in event and event["user_team"] != team_id:
        logger.info("ignoring Slack Connect event for team %s", team_id)
        return {"status": "ignored"}

    if not await is_workspace_installed(session, team_id):
        logger.info("ignoring event for team %s: workspace not installed", team_id)
        return {"status": "ignored"}

    is_new = await ProcessedEventRepository(session).mark_processed_if_new(event_id, team_id)
    if is_new and event["type"] in HANDLED_EVENT_TYPES:
        await InboundJobRepository(session).enqueue(
            team_id=team_id, event_type=event["type"], payload=event
        )

    await session.commit()
    return {"status": "ok"}
