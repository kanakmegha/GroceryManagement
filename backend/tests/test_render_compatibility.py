from __future__ import annotations

from pathlib import Path

from app.core.db import build_async_engine, normalize_database_url


def test_sqlite_url_remains_unchanged() -> None:
    local_url = "sqlite+aiosqlite:///./dev_local.db"
    container_url = "sqlite+aiosqlite:////app/data/dev_local.db"
    assert normalize_database_url(local_url) == local_url
    assert normalize_database_url(container_url) == container_url


def test_postgresql_url_gets_asyncpg_dialect_and_neon_ssl() -> None:
    neon_raw = (
        "postgresql://user:pass@ep-cool-pooler.ap-southeast-1.aws.neon.tech/neondb"
        "?sslmode=require&channel_binding=require"
    )
    normalized = normalize_database_url(neon_raw)
    assert normalized == (
        "postgresql+asyncpg://user:pass@ep-cool-pooler.ap-southeast-1.aws.neon.tech/neondb"
        "?ssl=require"
    )

    postgres_scheme = "postgres://user:pass@ep-direct.aws.neon.tech/neondb?sslmode=require"
    assert normalize_database_url(postgres_scheme) == (
        "postgresql+asyncpg://user:pass@ep-direct.aws.neon.tech/neondb?ssl=require"
    )


def test_already_normalized_url_is_idempotent() -> None:
    already_ok = (
        "postgresql+asyncpg://user:pass@ep-cool.aws.neon.tech/neondb?ssl=require"
    )
    assert normalize_database_url(already_ok) == already_ok

    engine = build_async_engine(already_ok)
    try:
        assert engine.dialect.name == "postgresql"
        assert engine.dialect.driver == "asyncpg"
    finally:
        engine.sync_engine.dispose()


def test_dockerfile_respects_dynamic_port_and_defaults_to_8001() -> None:
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(encoding="utf-8")
    assert "${PORT:-8001}" in dockerfile
    assert "--workers 1" in dockerfile
    # The startup command must bind to Render's injected $PORT (default 8001).
    assert "--port ${PORT:-8001}" in dockerfile
    # Runtime must use the pre-installed venv uvicorn directly, not `uv run`
    # (which would re-resolve/download packages on every container start).
    assert "uv run" not in dockerfile
    assert "/app/.venv/bin/uvicorn" in dockerfile
