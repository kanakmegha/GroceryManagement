from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    clerk_user_id: str
    email: str | None
    display_name: str


class HouseholdSummaryOut(BaseModel):
    id: UUID
    name: str
    currency_code: str
    role: str


class AuthMeResponse(BaseModel):
    user: UserOut
    active_household: HouseholdSummaryOut
    households: list[HouseholdSummaryOut]


class UpdateMeRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)

    @field_validator("display_name")
    @classmethod
    def _strip_display_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("display_name must not be blank")
        return stripped
