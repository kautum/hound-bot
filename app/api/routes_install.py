"""The Slack per-workspace install flow. Thin routes — the logic lives in
app/services/slack_oauth.py and the repositories. See ARCHITECTURE.md.
"""

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.core.security import token_cipher_from_settings
from app.repositories.oauth_state_repository import OAuthStateRepository
from app.repositories.workspace_repository import WorkspaceRepository
from app.services.slack_oauth import SlackOAuthError, build_install_url, exchange_code_for_token

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
) -> dict:
    row = await OAuthStateRepository(session).consume(
        state, expected_purpose=PURPOSE_SLACK_INSTALL
    )
    if row is None:
        raise HTTPException(status_code=400, detail="invalid or expired state")

    if not settings.slack_client_id or not settings.slack_client_secret:
        raise HTTPException(status_code=500, detail="Slack OAuth is not configured")

    async with httpx.AsyncClient(timeout=10.0) as http_client:
        try:
            bot_token, team_id = await exchange_code_for_token(
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

    return {"status": "installed", "team_id": team_id}
