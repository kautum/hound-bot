"""The in-process worker: claims queued jobs, does the slow work, posts
results back to Slack. See ARCHITECTURE.md's system shape — this is what
"ack-and-enqueue" hands off to.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import async_session_factory
from app.core.security import TokenCipher, token_cipher_from_settings
from app.core.slack_client import build_client_for_workspace
from app.models.inbound_job import InboundJob
from app.repositories.inbound_job_repository import InboundJobRepository
from app.repositories.workspace_repository import WorkspaceRepository

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 2


async def handle_app_mention(
    session: AsyncSession, job: InboundJob, cipher: TokenCipher
) -> None:
    workspace = await WorkspaceRepository(session).get(job.team_id)
    if workspace is None or workspace.uninstalled_at is not None:
        return

    client = build_client_for_workspace(workspace, cipher)
    # Phase 5 replaces this with the LLM agent loop; for now, a real reply
    # proves the end-to-end path (mention -> queue -> worker -> Slack) works.
    await client.chat_postMessage(
        channel=job.payload["channel"], text="Got it — working on that."
    )


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
