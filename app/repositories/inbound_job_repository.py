"""The ack-and-enqueue job queue's repository.

Deliberately *not* a `TenantScopedRepository` — the worker is one process
serving every tenant, so claiming work is cross-tenant by design (each job
still carries its own `team_id`, read after claiming to know which workspace's
bot token to use). Same FOR UPDATE SKIP LOCKED pattern as `reminders` — see
ARCHITECTURE.md's job queue section.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inbound_job import STATUS_DONE, STATUS_FAILED, STATUS_PENDING, InboundJob


class InboundJobRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def enqueue(self, team_id: str, event_type: str, payload: dict) -> InboundJob:
        job = InboundJob(
            team_id=team_id,
            event_type=event_type,
            payload=payload,
            status=STATUS_PENDING,
            created_at=datetime.now(UTC),
        )
        self._session.add(job)
        await self._session.flush()
        return job

    async def claim_batch(self, limit: int = 10) -> list[InboundJob]:
        stmt = (
            select(InboundJob)
            .where(InboundJob.status == STATUS_PENDING)
            .order_by(InboundJob.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self._session.execute(stmt)
        jobs = list(result.scalars().all())
        now = datetime.now(UTC)
        for job in jobs:
            job.claimed_at = now
        await self._session.flush()
        return jobs

    async def mark_done(self, job_id: uuid.UUID) -> None:
        job = await self._session.get(InboundJob, job_id)
        if job is not None:
            job.status = STATUS_DONE
            job.completed_at = datetime.now(UTC)
            await self._session.flush()

    async def mark_failed(self, job_id: uuid.UUID, error: str) -> None:
        job = await self._session.get(InboundJob, job_id)
        if job is not None:
            job.status = STATUS_FAILED
            job.completed_at = datetime.now(UTC)
            job.error = error
            await self._session.flush()
