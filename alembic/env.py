import asyncio
import os
import re
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from app.core.config import settings
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The URL is resolved here, in Python, and never round-tripped through
# ConfigParser (set_main_option/get_main_option/get_section all go through
# it). ConfigParser performs %-interpolation — verified experimentally, not
# assumed: config.set_main_option() itself raises ValueError on a URL
# containing "%40", before the value is ever read back. Exactly what a
# generated Supabase password looks like. A caller (e.g. a test) may have
# set config.attributes["sqlalchemy.url"] — a plain dict, no ConfigParser
# involved — to point at a scratch database; that takes precedence.
db_url = config.attributes.get("sqlalchemy.url") or settings.database_url
target_metadata = Base.metadata

# tests/conftest.py's D0 guard only intercepts pytest's own drop_all — it
# never covers a bare `alembic downgrade`/`upgrade` invoked directly on the
# CLI. That gap is exactly what let an unattended `alembic downgrade base`
# wipe the live seeded workspace/user rows on 2026-09-21 (restored from a
# 2026-09-10 backup afterwards). This guard closes it: any alembic run
# against a database name outside the always-safe test set requires an
# explicit, one-time opt-in env var naming that exact database, so an agent
# or script can never do this by accident again.
_ALWAYS_SAFE_DB_NAMES = {"swa_test", "swa_devin", "swa_migration_check"}


def _assert_safe_migration_target(url: str) -> None:
    match = re.search(r"/([^/?]+)(?:\?|$)", url)
    db_name = match.group(1) if match else None
    if db_name in _ALWAYS_SAFE_DB_NAMES:
        return
    confirmed = os.environ.get("CONFIRM_LIVE_ALEMBIC")
    if confirmed == db_name:
        return
    raise RuntimeError(
        f"Refusing to run alembic against database {db_name!r} — it is not in "
        f"the always-safe set {sorted(_ALWAYS_SAFE_DB_NAMES)}. If you really "
        f"intend to migrate {db_name!r} (take a pg_dump backup first), set "
        f"CONFIRM_LIVE_ALEMBIC={db_name} explicitly for this one invocation."
    )


_assert_safe_migration_target(db_url)


def run_migrations_offline() -> None:
    context.configure(
        url=db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = create_async_engine(
        db_url,
        poolclass=pool.NullPool,
        connect_args={"statement_cache_size": 0},
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
