"""The in-process worker: claims queued jobs, does the slow work, posts
results back to Slack. See ARCHITECTURE.md's system shape — this is what
"ack-and-enqueue" hands off to.
"""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable

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

    calendar_provider = None
    if settings.google_client_id and settings.google_client_secret:
        # One short-lived httpx client per turn — cheap, and avoids holding a
        # long-lived client across the worker's indefinite poll loop.
        calendar_provider = GoogleCalendarProvider(
            httpx.AsyncClient(timeout=10.0),
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

    async with httpx.AsyncClient(timeout=20.0) as groq_client:
        reply = await run_agent_turn(
            ctx, groq_client, settings.groq_api_key, user_message=user_message
        )

    await client.chat_postMessage(channel=channel, text=reply or "Done.")


HANDLERS: dict[str, Callable[[AsyncSession, InboundJob, TokenCipher], Awaitable[None]]] = {
    "app_mention": handle_app_mention,
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
