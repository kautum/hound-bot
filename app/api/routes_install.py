"""The Slack per-workspace install flow. Thin routes — the logic lives in
app/services/slack_oauth.py and the repositories. See ARCHITECTURE.md.
"""

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.core.security import token_cipher_from_settings
from app.repositories.oauth_state_repository import OAuthStateRepository
from app.repositories.workspace_repository import WorkspaceRepository
from app.services.slack_oauth import SlackOAuthError, build_install_url, exchange_code_for_token

logger = logging.getLogger(__name__)

router = APIRouter()

PURPOSE_SLACK_INSTALL = "slack_install"


def _redirect_uri() -> str:
    if not settings.public_base_url:
        raise HTTPException(status_code=500, detail="PUBLIC_BASE_URL is not configured")
    return f"{settings.public_base_url.rstrip('/')}/slack/oauth/callback"


@router.get("/slack/install")
async def install(session: AsyncSession = Depends(get_session)) -> RedirectResponse:
    if not settings.slack_client_id:
        raise HTTPException(status_code=500, detail="SLACK_CLIENT_ID is not configured")

    state = await OAuthStateRepository(session).issue(PURPOSE_SLACK_INSTALL)
    await session.commit()

    url = build_install_url(
        client_id=settings.slack_client_id,
        redirect_uri=_redirect_uri(),
        state=state,
    )
    return RedirectResponse(url)


@router.get("/slack/oauth/callback")
async def oauth_callback(
    code: str, state: str, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    row = await OAuthStateRepository(session).consume(
        state, expected_purpose=PURPOSE_SLACK_INSTALL
    )
    if row is None:
        raise HTTPException(status_code=400, detail="invalid or expired state")

    if not settings.slack_client_id or not settings.slack_client_secret:
        raise HTTPException(status_code=500, detail="Slack OAuth is not configured")

    async with httpx.AsyncClient(timeout=10.0) as http_client:
        try:
            bot_token, team_id, authed_user_id = await exchange_code_for_token(
                http_client,
                client_id=settings.slack_client_id,
                client_secret=settings.slack_client_secret,
                code=code,
                redirect_uri=_redirect_uri(),
            )
        except SlackOAuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    cipher = token_cipher_from_settings(settings)
    bot_token_enc, key_version = cipher.encrypt(bot_token)

    await WorkspaceRepository(session).upsert_on_install(team_id, bot_token_enc, key_version)
    await session.commit()

    # Best-effort welcome DM — failure must not block the install
    if authed_user_id:
        try:
            await _send_welcome_dm(bot_token, authed_user_id)
        except Exception as exc:
            logger.warning(
                "Failed to send welcome DM to user %s after install: %s",
                authed_user_id,
                exc,
                exc_info=True,
            )

    return HTMLResponse(_success_html())


async def _send_welcome_dm(bot_token: str, user_id: str) -> None:
    """Send a welcome DM to the installing user using the bot token.
    Uses conversations.open to get the DM channel, then chat.postMessage.
    Best-effort only — errors are logged but never propagate."""
    SLACK_API_BASE = "https://slack.com/api"

    async with httpx.AsyncClient(timeout=10.0) as http_client:
        headers = {"Authorization": f"Bearer {bot_token}", "Content-Type": "application/json"}

        # Open a DM channel with the user
        open_response = await http_client.post(
            f"{SLACK_API_BASE}/conversations.open",
            headers=headers,
            json={"users": user_id},
        )
        open_response.raise_for_status()
        open_body = open_response.json()

        if not open_body.get("ok"):
            logger.warning(
                "conversations.open failed for user %s: %s",
                user_id,
                open_body.get("error", "unknown"),
            )
            return

        channel_id = open_body.get("channel", {}).get("id")
        if not channel_id:
            logger.warning("conversations.open response missing channel.id for user %s", user_id)
            return

        # Send the welcome message
        message = (
            "🎉 Welcome to Hound! I'm installed and ready to help.\n\n"
            "Here's what I can do:\n"
            "• `/task add <title>` — Add a task\n"
            "• `/task list` — List your tasks\n"
            "• `/meet` — Schedule a meeting\n"
            "• `@Hound <anything>` — Ask me anything in natural language\n\n"
            "Let's get started!"
        )
        post_response = await http_client.post(
            f"{SLACK_API_BASE}/chat.postMessage",
            headers=headers,
            json={"channel": channel_id, "text": message},
        )
        post_response.raise_for_status()
        post_body = post_response.json()

        if not post_body.get("ok"):
            logger.warning(
                "chat.postMessage failed for channel %s: %s",
                channel_id,
                post_body.get("error", "unknown"),
            )


def _success_html() -> str:
    """Return a self-contained HTML success page with inline CSS."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Hound Installed</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
                Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0;
            padding: 20px;
        }
        .container {
            background: white;
            border-radius: 12px;
            padding: 40px;
            max-width: 500px;
            box-shadow: 0 10px 40px rgba(0, 0, 0, 0.2);
        }
        h1 {
            color: #333;
            margin: 0 0 20px 0;
            font-size: 28px;
        }
        .success-icon {
            color: #10b981;
            font-size: 48px;
            margin-bottom: 20px;
        }
        .description {
            color: #666;
            line-height: 1.6;
            margin-bottom: 30px;
        }
        .commands {
            background: #f8fafc;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 20px;
        }
        .commands h2 {
            color: #333;
            margin: 0 0 15px 0;
            font-size: 18px;
        }
        .command {
            color: #4a5568;
            margin: 8px 0;
            font-family: "Monaco", "Menlo", monospace;
            font-size: 14px;
        }
        .footer {
            color: #999;
            font-size: 12px;
            text-align: center;
            margin-top: 20px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="success-icon">✓</div>
        <h1>Hound is installed</h1>
        <p class="description">
            Hound is now ready to help your workspace manage tasks and schedule meetings.
        </p>
        <div class="commands">
            <h2>Available commands</h2>
            <div class="command">/task add &lt;title&gt;</div>
            <div class="command">/task list</div>
            <div class="command">/meet</div>
            <div class="command">@Hound &lt;anything&gt;</div>
        </div>
        <div class="footer">
            Check your Slack DMs for a welcome message with more details.
        </div>
    </div>
</body>
</html>"""
