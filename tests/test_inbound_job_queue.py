import asyncio
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Workspace
from app.repositories.inbound_job_repository import InboundJobRepository


async def _make_workspace(session, team_id: str) -> None:
    session.add(
        Workspace(
            team_id=team_id,
            bot_token_enc=b"irrelevant",
            key_version=1,
            installed_at=datetime.now(UTC),
        )
    )
    await session.flush()


class TestInboundJobQueue:
    async def test_enqueue_and_claim(self, db_session):
        await _make_workspace(db_session, "team-A")
        repo = InboundJobRepository(db_session)

        job = await repo.enqueue("team-A", "app_mention", {"channel": "C1", "text": "hi"})
        await db_session.commit()

        claimed = await repo.claim_batch()
        assert [j.id for j in claimed] == [job.id]
        assert claimed[0].claimed_at is not None

    async def test_two_concurrent_workers_never_claim_the_same_job(self, db_session, db_engine):
        """The exact property FOR UPDATE SKIP LOCKED exists for — see
        ARCHITECTURE.md's job queue section. Uses two real, separate
        connections/transactions to actually exercise Postgres locking,
        not just two calls on one session (which wouldn't block itself)."""
        await _make_workspace(db_session, "team-A")
        for i in range(4):
            await InboundJobRepository(db_session).enqueue(
                "team-A", "app_mention", {"channel": "C1", "n": i}
            )
        await db_session.commit()

        session_factory = async_sessionmaker(db_engine, expire_on_commit=False)

        async def claim_and_hold(barrier: asyncio.Barrier) -> list:
            async with session_factory() as session:
                async with session.begin():
                    jobs = await InboundJobRepository(session).claim_batch(limit=2)
                    await barrier.wait()  # ensure both transactions are open at once
                return [j.id for j in jobs]

        barrier = asyncio.Barrier(2)
        results = await asyncio.gather(
            claim_and_hold(barrier),
            claim_and_hold(barrier),
        )

        first_ids, second_ids = results
        assert set(first_ids).isdisjoint(second_ids)
        assert len(first_ids) + len(second_ids) == 4

    async def test_claimed_job_is_not_reclaimed_after_a_commit_mid_batch(
        self, db_session, db_engine
    ):
        """S8: claim_batch used to leave status as STATUS_PENDING, so a
        per-job commit inside the batch (ending the transaction and
        releasing FOR UPDATE SKIP LOCKED on the rest of it) let a second
        worker re-claim jobs that were claimed but not yet processed. This
        reproduces exactly that shape: claim, commit (simulating the
        worker's per-job commit), then a second worker's claim_batch must
        not see the same rows."""
        await _make_workspace(db_session, "team-A")
        repo = InboundJobRepository(db_session)
        job_ids = []
        for i in range(3):
            job = await repo.enqueue("team-A", "app_mention", {"channel": "C1", "n": i})
            job_ids.append(job.id)
        await db_session.commit()

        session_factory = async_sessionmaker(db_engine, expire_on_commit=False)

        async with session_factory() as worker_a_session:
            claimed_by_a = await InboundJobRepository(worker_a_session).claim_batch(limit=3)
            await worker_a_session.commit()  # the exact moment S8's lock was released

        async with session_factory() as worker_b_session:
            claimed_by_b = await InboundJobRepository(worker_b_session).claim_batch(limit=3)

        assert {j.id for j in claimed_by_a} == set(job_ids)
        assert claimed_by_b == []  # already claimed — must not be re-claimed

    async def test_mark_done_and_mark_failed(self, db_session):
        await _make_workspace(db_session, "team-A")
        repo = InboundJobRepository(db_session)
        job_ok = await repo.enqueue("team-A", "app_mention", {"channel": "C1"})
        job_bad = await repo.enqueue("team-A", "app_mention", {"channel": "C1"})
        await db_session.commit()

        await repo.mark_done(job_ok.id)
        await repo.mark_failed(job_bad.id, "boom")
        await db_session.commit()

        assert (await repo.claim_batch()) == []
