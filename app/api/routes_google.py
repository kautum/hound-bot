"""The Google account-linking flow — the second of ARCHITECTURE.md's two
OAuth flows. Same state-CSRF shape as the Slack install flow, but scoped to
one person's calendar rather than a whole workspace's bot token.
"""

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.calendar.google_calendar import (
    GoogleOAuthError,
    build_authorize_url,
    exchange_code_for_tokens,
    fetch_user_email,
)
from app.core.config import settings
from app.core.db import get_session
from app.core.security import token_cipher_from_settings
from app.repositories.oauth_state_repository import OAuthStateRepository
from app.repositories.user_repository import UserRepository

router = APIRouter()

PURPOSE_GOOGLE_LINK = "google_link"


def _redirect_uri() -> str:
    if not settings.public_base_url:
        raise HTTPException(status_code=500, detail="PUBLIC_BASE_URL is not configured")
    return f"{settings.public_base_url.rstrip('/')}/google/oauth/callback"


@router.get("/google/link")
async def link(state: str, session: AsyncSession = Depends(get_session)) -> RedirectResponse:
    """Takes only an opaque state token — never an identity. The token was
    issued by /link-calendar, at the one point in this flow where Slack's
    signature has already authenticated the caller. This endpoint just checks
    the token is real and unexpired before handing it to Google; the actual
    team_id/slack_user_id binding is only ever read back at the callback,
    from the row this endpoint did not create and cannot forge."""
    if not settings.google_client_id:
        raise HTTPException(status_code=500, detail="GOOGLE_CLIENT_ID is not configured")

    valid = await OAuthStateRepository(session).peek_valid(
        state, expected_purpose=PURPOSE_GOOGLE_LINK
    )
    if not valid:
        raise HTTPException(status_code=400, detail="invalid or expired state")

    url = build_authorize_url(
        client_id=settings.google_client_id, redirect_uri=_redirect_uri(), state=state
    )
    return RedirectResponse(url)


@router.get("/google/oauth/callback")
async def oauth_callback(
    code: str, state: str, session: AsyncSession = Depends(get_session)
) -> dict:
    row = await OAuthStateRepository(session).consume(state, expected_purpose=PURPOSE_GOOGLE_LINK)
    if row is None or row.team_id is None or row.slack_user_id is None:
        raise HTTPException(status_code=400, detail="invalid or expired state")

    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(status_code=500, detail="Google OAuth is not configured")

    async with httpx.AsyncClient(timeout=10.0) as http_client:
        try:
            refresh_token, access_token = await exchange_code_for_tokens(
                http_client,
                client_id=settings.google_client_id,
                client_secret=settings.google_client_secret,
                code=code,
                redirect_uri=_redirect_uri(),
            )
            email = await fetch_user_email(http_client, access_token)
        except GoogleOAuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    cipher = token_cipher_from_settings(settings)
    refresh_token_enc, key_version = cipher.encrypt(refresh_token)

    users = UserRepository(session)
    # Default tz until Phase 5/UI captures the real one via Slack's
    # users.info — a documented simplification, not a silent guess about
    # anything security- or correctness-sensitive.
    await users.get_or_create(row.slack_user_id, row.team_id, tz="UTC")
    await users.store_google_link(
        row.slack_user_id,
        refresh_token_enc=refresh_token_enc,
        key_version=key_version,
        google_email=email,
    )
    await session.commit()

    return {"status": "linked", "email": email}
