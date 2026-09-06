"""The in-process worker: claims queued jobs, does the slow work, posts
results back to Slack. See ARCHITECTURE.md's system shape — this is what
"ack-and-enqueue" hands off to.
"""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from datetime import datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.loop import run_agent_turn
from app.agent.tools import AgentContext
from app.calendar.google_calendar import GoogleCalendarProvider
from app.core.config import settings
from app.core.db import async_session_factory
from app.core.security import TokenCipher, token_cipher_from_settings
from app.core.slack_client import build_client_for_workspace
from app.models.inbound_job import InboundJob
from app.repositories.inbound_job_repository import InboundJobRepository
from app.repositories.workspace_repository import WorkspaceRepository
from app.services import meeting_service

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 2

# Strips a leading Slack mention token (`<@U012ABC>` or `<@U012ABC|name>`) —
# Slack always puts the mentioned bot's own ID at the front of an
# app_mention's text, which isn't part of what the user actually said.
_LEADING_MENTION_RE = re.compile(r"^<@[A-Z0-9]+(?:\|[^>]*)?>\s*")


def _strip_bot_mention(text: str) -> str:
    return _LEADING_MENTION_RE.sub("", text).strip()


async def handle_app_mention(
    session: AsyncSession, job: InboundJob, cipher: TokenCipher
) -> None:
    workspace = await WorkspaceRepository(session).get(job.team_id)
    if workspace is None or workspace.uninstalled_at is not None:
        return

    channel = job.payload["channel"]
    client = build_client_for_workspace(workspace, cipher)

    if not settings.groq_api_key:
        # Fail visibly to the user rather than pretending the request was
        # understood — see ENGINEERING.md's "fail loud, not plausible" rule.
        await client.chat_postMessage(
            channel=channel, text="Natural-language requests aren't set up on this workspace yet."
        )
        return

    user_message = _strip_bot_mention(job.payload.get("text", ""))
    slack_user_id = job.payload.get("user", "")

    async with AsyncExitStack() as stack:
        calendar_provider = None
        if settings.google_client_id and settings.google_client_secret:
            calendar_http_client = await stack.enter_async_context(httpx.AsyncClient(timeout=10.0))
            calendar_provider = GoogleCalendarProvider(
                calendar_http_client,
                client_id=settings.google_client_id,
                client_secret=settings.google_client_secret,
            )

        ctx = AgentContext(
            session=session,
            team_id=job.team_id,
            slack_user_id=slack_user_id,
            calendar_provider=calendar_provider,
            cipher=cipher,
        )

        groq_client = await stack.enter_async_context(httpx.AsyncClient(timeout=20.0))
        reply = await run_agent_turn(
            ctx, groq_client, settings.groq_api_key, user_message=user_message
        )

    await client.chat_postMessage(channel=channel, text=reply or "Done.")


async def handle_meet_propose(
    session: AsyncSession, job: InboundJob, cipher: TokenCipher
) -> None:
    """Finding a mutual slot makes one real Google API call per participant
    — too slow for Slack's 3-second ack budget, which is why /meet enqueues
    this instead of doing it inline. Replies via Slack's `response_url`,
    valid for 30 minutes after the original command. See ARCHITECTURE.md's
    3-second rule."""
    payload = job.payload
    response_url = payload.get("response_url", "")

    async def _respond(text: str) -> None:
        if not response_url:
            logger.warning("meet_propose job %s has no response_url to reply to", job.id)
            return
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(response_url, json={"response_type": "ephemeral", "text": text})

    if not (settings.google_client_id and settings.google_client_secret):
        await _respond("Calendar scheduling isn't configured on this workspace yet.")
        return

    window_start = datetime.fromisoformat(payload["window_start_utc"])
    window_end = datetime.fromisoformat(payload["window_end_utc"])
    duration_minutes = payload["duration_minutes"]

    async with httpx.AsyncClient(timeout=10.0) as http_client:
        provider = GoogleCalendarProvider(
            http_client,
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
        )
        result = await meeting_service.propose_meeting(
            session,
            provider,
            cipher,
            team_id=job.team_id,
            organiser_slack_id=payload["organiser_id"],
            participant_slack_ids=payload["participant_slack_ids"],
            duration=timedelta(minutes=duration_minutes),
            search_window_start_utc=window_start,
            search_window_end_utc=window_end,
        )

    # Commit before notifying — the user must never be told about a meeting
    # id that isn't durably persisted yet (they could click "book" on it
    # within seconds).
    await session.commit()

    if result.meeting is None:
        await _respond("No mutual free slot exists in that window for the linked calendars.")
        return

    note = ""
    if result.unavailable_participants:
        names = ", ".join(f"<@{p}>" for p in result.unavailable_participants)
        note = f"\n(Couldn't check {names} — not linked yet: `/link-calendar`)"

    await _respond(
        f"Earliest mutual slot: {result.slot_start_utc.isoformat()} UTC, "
        f"{duration_minutes} min. Run `/meet book {result.meeting.id}` to book it.{note}"
    )


HANDLERS: dict[str, Callable[[AsyncSession, InboundJob, TokenCipher], Awaitable[None]]] = {
    "app_mention": handle_app_mention,
    "meet_propose": handle_meet_propose,
}


async def process_one_batch(session: AsyncSession, cipher: TokenCipher) -> int:
    repo = InboundJobRepository(session)
    jobs = await repo.claim_batch()
    for job in jobs:
        handler = HANDLERS.get(job.event_type)
        try:
            if handler is not None:
                await handler(session, job, cipher)
            await repo.mark_done(job.id)
        except Exception as exc:  # noqa: BLE001 — one bad job must not kill the worker loop
            logger.exception("job %s failed", job.id)
            await repo.mark_failed(job.id, str(exc))
        await session.commit()
    return len(jobs)


async def run_worker_loop() -> None:
    cipher = token_cipher_from_settings(settings)
    while True:
        async with async_session_factory() as session:
            await process_one_batch(session, cipher)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
