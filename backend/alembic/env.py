import asyncio
from logging.config import fileConfig

from sqlalchemy import String, inspect, pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import get_settings
from app.core.db import Base, normalize_database_url
from app.modules.auth import models as _auth_models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_database_url() -> str:
    configured = config.get_main_option("sqlalchemy.url")
    if configured:
        return normalize_database_url(configured)
    settings = get_settings()
    return normalize_database_url(settings.DATABASE_MIGRATION_URL or settings.DATABASE_URL)


def _configure_version_column_width() -> None:
    """Widen alembic_version.version_num from default VARCHAR(32) to VARCHAR(64).

    Revision 0018_purchase_item_resolution_meta is 34 characters, which PostgreSQL
    strictly rejects under Alembic's default 32-character limit.
    """
    migration_ctx = context.get_context()
    migration_ctx._version.c.version_num.type = String(64)


def run_migrations_offline() -> None:
    url = _get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    _configure_version_column_width()

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    _configure_version_column_width()

    with context.begin_transaction():
        if connection.dialect.name == "postgresql" and inspect(connection).has_table(
            "alembic_version"
        ):
            connection.execute(
                text("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(64)")
            )
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    url = _get_database_url()
    configuration["sqlalchemy.url"] = url

    connect_args: dict[str, object] = {}
    if url.startswith("postgresql"):
        connect_args["statement_cache_size"] = 0
    elif url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
