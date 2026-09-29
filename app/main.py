"""FastAPI entrypoint. Wires routes, /health, and the in-process worker
background task. See ARCHITECTURE.md's system shape.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api.routes_commands import router as commands_router
from app.api.routes_events import router as events_router
from app.api.routes_google import router as google_router
from app.api.routes_install import router as install_router
from app.api.routes_interactions import router as interactions_router
from app.api.routes_internal import router as internal_router
from app.core.config import settings
from app.worker import run_worker_loop, worker_is_alive

logger = logging.getLogger(__name__)


def _log_worker_exit(task: asyncio.Task) -> None:
    """S7: the worker task previously had no supervision at all — if it
    ever exited, nothing was logged and /health kept returning 200 forever.
    run_worker_loop itself now swallows per-iteration errors, so reaching
    this callback at all means the loop exited entirely, which should never
    happen outside of cancellation — log loudly rather than silently."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.critical("worker task exited unexpectedly", exc_info=exc)
    else:
        logger.critical("worker task exited unexpectedly with no exception")


@asynccontextmanager
async def lifespan(app: FastAPI):
    worker_task = None
    if settings.encryption_key:
        worker_task = asyncio.create_task(run_worker_loop())
        worker_task.add_done_callback(_log_worker_exit)
    else:
        logger.warning("ENCRYPTION_KEY not configured — in-process worker not started")
    try:
        yield
    finally:
        if worker_task is not None:
            worker_task.remove_done_callback(_log_worker_exit)
            worker_task.cancel()


app = FastAPI(lifespan=lifespan)
app.include_router(install_router)
app.include_router(events_router)
app.include_router(commands_router)
app.include_router(interactions_router)
app.include_router(internal_router)
app.include_router(google_router)


@app.get("/health")
async def health() -> JSONResponse:
    """S7: used to be a static 200 forever — a health check that can never
    go unhealthy is decoration, not monitoring. Reports actual worker
    liveness (whether the poll loop has completed an iteration recently)
    and returns 503 when it hasn't, so uptime monitoring actually notices."""
    if settings.encryption_key and not worker_is_alive():
        return JSONResponse(
            status_code=503, content={"status": "degraded", "worker": "not polling"}
        )
    return JSONResponse(
        content={"status": "ok", "worker": "alive" if settings.encryption_key else "disabled"}
    )
