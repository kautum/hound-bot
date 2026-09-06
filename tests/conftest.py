import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models import Base


@pytest.fixture
async def db_engine():
    """A real Postgres engine against DATABASE_URL, schema created and torn
    down per test. Requires `make db-up` (or an equivalent reachable Postgres).
    Exposed separately from db_session so tests that need multiple independent
    connections (e.g. exercising real Postgres row locking) can open their own
    sessions against the same schema."""
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine):
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest.fixture
async def api_client(db_session):
    """An async httpx client against the app via ASGITransport — not the sync
    TestClient, which runs the app in a separate thread with its own event
    loop and breaks when a route and the test share one asyncpg session
    across loops. DB dependency overridden to use the per-test db_session,
    so route assertions see what the route wrote."""
    import httpx

    from app.core.db import get_session
    from app.main import app

    async def _override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()
