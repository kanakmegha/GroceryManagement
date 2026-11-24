"""Tests for database engine configuration: Neon pooling and SQLite compatibility."""

from app.core.db import build_async_engine


def test_postgresql_engine_has_conservative_pool_limits() -> None:
    """Render Free Neon pool must be capped to avoid exhausting connections."""
    url = (
        "postgresql+asyncpg://user:pass@ep-cool-pooler."
        "ap-southeast-1.aws.neon.tech/neondb?ssl=require"
    )
    engine = build_async_engine(url)
    try:
        assert engine.dialect.name == "postgresql"
        assert engine.dialect.driver == "asyncpg"
        # Conservative limits for Neon Free.
        assert engine.pool._pool.maxsize == 3
        assert engine.pool._max_overflow == 2
        assert engine.pool._pre_ping is True
    finally:
        engine.sync_engine.dispose()


def test_sqlite_engine_preserved_without_pool_limits() -> None:
    """Local development SQLite behavior must remain unchanged (no pool caps)."""
    engine = build_async_engine("sqlite+aiosqlite:///./dev.db")
    try:
        assert engine.dialect.name == "sqlite"
        assert engine.dialect.driver == "aiosqlite"
        # SQLite keeps pool_pre_ping and is NOT given pool_size/max_overflow caps.
        assert engine.pool._pre_ping is True
        assert engine.pool._max_overflow == 10  # SQLAlchemy default, uncapped
    finally:
        engine.sync_engine.dispose()
