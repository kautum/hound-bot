"""Async SQLAlchemy engine/session, pointed at DATABASE_URL.

In production this is the Supabase pooler URL on port 6543, not a direct 5432
connection — see ARCHITECTURE.md. Locally it's native Postgres (see
PROJECT-WIKI.md §7 — no Docker on this machine).
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# S9: the Supabase pooler (port 6543) runs pgbouncer in transaction mode,
# which is incompatible with asyncpg's server-side prepared statements —
# every query would fail on day one in production despite passing every
# test against a direct connection. statement_cache_size=0 disables them;
# pool_pre_ping avoids handing out a connection the pooler has silently
# dropped.
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={"statement_cache_size": 0},
)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session
