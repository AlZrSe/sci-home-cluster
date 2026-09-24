"""Alembic environment configuration for async migrations."""

import asyncio
import sys
import os
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import pool

from alembic import context

# Import models for autogenerate support
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from backend.store.database import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    """Run migrations with the given connection."""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode with async engine."""
# Get the database URL from config
    url = config.get_main_option("sqlalchemy.url")

    # Create async engine
    connectable = create_async_engine(
        url,
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_async_migrations():
    """Run async migrations, handling both sync and async contexts."""
    try:
        # Try to get the current event loop
        asyncio.get_running_loop()
        # If we're in a running loop, we need to run the coroutine differently
        # Create a task and run it in the existing loop
        import concurrent.futures

        def run_in_new_loop():
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                return new_loop.run_until_complete(run_migrations_online())
            finally:
                new_loop.close()

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_in_new_loop)
            future.result()
    except RuntimeError:
        # No running loop, safe to use asyncio.run()
        asyncio.run(run_migrations_online())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_async_migrations()
