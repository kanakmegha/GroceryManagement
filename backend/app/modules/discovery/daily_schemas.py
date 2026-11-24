from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.recipes.schemas import RecipeChatIngredientOut, RecipeStepOut

DailyMealCategory = Literal["breakfast", "lunch", "snacks", "dinner"]
DailyPlanStatus = Literal["pending", "generating", "ready", "failed", "unavailable"]


class DailyMealPlanRecipeOut(BaseModel):
    id: UUID
    meal_category: DailyMealCategory
    position: int
    slug: str
    title: str
    description: str = ""
    servings: int = 2
    prep_minutes: int = 15
    cook_minutes: int | None = None
    difficulty: str | None = None
    cuisine: str | None = None
    dietary_tags: list[str] = Field(default_factory=list)
    equipment_required: list[str] = Field(default_factory=list)
    preparation_steps: list[str] = Field(default_factory=list)
    structured_steps: list[RecipeStepOut] = Field(default_factory=list)
    instructions: list[str] = Field(default_factory=list)
    substitutions: list[str] = Field(default_factory=list)
    cooking_tips: list[str] = Field(default_factory=list)
    troubleshooting_tips: list[str] = Field(default_factory=list)
    serving_result: str | None = None
    food_safety_notes: list[str] = Field(default_factory=list)
    availability_bucket: Literal["can_make_now", "almost_there", "explore_more"] = "explore_more"
    available_ingredients: list[RecipeChatIngredientOut] = Field(default_factory=list)
    partial_ingredients: list[RecipeChatIngredientOut] = Field(default_factory=list)
    missing_ingredients: list[RecipeChatIngredientOut] = Field(default_factory=list)
    optional_ingredients: list[RecipeChatIngredientOut] = Field(default_factory=list)
    quality_status: str = "valid"
    quality_warnings: list[str] = Field(default_factory=list)


class DailyMealPlanResponse(BaseModel):
    status: DailyPlanStatus
    local_date: date
    timezone: str
    active_meal_category: DailyMealCategory
    auto_attempted: bool = False
    manual_regeneration_count: int = 0
    manual_regeneration_limit: int = 1
    is_stale_fallback: bool = False
    generated_at: datetime | None = None
    weather_summary: str | None = None
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    categories: dict[DailyMealCategory, list[DailyMealPlanRecipeOut]] = Field(
        default_factory=lambda: {
            "breakfast": [],
            "lunch": [],
            "snacks": [],
            "dinner": [],
        }
    )
    recipes: list[DailyMealPlanRecipeOut] = Field(default_factory=list)
