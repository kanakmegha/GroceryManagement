from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    APP_NAME: str = "GroceryManagement API"
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    DATABASE_URL: str = "sqlite+aiosqlite:///./dev.db"
    DATABASE_MIGRATION_URL: str | None = None

    CLERK_ISSUER: str = ""
    CLERK_JWKS_URL: str = ""
    CLERK_AUTHORIZED_PARTIES: Annotated[list[str], NoDecode] = []
    CLERK_SECRET_KEY: str | None = None

    JWKS_CACHE_TTL_SECONDS: int = 3600
    JWKS_REFRESH_COOLDOWN_SECONDS: int = 30
    JWT_LEEWAY_SECONDS: int = 10

    HF_TOKEN: str = ""
    HF_MODEL_ID: str = "Qwen/Qwen2.5-72B-Instruct"
    HF_TIMEOUT_SECONDS: float = 20.0

    # Isolated Experimental V10.6 OCR Service (disabled by default; never replaces production OCR)
    EXPERIMENTAL_OCR_ENABLED: bool = False
    EXPERIMENTAL_OCR_PRIMARY: bool = False
    EXISTING_OCR_FALLBACK_ENABLED: bool = True
    EXPERIMENTAL_OCR_PROVIDER: str = "qwen3_vl_30b_v10_6"
    EXPERIMENTAL_OCR_MODEL: str = "Qwen/Qwen3-VL-30B-A3B-Instruct"
    EXPERIMENTAL_OCR_BASE_URL: str = "https://router.huggingface.co/v1/chat/completions"
    EXPERIMENTAL_OCR_API_KEY: str = ""
    EXPERIMENTAL_OCR_TIMEOUT_SECONDS: float = 90.0

    @field_validator("CLERK_AUTHORIZED_PARTIES", mode="before")
    @classmethod
    def _parse_authorized_parties(cls, value: object) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        raise ValueError("CLERK_AUTHORIZED_PARTIES must be a list or comma-separated string")

    @property
    def effective_jwks_url(self) -> str:
        if self.CLERK_JWKS_URL:
            return self.CLERK_JWKS_URL
        if self.CLERK_ISSUER:
            return f"{self.CLERK_ISSUER.rstrip('/')}/.well-known/jwks.json"
        return ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
