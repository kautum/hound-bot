"""oauth_states housekeeping: expired rows are purged by the tick, and
`consume` is atomic so two concurrent callers can't both win."""

import asyncio

from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.security import TokenCipher
from app.models.operational import OAuthState
from app.repositories.oauth_state_repository import OAuthStateRepository

BLOCKED_CONSUMER_SETTLE_SECONDS = 0.3


async def _states(session) -> set[str]:
    result = await session.execute(select(OAuthState.state))
    return set(result.scalars().all())


class TestDeleteExpired:
    async def test_expired_rows_deleted_unexpired_kept(self, db_session):
        repo = OAuthStateRepository(db_session)
        expired_a = await repo.issue("slack_install", ttl_seconds=-60)
        expired_b = await repo.issue(
            "google_link", team_id="T1", slack_user_id="U1", ttl_seconds=-1
        )
        live = await repo.issue("slack_install", ttl_seconds=600)
        await db_session.commit()

        deleted = await repo.delete_expired()
        await db_session.commit()

        assert deleted == 2
        remaining = await _states(db_session)
        assert remaining == {live}
        assert expired_a not in remaining and expired_b not in remaining

    async def test_nothing_expired_deletes_nothing(self, db_session):
        repo = OAuthStateRepository(db_session)
        live = await repo.issue("slack_install")
        await db_session.commit()

        assert await repo.delete_expired() == 0
        assert await _states(db_session) == {live}

    async def test_tick_purges_expired_states_and_keeps_response_keys(
        self, api_client, db_session, monkeypatch
    ):
        key = Fernet.generate_key().decode()
        cipher = TokenCipher(keys={1: key}, current_version=1)
        monkeypatch.setattr("app.api.routes_internal.settings.cron_shared_secret", "s3cret")
        monkeypatch.setattr(
            "app.api.routes_internal.token_cipher_from_settings", lambda settings: cipher
        )
        repo = OAuthStateRepository(db_session)
        await repo.issue("slack_install", ttl_seconds=-60)
        live = await repo.issue("slack_install", ttl_seconds=600)
        await db_session.commit()

        response = await api_client.post("/internal/tick", headers={"X-Cron-Secret": "s3cret"})

        assert response.status_code == 200
        assert response.json() == {"reminders_sent": 0, "processed_events_deleted": 0}
        assert await _states(db_session) == {live}


class TestConsumeIsAtomic:
    async def test_two_concurrent_consumers_exactly_one_wins(self, db_session, db_engine):
        """Consumer A deletes the row but has not committed yet; consumer B then
        tries to consume the same state. With select-then-delete, B's select still
        sees the row and both succeed; with a single DELETE ... RETURNING, B blocks on
        the row lock and gets nothing once A commits."""
        state = await OAuthStateRepository(db_session).issue("slack_install")
        await db_session.commit()

        session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
        a_consumed = asyncio.Event()

        async def consumer_a():
            async with session_factory() as session:
                row = await OAuthStateRepository(session).consume(
                    state, expected_purpose="slack_install"
                )
                a_consumed.set()
                # Hold A's transaction open while B runs against the same row.
                await asyncio.sleep(BLOCKED_CONSUMER_SETTLE_SECONDS)
                await session.commit()
                return row

        async def consumer_b():
            await a_consumed.wait()
            async with session_factory() as session:
                row = await OAuthStateRepository(session).consume(
                    state, expected_purpose="slack_install"
                )
                await session.commit()
                return row

        row_a, row_b = await asyncio.gather(consumer_a(), consumer_b())

        winners = [row for row in (row_a, row_b) if row is not None]
        assert len(winners) == 1
        assert winners[0].state == state
        count = await db_session.scalar(select(func.count()).select_from(OAuthState))
        assert count == 0

    async def test_barrier_released_consumers_exactly_one_wins(self, db_session, db_engine):
        """Many rounds of two consumers released together by a barrier."""
        session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
        repo = OAuthStateRepository(db_session)

        for _ in range(10):
            state = await repo.issue("slack_install")
            await db_session.commit()
            barrier = asyncio.Barrier(2)

            async def consumer(state=state, barrier=barrier):
                async with session_factory() as session:
                    await barrier.wait()
                    row = await OAuthStateRepository(session).consume(
                        state, expected_purpose="slack_install"
                    )
                    await session.commit()
                    return row

            results = await asyncio.gather(consumer(), consumer())
            assert sum(row is not None for row in results) == 1
