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
# ConfigParser (set_main_option/get_main_option/get_section all go through
# it). ConfigParser performs %-interpolation — verified experimentally, not
# assumed: config.set_main_option() itself raises ValueError on a URL
# containing "%40", before the value is ever read back. Exactly what a
# generated Supabase password looks like. A caller (e.g. a test) may have
# set config.attributes["sqlalchemy.url"] — a plain dict, no ConfigParser
# involved — to point at a scratch database; that takes precedence.
db_url = config.attributes.get("sqlalchemy.url") or settings.database_url
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
