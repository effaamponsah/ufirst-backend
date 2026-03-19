from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import settings
from app.core.database import Base

# ---------------------------------------------------------------------------
# Alembic config
# ---------------------------------------------------------------------------

alembic_config = context.config
alembic_config.set_main_option("sqlalchemy.url", settings.async_database_url)

if alembic_config.config_file_name is not None:
    fileConfig(alembic_config.config_file_name)

# Import all models so their tables are registered on Base.metadata.
# Add each module's models here as they are implemented.
from app.modules.identity import models as _identity_models  # noqa: F401
# from app.modules.wallet import models as _wallet_models   # noqa: F401

target_metadata = Base.metadata

# ---------------------------------------------------------------------------
# Schema list — ensured to exist before migrations run
# ---------------------------------------------------------------------------

SCHEMAS = [
    "identity",
    "wallet",
    "card",
    "transaction",
    "vendor",
    "compliance",
    "notification",
    "reporting",
]


def include_object(object, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
    """Only migrate tables that belong to our managed schemas."""
    if type_ == "table" and object.schema not in SCHEMAS:
        return False
    return True


# ---------------------------------------------------------------------------
# Offline migrations
# ---------------------------------------------------------------------------


def run_migrations_offline() -> None:
    url = settings.async_database_url
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online migrations (async)
# ---------------------------------------------------------------------------


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        include_object=include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        alembic_config.get_section(alembic_config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        # Ensure all module schemas exist before running migrations
        for schema in SCHEMAS:
            await connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        await connection.commit()

        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
