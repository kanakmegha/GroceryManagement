from collections.abc import AsyncGenerator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import Request
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def normalize_database_url(database_url: str) -> str:
    """Normalize PostgreSQL/Neon URLs for SQLAlchemy asyncpg while preserving SQLite URLs."""
    raw = database_url.strip()
    if not raw or raw.startswith("sqlite"):
        return raw

    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme in {"postgres", "postgresql"}:
        scheme = "postgresql+asyncpg"
    elif scheme != "postgresql+asyncpg":
        return raw

    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    has_ssl = any(k.lower() == "ssl" for k, _ in query_pairs)
    normalized_pairs: list[tuple[str, str]] = []
    for key, val in query_pairs:
        low = key.lower()
        if low == "sslmode":
            if not has_ssl and val:
                normalized_pairs.append(("ssl", val))
                has_ssl = True
            continue
        if low == "channel_binding":
            # asyncpg does not accept channel_binding in connect kwargs
            continue
        normalized_pairs.append((key, val))

    return urlunsplit(
        (
            scheme,
            parts.netloc,
            parts.path,
            urlencode(normalized_pairs),
            parts.fragment,
        )
    )


def build_async_engine(database_url: str) -> AsyncEngine:
    normalized_url = normalize_database_url(database_url)
    if normalized_url.startswith("sqlite"):
        engine = create_async_engine(
            normalized_url,
            connect_args={"check_same_thread": False},
            pool_pre_ping=True,
        )

        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine

    return create_async_engine(
        normalized_url,
        connect_args={"statement_cache_size": 0},
        pool_size=3,
        max_overflow=2,
        pool_pre_ping=True,
        pool_recycle=300,
    )


def build_session_maker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    session_maker: async_sessionmaker[AsyncSession] = request.app.state.session_maker
    async with session_maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
