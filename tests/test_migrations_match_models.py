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
    alembic_cfg.attributes["sqlalchemy.url"] = scratch_db
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
    alembic_cfg.attributes["sqlalchemy.url"] = scratch_db
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


def test_url_with_percent_encoded_password_survives_config_attributes():
    """S10: a Supabase-generated password routinely contains a URL-encoded
    character like %40 or %23. Verified experimentally (not assumed):
    Config.set_main_option() itself raises ValueError on such a URL, before
    it's ever read back — configparser.BasicInterpolation treats a bare '%'
    as the start of an interpolation reference. alembic/env.py resolves its
    URL via config.attributes (a plain dict, no ConfigParser involved) for
    exactly this reason; this pins that a %40-bearing URL survives that
    path completely unchanged, and that the old, broken path really does
    blow up on the same input."""
    tricky_url = "postgresql+asyncpg://user:pass%40word@localhost:6543/db"

    alembic_cfg = Config("alembic.ini")

    # The mechanism env.py actually uses: unaffected by ConfigParser.
    alembic_cfg.attributes["sqlalchemy.url"] = tricky_url
    assert alembic_cfg.attributes["sqlalchemy.url"] == tricky_url

    # The mechanism the original bug used: demonstrably broken on this input.
    with pytest.raises(ValueError, match="interpolation"):
        alembic_cfg.set_main_option("sqlalchemy.url", tricky_url)
