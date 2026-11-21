from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class HouseholdItemOut(BaseModel):
    id: UUID
    name: str
    currency_code: str
    role: str
    joined_at: datetime


class HouseholdListResponse(BaseModel):
    items: list[HouseholdItemOut]


class CreateHouseholdRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    currency_code: str = Field(default="INR", min_length=3, max_length=3)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped

    @field_validator("currency_code")
    @classmethod
    def _normalize_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        if len(normalized) != 3 or not normalized.isalpha():
            raise ValueError("currency_code must be a 3-letter ISO code")
        return normalized


class HouseholdDetailOut(BaseModel):
    id: UUID
    name: str
    currency_code: str
    role: str
