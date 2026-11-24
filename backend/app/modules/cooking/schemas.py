from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CookingIngredientIn(BaseModel):
    line_id: str | None = None
    canonical_item_id: UUID
    name: str | None = None
    planned_qty: Decimal = Field(gt=0)
    base_unit: str = Field(min_length=1, max_length=8)


class CreateCookingPlanRequest(BaseModel):
    recipe_id: str | None = None
    recipe_title: str | None = Field(default=None, min_length=1, max_length=200)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    planned_servings: int = Field(default=2, ge=1)
    meal_type: str | None = Field(default=None, max_length=32)
    cuisine: str | None = Field(default=None, max_length=64)
    prep_minutes: int | None = Field(default=None, ge=0)
    cook_minutes: int | None = Field(default=None, ge=0)
    description: str | None = None
    steps: list[str] | None = None
    substitutions: list[str] | None = None
    ingredients: list[CookingIngredientIn] | None = None
    items: list[CookingIngredientIn] | None = None


class CompleteIngredientInput(BaseModel):
    line_id: str | None = None
    canonical_item_id: UUID
    actual_qty: Decimal | None = Field(default=None, ge=0)
    actual_used_qty: Decimal | None = Field(default=None, ge=0)
    waste_qty: Decimal | None = Field(default=None, ge=0)
    omitted: bool = False
    session_confirmed: bool = False
    confirmed_qty: Decimal | None = Field(default=None, ge=0)
    confirmed_unit: str | None = Field(default=None, max_length=16)


class CompleteCookingPlanRequest(BaseModel):
    items: list[CompleteIngredientInput] | None = None
    meal_type: str | None = Field(default=None, max_length=32)
    cook_minutes: int | None = Field(default=None, ge=0)
    substitutions: list[str] | None = None
    modifications: str | None = Field(default=None, max_length=1000)
    rating: int | None = Field(default=None, ge=1, le=5)
    feedback_tags: list[str] | None = None
    feedback_comment: str | None = Field(default=None, max_length=1000)
    would_make_again: bool | None = None


class CookingDeductionOut(BaseModel):
    canonical_item_id: UUID
    lot_id: UUID
    deducted_qty: Decimal
    base_unit: str
    ledger_entry_id: UUID


class CookingPlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cooking_plan_id: UUID | None = None
    household_id: UUID
    recipe_id: str | None
    recipe_title: str
    title: str | None = None
    status: str
    planned_servings: int
    ingredients: list[dict]
    items: list[dict] | None = None
    deductions: list[dict] | None = None
    started_at: datetime
    created_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    meal_type: str | None = None
    cuisine: str | None = None
    prep_minutes: int | None = None
    cook_minutes: int | None = None
    meal_history_id: UUID | None = None


class CookingPlanListResponse(BaseModel):
    items: list[CookingPlanOut]
    total: int


class MealFeedbackRequest(BaseModel):
    rating: int | None = Field(default=None, ge=1, le=5)
    feedback_tags: list[str] = Field(default_factory=list)
    feedback_comment: str | None = Field(default=None, max_length=1000)
    would_make_again: bool | None = None


class MealHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    household_id: UUID
    cooking_plan_id: UUID
    cooked_by: UUID
    recipe_id: str | None = None
    recipe_title: str
    meal_type: str | None = None
    cuisine: str | None = None
    servings: int
    prep_minutes: int | None = None
    cook_minutes: int | None = None
    ingredients_used: list[dict[str, Any]] = Field(default_factory=list)
    substitutions: list[str] = Field(default_factory=list)
    recipe_snapshot: dict[str, Any] | None = None
    cooked_at: datetime
    rating: int | None = None
    feedback_tags: list[str] = Field(default_factory=list)
    feedback_comment: str | None = None
    would_make_again: bool | None = None
    feedback_updated_at: datetime | None = None
    cook_count: int = 1


class MealHistoryListResponse(BaseModel):
    items: list[MealHistoryOut]
    total: int
    frequently_cooked: list[str] = Field(default_factory=list)
    favourite_meals: list[str] = Field(default_factory=list)
    disliked_meals: list[str] = Field(default_factory=list)
