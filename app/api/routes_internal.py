"""The cron-job.org tick endpoint. Public by necessity, so it's protected by
a shared secret rather than a Slack signature — see ARCHITECTURE.md's key
management section on CRON_SHARED_SECRET.
"""

import hmac

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.core.security import token_cipher_from_settings
from app.scheduler import process_due_reminders

router = APIRouter()


@router.post("/internal/tick")
async def internal_tick(
    session: AsyncSession = Depends(get_session),
    x_cron_secret: str = Header(default=""),
) -> dict:
    if not settings.cron_shared_secret:
        raise HTTPException(status_code=500, detail="CRON_SHARED_SECRET is not configured")
    if not hmac.compare_digest(x_cron_secret, settings.cron_shared_secret):
        raise HTTPException(status_code=401, detail="invalid cron secret")

    cipher = token_cipher_from_settings(settings)
    sent = await process_due_reminders(session, cipher)
    return {"reminders_sent": sent}
