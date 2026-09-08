import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The URL is resolved here, in Python, and never round-tripped through
# ConfigParser (config.set_main_option / config.get_section both go through
# it). ConfigParser performs %-interpolation, which corrupts any password
# containing a URL-encoded character such as %40 or %23 — exactly what a
# generated Supabase password looks like. A caller (e.g. a test) may already
# have called config.set_main_option("sqlalchemy.url", ...) to point at a
# scratch database; that takes precedence over the app's own settings.
db_url = config.get_main_option("sqlalchemy.url") or settings.database_url
target_metadata = Base.metadata


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
