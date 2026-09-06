"""Slack Events API ingestion. Ack-and-enqueue, always — see ARCHITECTURE.md's
3-second rule. The signature is verified against the *raw* body before any
JSON parsing; re-serialising the payload would silently break that check.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.core.security import verify_slack_signature
from app.repositories.inbound_job_repository import InboundJobRepository
from app.repositories.processed_event_repository import ProcessedEventRepository

router = APIRouter()

# Event types the in-process worker knows how to handle. Anything else is
# acked and dropped rather than silently queued forever.
HANDLED_EVENT_TYPES = {"app_mention"}


@router.post("/slack/events")
async def slack_events(
    request: Request, session: AsyncSession = Depends(get_session)
) -> dict:
    raw_body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not settings.slack_signing_secret:
        raise HTTPException(status_code=500, detail="SLACK_SIGNING_SECRET is not configured")

    if not verify_slack_signature(
        raw_body, timestamp, signature, settings.slack_signing_secret
    ):
        raise HTTPException(status_code=401, detail="invalid Slack signature")

    body = json.loads(raw_body)

    # Slack sends this once, when the Events API URL is first configured —
    # it must be echoed back verbatim, unrelated to the ack-and-enqueue path.
    if body.get("type") == "url_verification":
        return {"challenge": body["challenge"]}

    if body.get("type") != "event_callback":
        return {"status": "ignored"}

    event = body["event"]
    event_id = body["event_id"]
    team_id = body["team_id"]

    is_new = await ProcessedEventRepository(session).mark_processed_if_new(event_id, team_id)
    if is_new and event.get("type") in HANDLED_EVENT_TYPES:
        await InboundJobRepository(session).enqueue(
            team_id=team_id, event_type=event["type"], payload=event
        )

    await session.commit()
    return {"status": "ok"}
