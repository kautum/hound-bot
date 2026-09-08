"""The suite's fixtures build tables straight from `Base.metadata`, so a
migration that drifts from the models is invisible to every other test — and
to CI. This test is the one place that runs the *actual* migrations against a
scratch database and diffs the result against the models, using Alembic's own
comparison engine as the oracle rather than our own expectations.
"""

import asyncio

import asyncpg
import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.models import Base

SCRATCH_DB = "swa_migration_check"


def _admin_dsn() -> str:
    # Strip the +asyncpg driver marker: asyncpg's own connect() wants a plain
    # postgresql:// DSN, not a SQLAlchemy URL.
    url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    return url.rsplit("/", 1)[0] + "/postgres"


def _scratch_url() -> str:
    base = settings.database_url.rsplit("/", 1)[0]
    return f"{base}/{SCRATCH_DB}"


async def _recreate_scratch_db():
    conn = await asyncpg.connect(_admin_dsn())
    try:
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            SCRATCH_DB,
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"')
        await conn.execute(f'CREATE DATABASE "{SCRATCH_DB}"')
    finally:
        await conn.close()


@pytest.fixture
def scratch_db():
    asyncio.run(_recreate_scratch_db())
    return _scratch_url()


def test_migrations_match_models(scratch_db):
    """Upgrading head then diffing against Base.metadata must show no drift."""
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", scratch_db)
    from alembic import command

    command.upgrade(alembic_cfg, "head")

    async def _diff():
        engine = create_async_engine(scratch_db)
        async with engine.connect() as conn:
            diff = await conn.run_sync(
                lambda sync_conn: compare_metadata(
                    MigrationContext.configure(sync_conn), Base.metadata
                )
            )
        await engine.dispose()
        return diff

    diff = asyncio.run(_diff())
    assert diff == [], f"Migrations have drifted from the models: {diff}"


def test_migrations_downgrade_cleanly_to_base(scratch_db):
    """Every migration's downgrade() must actually work, not just exist."""
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", scratch_db)
    from alembic import command

    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "base")

    script = ScriptDirectory.from_config(alembic_cfg)
    assert script.get_heads(), "No migrations found in alembic/versions"

    async def _current_heads():
        engine = create_async_engine(scratch_db)
        async with engine.connect() as conn:
            heads = await conn.run_sync(
                lambda sync_conn: MigrationContext.configure(sync_conn).get_current_heads()
            )
        await engine.dispose()
        return heads

    current_heads = asyncio.run(_current_heads())
    assert current_heads == (), "Downgrade to base did not clear the version table"
