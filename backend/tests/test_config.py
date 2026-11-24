import pytest

from app.core.config import Settings


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
