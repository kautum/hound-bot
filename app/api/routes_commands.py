"""Slash commands. All fast, deterministic paths — no LLM, no ack-and-enqueue
needed, since these operations are quick enough to answer inline within
Slack's 3-second budget. See ARCHITECTURE.md's tools-first section. Slack
lets multiple slash commands share one request URL, dispatched by the
`command` field — that's what this router does first.
"""

import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.calendar.google_calendar import GoogleCalendarProvider
from app.core.config import settings
from app.core.db import get_session
from app.core.security import token_cipher_from_settings, verify_slack_signature
from app.repositories.inbound_job_repository import InboundJobRepository
from app.api.routes_google import PURPOSE_GOOGLE_LINK
from app.repositories.oauth_state_repository import OAuthStateRepository
from app.services import meeting_service, task_service
from app.services.meet_command_parser import MeetCommandError, parse_meet_command
from app.services.task_command_parser import TaskCommandError, parse_task_add

router = APIRouter()


def _ephemeral(text: str) -> dict:
    return {"response_type": "ephemeral", "text": text}


async def _handle_task_command(session: AsyncSession, form) -> dict:
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


def _link_calendar_url(state: str) -> str:
    if not settings.public_base_url:
        raise HTTPException(status_code=500, detail="PUBLIC_BASE_URL is not configured")
    base = settings.public_base_url.rstrip("/")
    return f"{base}/google/link?state={state}"


async def _handle_link_calendar_command(session: AsyncSession, form) -> dict:
    # Slack's signature has already authenticated this request (verified in
    # slack_commands below) — this is the ONLY point in the Google-link flow
    # where the caller's identity is trustworthy, so the oauth_states row is
    # issued right here, server-side. /google/link never sees team_id or
    # slack_user_id at all: it only ever sees the opaque state token. See
    # PROJECT-WIKI.md / the security audit for why the old shape (identity
    # passed as a query param) let anyone link their own Google account to
    # someone else's Slack identity.
    team_id = str(form.get("team_id", ""))
    user_id = str(form.get("user_id", ""))
    state = await OAuthStateRepository(session).issue(
        PURPOSE_GOOGLE_LINK, team_id=team_id, slack_user_id=user_id
    )
    await session.commit()
    url = _link_calendar_url(state)
    return _ephemeral(f"Link your Google Calendar: {url}")


async def _handle_meet_book(
    session: AsyncSession, team_id: str, requesting_user_id: str, raw_id: str
) -> dict:
    try:
        meeting_id = uuid.UUID(raw_id)
    except ValueError:
        return _ephemeral(f"{raw_id!r} isn't a valid meeting ID.")

    calendar_configured = bool(
        settings.google_client_id and settings.google_client_secret and settings.encryption_key
    )
    if not calendar_configured:
        return _ephemeral("Calendar scheduling isn't configured on this workspace yet.")

    cipher = token_cipher_from_settings(settings)
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
                team_id=team_id,
                meeting_id=meeting_id,
                requesting_slack_user_id=requesting_user_id,
                title="Meeting",
            )
        except meeting_service.MeetingConfirmationError as exc:
            return _ephemeral(str(exc))
    await session.commit()

    return _ephemeral(f"Booked for {meeting.proposed_start_utc.isoformat()} UTC.")


async def _handle_meet_command(session: AsyncSession, form) -> dict:
    text = str(form.get("text", "")).strip()
    team_id = str(form.get("team_id", ""))
    organiser_id = str(form.get("user_id", ""))
    response_url = str(form.get("response_url", ""))

    if text.startswith("book "):
        return await _handle_meet_book(
            session, team_id, organiser_id, text.removeprefix("book ").strip()
        )

    try:
        participants, duration_minutes, window_start, window_end = parse_meet_command(text)
    except MeetCommandError as exc:
        return _ephemeral(str(exc))

    calendar_configured = bool(
        settings.google_client_id and settings.google_client_secret and settings.encryption_key
    )
    if not calendar_configured:
        return _ephemeral("Calendar scheduling isn't configured on this workspace yet.")

    # Finding a slot makes one real Google API call per participant — far
    # too slow to fit inside Slack's 3-second ack budget with more than a
    # couple of people. Enqueue it and reply via `response_url` once it's
    # done, the same ack-and-enqueue shape /slack/events uses. See
    # ARCHITECTURE.md's 3-second rule — this used to run inline here, which
    # was a real bug, not a stylistic choice.
    await InboundJobRepository(session).enqueue(
        team_id=team_id,
        event_type="meet_propose",
        payload={
            "organiser_id": organiser_id,
            "participant_slack_ids": participants,
            "duration_minutes": duration_minutes,
            "window_start_utc": window_start.isoformat(),
            "window_end_utc": window_end.isoformat(),
            "response_url": response_url,
        },
    )
    await session.commit()

    return _ephemeral("Looking for a mutual free slot — I'll follow up here shortly.")


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
    command = str(form.get("command", ""))

    if command == "/task":
        return await _handle_task_command(session, form)
    if command == "/link-calendar":
        return await _handle_link_calendar_command(session, form)
    if command == "/meet":
        return await _handle_meet_command(session, form)

    return _ephemeral(f"Unknown command {command!r}.")
