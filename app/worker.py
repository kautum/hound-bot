"""The in-process worker: claims queued jobs, does the slow work, posts
results back to Slack. See ARCHITECTURE.md's system shape — this is what
"ack-and-enqueue" hands off to.
"""

import asyncio
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta

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
from app.services.task_service import list_open_tasks
from app.ui.blocks import build_app_home_view, build_meeting_action_blocks

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 2
# /health considers the worker unhealthy if it hasn't completed a poll cycle
# in this long — several multiples of POLL_INTERVAL_SECONDS so a single slow
# batch doesn't flap the health check, but well below "actually stuck".
STALE_AFTER_SECONDS = 30

# Timestamp of the worker loop's last completed iteration (successful or
# not — what matters for liveness is that the loop is still turning, not
# that every batch succeeded). None until the loop has run at least once.
_last_poll_completed_utc: datetime | None = None


def worker_is_alive() -> bool:
    """S7: /health used to return a static 200 forever, even after the
    worker task had silently died — a health check that can't go unhealthy
    is decoration. True only if the loop has completed a poll recently."""
    if _last_poll_completed_utc is None:
        return False
    age = (datetime.now(UTC) - _last_poll_completed_utc).total_seconds()
    return age < STALE_AFTER_SECONDS

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

    async def _respond(text: str, blocks: list[dict] | None = None) -> None:
        if not response_url:
            logger.warning("meet_propose job %s has no response_url to reply to", job.id)
            return
        payload = {"response_type": "ephemeral", "text": text}
        if blocks:
            payload["blocks"] = blocks
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(response_url, json=payload)

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

    blocks = build_meeting_action_blocks(
        meeting_id=str(result.meeting.id), action_id="meet_book", button_text="Book"
    )
    await _respond(
        f"Earliest mutual slot: {result.slot_start_utc.isoformat()} UTC, "
        f"{duration_minutes} min.{note}",
        blocks=blocks,
    )


async def handle_meet_book(session: AsyncSession, job: InboundJob, cipher: TokenCipher) -> None:
    """Booking makes two sequential Google calls (refresh, then
    events.insert) — the same reason meet_propose is enqueued rather than
    run inline. This used to run inside the slash-command handler itself
    (S5), the exact bug meet_propose was already fixed for."""
    payload = job.payload
    response_url = payload.get("response_url", "")

    async def _respond(text: str, blocks: list[dict] | None = None) -> None:
        if not response_url:
            logger.warning("meet_book job %s has no response_url to reply to", job.id)
            return
        payload = {"response_type": "ephemeral", "text": text}
        if blocks:
            payload["blocks"] = blocks
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(response_url, json=payload)

    if not (settings.google_client_id and settings.google_client_secret):
        await _respond("Calendar scheduling isn't configured on this workspace yet.")
        return

    async with httpx.AsyncClient(timeout=10.0) as http_client:
        provider = GoogleCalendarProvider(
            http_client,
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
        )
        try:
            meeting = await meeting_service.confirm_meeting(
                session,
                provider,
                cipher,
                team_id=job.team_id,
                meeting_id=uuid.UUID(payload["meeting_id"]),
                requesting_slack_user_id=payload["requesting_user_id"],
                title="Meeting",
            )
        except meeting_service.MeetingConfirmationError as exc:
            await _respond(str(exc))
            return

    await session.commit()
    blocks = build_meeting_action_blocks(
        meeting_id=str(meeting.id), action_id="meet_cancel", button_text="Cancel"
    )
    await _respond(f"Booked for {meeting.proposed_start_utc.isoformat()} UTC.", blocks=blocks)


async def handle_meet_cancel(session: AsyncSession, job: InboundJob, cipher: TokenCipher) -> None:
    """Cancelling a booked meeting makes a Google events.delete call —
    enqueued for the same 3-second budget reason as book/propose."""
    payload = job.payload
    response_url = payload.get("response_url", "")

    async def _respond(text: str) -> None:
        if not response_url:
            logger.warning("meet_cancel job %s has no response_url to reply to", job.id)
            return
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(response_url, json={"response_type": "ephemeral", "text": text})

    if not (settings.google_client_id and settings.google_client_secret):
        await _respond("Calendar scheduling isn't configured on this workspace yet.")
        return

    async with httpx.AsyncClient(timeout=10.0) as http_client:
        provider = GoogleCalendarProvider(
            http_client,
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
        )
        try:
            await meeting_service.cancel_meeting(
                session,
                provider,
                cipher,
                team_id=job.team_id,
                meeting_id=uuid.UUID(payload["meeting_id"]),
                requesting_slack_user_id=payload["requesting_user_id"],
            )
        except meeting_service.MeetingConfirmationError as exc:
            await _respond(str(exc))
            return

    await session.commit()
    await _respond("Cancelled that meeting.")


async def handle_app_uninstalled(
    session: AsyncSession, job: InboundJob, cipher: TokenCipher
) -> None:
    """Handles app_uninstalled events by marking the workspace as uninstalled.
    This is a workspace-level event (not per-user tokens_revoked)."""
    await WorkspaceRepository(session).mark_uninstalled(job.team_id)


async def handle_app_home_opened(
    session: AsyncSession, job: InboundJob, cipher: TokenCipher
) -> None:
    """Handles app_home_opened events by publishing the user's open tasks
    to the App Home tab. Refreshed every time they open the tab."""
    workspace = await WorkspaceRepository(session).get(job.team_id)
    if workspace is None or workspace.uninstalled_at is not None:
        return

    client = build_client_for_workspace(workspace, cipher)
    user_id = job.payload["user"]

    tasks = await list_open_tasks(
        session, team_id=job.team_id, assignee_slack_id=user_id
    )
    view = build_app_home_view(tasks)

    await client.views_publish(user_id=user_id, view=view)


HANDLERS: dict[str, Callable[[AsyncSession, InboundJob, TokenCipher], Awaitable[None]]] = {
    "app_mention": handle_app_mention,
    "meet_propose": handle_meet_propose,
    "meet_book": handle_meet_book,
    "meet_cancel": handle_meet_cancel,
    "app_uninstalled": handle_app_uninstalled,
    "app_home_opened": handle_app_home_opened,
}


async def process_one_batch(session: AsyncSession, cipher: TokenCipher) -> int:
    repo = InboundJobRepository(session)
    jobs = await repo.claim_batch()
    for job in jobs:
        handler = HANDLERS.get(job.event_type)
        try:
            if handler is not None:
                await handler(session, job, cipher)
            else:
                error_msg = f"no handler registered for event_type {job.event_type!r}"
                logger.error("job %s failed: %s", job.id, error_msg)
                await repo.mark_failed(job.id, error_msg)
                await session.commit()
                continue
            await repo.mark_done(job.id)
        except Exception as exc:  # noqa: BLE001 — one bad job must not kill the worker loop
            logger.exception("job %s failed", job.id)
            await repo.mark_failed(job.id, str(exc))
        await session.commit()
    return len(jobs)


async def run_worker_loop() -> None:
    """S7: this used to have no supervision at all — one DB blip raised out
    of the while loop, silently ending the worker for the life of the
    process, with /health none the wiser. Now a single iteration's failure
    is logged and the loop continues; only Cancelled/exit propagate."""
    global _last_poll_completed_utc
    cipher = token_cipher_from_settings(settings)
    while True:
        try:
            async with async_session_factory() as session:
                await process_one_batch(session, cipher)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("worker poll iteration failed — continuing")
        _last_poll_completed_utc = datetime.now(UTC)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
