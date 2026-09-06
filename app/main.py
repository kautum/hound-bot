"""FastAPI entrypoint. Wires routes, /health, and the in-process worker
background task. See ARCHITECTURE.md's system shape.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes_events import router as events_router
from app.api.routes_install import router as install_router
from app.core.config import settings
from app.worker import run_worker_loop

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    worker_task = None
    if settings.encryption_key:
        worker_task = asyncio.create_task(run_worker_loop())
    else:
        logger.warning("ENCRYPTION_KEY not configured — in-process worker not started")
    try:
        yield
    finally:
        if worker_task is not None:
            worker_task.cancel()


app = FastAPI(lifespan=lifespan)
app.include_router(install_router)
app.include_router(events_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
