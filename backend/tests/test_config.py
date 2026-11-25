from collections.abc import AsyncIterator

import httpx
import pytest

from app.core.config import Settings
from app.core.db import get_db
from app.main import create_app


def test_production_rejects_missing_database_url() -> None:
    with pytest.raises(ValueError):
        Settings(ENVIRONMENT="production", DATABASE_URL="")


def test_production_rejects_sqlite_database_url() -> None:
    with pytest.raises(ValueError):
        Settings(
            ENVIRONMENT="production",
            DATABASE_URL="sqlite+aiosqlite:///./dev.db",
        )


def test_production_accepts_postgresql_database_url() -> None:
    settings = Settings(
        ENVIRONMENT="production",
        DATABASE_URL="postgresql+asyncpg://user:pass@ep-pooler.region.aws.neon.tech/neondb?ssl=require",
    )
    assert settings.ENVIRONMENT == "production"
    assert settings.DATABASE_URL.startswith("postgresql+asyncpg")


def test_development_still_accepts_sqlite() -> None:
    settings = Settings(
        ENVIRONMENT="development",
        DATABASE_URL="sqlite+aiosqlite:///./dev.db",
    )
    assert settings.ENVIRONMENT == "development"
    assert settings.DATABASE_URL.startswith("sqlite")


def test_database_migration_url_field_exists() -> None:
    settings = Settings(
        DATABASE_MIGRATION_URL="postgresql+asyncpg://user:pass@ep-direct.region.aws.neon.tech/neondb?ssl=require",
    )
    assert settings.DATABASE_MIGRATION_URL.startswith("postgresql+asyncpg")


async def _dummy_db() -> AsyncIterator[None]:
    yield None


@pytest.mark.asyncio
async def test_production_disables_api_docs_and_keeps_auth_and_demo_gate() -> None:
    settings = Settings(
        ENVIRONMENT="production",
        DATABASE_URL="postgresql+asyncpg://user:pass@localhost/neondb",
        PUBLIC_DEMO_ENABLED=False,
    )
    app = create_app(settings_override=settings)
    app.state.settings = settings
    app.dependency_overrides[get_db] = _dummy_db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as ac:
        for path in ("/docs", "/redoc", "/openapi.json"):
            res = await ac.get(path)
            assert res.status_code == 404, (
                f"{path} expected 404 in production, got {res.status_code}"
            )

        auth_res = await ac.get("/api/v1/auth/me")
        assert auth_res.status_code == 401

        demo_res = await ac.get("/api/v1/demo/dashboard")
        assert demo_res.status_code == 404


@pytest.mark.asyncio
async def test_development_retains_api_docs_health_auth_and_demo_gate(
    client: httpx.AsyncClient,
) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        res = await client.get(path)
        assert res.status_code == 200, f"{path} expected 200 in development, got {res.status_code}"

    health = await client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "db": "connected"}

    assert (await client.get("/api/v1/auth/me")).status_code == 401
