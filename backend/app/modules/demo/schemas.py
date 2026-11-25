"""Explicit field-allowlisted response DTOs for Public Product Demo read-only endpoints.

Every DTO sets ``extra="forbid"`` and omits user emails, Clerk IDs, household owner IDs,
raw OCR payloads, attachments, receipt/order numbers, payment methods, and free-text notes.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.discovery.daily_schemas import DailyMealCategory


class _StrictDemoModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DemoHouseholdInfoOut(_StrictDemoModel):
    name: str
    currency_code: str
    is_demo_mode: Literal[True] = True
    read_only: Literal[True] = True


class DemoInventorySummaryItemOut(_StrictDemoModel):
    canonical_item_id: UUID
    name: str
    slug: str
    category: str
    secondary_category: str | None = None
    total_remaining_qty: Decimal
    base_unit: str
    active_lot_count: int
    earliest_expires_on: date | None = None
    has_expired_lots: bool = False


class DemoInventoryLotOut(_StrictDemoModel):
    id: UUID
    canonical_item_id: UUID
    item_name: str
    category: str
    initial_qty: Decimal
    remaining_qty: Decimal
    base_unit: str
    unit_cost_paise: int
    purchased_at: datetime
    expires_on: date | None = None
    lot_status: str


class DemoInventoryResponse(_StrictDemoModel):
    demo_mode: Literal[True] = True
    summary: list[DemoInventorySummaryItemOut] = Field(default_factory=list)
    lots: list[DemoInventoryLotOut] = Field(default_factory=list)
    total_items: int = 0
    total_lots: int = 0
    expiring_soon_count: int = 0
    limit: int = 50
    offset: int = 0


class DemoPurchaseItemOut(_StrictDemoModel):
    id: UUID
    line_index: int
    display_name: str
    normalized_name: str | None = None
    brand: str | None = None
    variant: str | None = None
    pack_size: str | None = None
    category: str
    secondary_category: str | None = None
    normalized_qty: Decimal
    base_unit: str
    unit_price_paise: int
    line_total_paise: int
    discount_paise: int = 0
    tax_paise: int = 0
    track_inventory: bool = True
    expires_on: date | None = None


class DemoPurchaseOut(_StrictDemoModel):
    id: UUID
    status: str
    source_type: str
    document_type: str
    merchant_name: str
    purchased_on: date
    purchased_time: str | None = None
    currency_code: str
    receipt_category: str
    subtotal_paise: int | None = None
    discount_paise: int = 0
    additional_charges_paise: int = 0
    tax_paise: int = 0
    total_amount_paise: int = 0
    item_count: int = 0
    items: list[DemoPurchaseItemOut] = Field(default_factory=list)


class DemoPurchasesResponse(_StrictDemoModel):
    demo_mode: Literal[True] = True
    items: list[DemoPurchaseOut] = Field(default_factory=list)
    total: int = 0
    limit: int = 20
    offset: int = 0


class DemoReceiptOut(_StrictDemoModel):
    """Sanitized receipt view: strictly excludes raw_ocr_payload, attachments, order/receipt IDs."""

    id: UUID
    merchant_name: str
    purchased_on: date
    purchased_time: str | None = None
    currency_code: str
    receipt_category: str
    document_type: str
    subtotal_paise: int | None = None
    discount_paise: int = 0
    tax_paise: int = 0
    total_amount_paise: int = 0
    item_count: int = 0
    items: list[DemoPurchaseItemOut] = Field(default_factory=list)


class DemoReceiptsResponse(_StrictDemoModel):
    demo_mode: Literal[True] = True
    items: list[DemoReceiptOut] = Field(default_factory=list)
    total: int = 0
    limit: int = 20
    offset: int = 0


class DemoDailyRecipeIngredientOut(_StrictDemoModel):
    name: str
    quantity: str = ""
    base_unit: str = "pcs"
    essential: bool = True
    status: str = "missing"
    available: bool = False


class DemoDailyRecipeStepOut(_StrictDemoModel):
    step_number: int
    title: str
    instruction: str
    ingredients_used: list[str] = Field(default_factory=list)
    duration_minutes: int | None = None
    heat_level: str | None = None
    technique: str | None = None
    equipment: list[str] = Field(default_factory=list)
    completion_cue: str = ""
    caution: str | None = None
    next_action: str | None = None


class DemoDailyMealPlanRecipeOut(_StrictDemoModel):
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
    structured_steps: list[DemoDailyRecipeStepOut] = Field(default_factory=list)
    instructions: list[str] = Field(default_factory=list)
    substitutions: list[str] = Field(default_factory=list)
    cooking_tips: list[str] = Field(default_factory=list)
    troubleshooting_tips: list[str] = Field(default_factory=list)
    serving_result: str | None = None
    availability_bucket: Literal["can_make_now", "almost_there", "explore_more"] = "explore_more"
    available_ingredients: list[DemoDailyRecipeIngredientOut] = Field(default_factory=list)
    partial_ingredients: list[DemoDailyRecipeIngredientOut] = Field(default_factory=list)
    missing_ingredients: list[DemoDailyRecipeIngredientOut] = Field(default_factory=list)
    optional_ingredients: list[DemoDailyRecipeIngredientOut] = Field(default_factory=list)
    quality_status: str = "valid"


class DemoDailyMealPlanResponse(_StrictDemoModel):
    demo_mode: Literal[True] = True
    status: Literal["ready", "empty"] = "empty"
    local_date: date
    timezone: str = "Asia/Kolkata"
    active_meal_category: DailyMealCategory = "breakfast"
    generated_at: datetime | None = None
    categories: dict[DailyMealCategory, list[DemoDailyMealPlanRecipeOut]] = Field(
        default_factory=lambda: {
            "breakfast": [],
            "lunch": [],
            "snacks": [],
            "dinner": [],
        }
    )
    recipes: list[DemoDailyMealPlanRecipeOut] = Field(default_factory=list)


class DemoCookingPlanOut(_StrictDemoModel):
    id: UUID
    recipe_title: str
    status: str
    planned_servings: int
    meal_type: str | None = None
    cuisine: str | None = None
    prep_minutes: int | None = None
    cook_minutes: int | None = None
    started_at: datetime
    completed_at: datetime | None = None


class DemoMealHistoryOut(_StrictDemoModel):
    id: UUID
    recipe_title: str
    meal_type: str | None = None
    cuisine: str | None = None
    servings: int
    prep_minutes: int | None = None
    cook_minutes: int | None = None
    cooked_at: datetime
    rating: int | None = None
    feedback_tags: list[str] = Field(default_factory=list)
    would_make_again: bool | None = None


class DemoRecipePreferencesOut(_StrictDemoModel):
    timezone: str = "Asia/Kolkata"
    diet_type: str = "vegetarian"
    dietary_restrictions: list[str] = Field(default_factory=list)
    preferred_cuisines: list[str] = Field(default_factory=list)
    preferred_meal_types: list[str] = Field(default_factory=list)
    max_prep_minutes: int | None = None
    recommendation_mode: str = "balanced"


class DemoRecipesResponse(_StrictDemoModel):
    demo_mode: Literal[True] = True
    preferences: DemoRecipePreferencesOut
    daily_recipes: list[DemoDailyMealPlanRecipeOut] = Field(default_factory=list)
    cooking_plans: list[DemoCookingPlanOut] = Field(default_factory=list)
    meal_history: list[DemoMealHistoryOut] = Field(default_factory=list)
    total_cooking_plans: int = 0
    total_meal_history: int = 0


class DemoCategorySpendOut(_StrictDemoModel):
    category: str
    total_paise: int
    item_count: int


class DemoMerchantSpendOut(_StrictDemoModel):
    merchant_name: str
    total_paise: int
    purchase_count: int


class DemoMonthlySpendOut(_StrictDemoModel):
    year: int
    month: int
    total_paise: int
    grocery_paise: int
    outside_food_paise: int
    purchase_count: int


class DemoBudgetOut(_StrictDemoModel):
    id: UUID
    period_type: str
    overall_limit_paise: int
    grocery_limit_paise: int | None = None
    outside_food_limit_paise: int | None = None
    effective_from: date


class DemoAnalyticsResponse(_StrictDemoModel):
    demo_mode: Literal[True] = True
    currency_code: str = "INR"
    total_spend_paise: int = 0
    grocery_spend_paise: int = 0
    outside_food_spend_paise: int = 0
    purchase_count: int = 0
    category_breakdown: list[DemoCategorySpendOut] = Field(default_factory=list)
    merchant_breakdown: list[DemoMerchantSpendOut] = Field(default_factory=list)
    monthly_trend: list[DemoMonthlySpendOut] = Field(default_factory=list)
    budgets: list[DemoBudgetOut] = Field(default_factory=list)


class DemoDashboardResponse(_StrictDemoModel):
    demo_mode: Literal[True] = True
    household: DemoHouseholdInfoOut
    inventory_item_count: int = 0
    active_lot_count: int = 0
    expiring_soon_lots: list[DemoInventoryLotOut] = Field(default_factory=list)
    recent_purchases: list[DemoPurchaseOut] = Field(default_factory=list)
    daily_plan: DemoDailyMealPlanResponse
    analytics: DemoAnalyticsResponse
