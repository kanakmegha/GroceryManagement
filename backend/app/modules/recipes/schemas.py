from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

RecommendationMode = Literal["enjoyed", "new", "balanced"]


class RecipeIngredientOut(BaseModel):
    canonical_item_id: UUID | None = None
    name: str
    required_qty: Decimal
    base_unit: str
    is_optional: bool = False
    essential: bool = True
    status: str = "missing"
    available_qty: str | None = None
    missing_qty: str | None = None
    unit_compatible: bool = True


class RecipeReferenceOut(BaseModel):
    title: str
    url: str
    source: str | None = None
    link_type: str = "written_recipe"
    verified: bool = True


class RecipeStepOut(BaseModel):
    step_number: int
    title: str
    instruction: str
    ingredients_used: list[str] = Field(default_factory=list)
    ingredient_quantities: dict[str, str] = Field(default_factory=dict)
    duration_minutes: int | None = None
    heat_level: str | None = None
    technique: str | None = None
    equipment: list[str] = Field(default_factory=list)
    completion_cue: str = ""
    caution: str | None = None
    next_action: str | None = None


class RecipeRecommendationOut(BaseModel):
    recipe_id: str
    title: str
    description: str | None = None
    meal_type: str | None = None
    cuisine: str | None = None
    servings: int = 2
    prep_minutes: int = 15
    cook_minutes: int | None = None
    difficulty: str | None = None
    source_type: str = "ai_generated"
    source_label: str = "AI-generated recipe"
    recommendation_type: str = "REQUIRES_SHOPPING"
    why_recommended: str | None = None
    match_score: float = Field(default=1.0, ge=0.0, le=1.0)
    matched_ingredients_count: int = 0
    total_required_ingredients_count: int = 0
    uses_expiring_soon: bool = False
    missing_ingredients: list[str] = Field(default_factory=list)
    insufficient_ingredients: list[str] = Field(default_factory=list)
    ingredients: list[RecipeIngredientOut] = Field(default_factory=list)
    instructions: list[str] = Field(default_factory=list)
    preparation_steps: list[str] = Field(default_factory=list)
    structured_steps: list[RecipeStepOut] = Field(default_factory=list)
    equipment_required: list[str] = Field(default_factory=list)
    quality_warnings: list[str] = Field(default_factory=list)
    substitutions: list[str] = Field(default_factory=list)
    cooking_tips: list[str] = Field(default_factory=list)
    troubleshooting_tips: list[str] = Field(default_factory=list)
    serving_result: str | None = None
    food_safety_notes: list[str] = Field(default_factory=list)
    references: list[RecipeReferenceOut] = Field(default_factory=list)


class RecipeRecommendationsResponse(BaseModel):
    empty_inventory: bool
    items: list[RecipeRecommendationOut]
    is_refined: bool = True
    refinement_pending: bool = False
    greeting: str | None = None
    meal_window: str | None = None
    meal_window_label: str | None = None
    season: str | None = None
    weather_summary: str | None = None


class HouseholdRecipePreferencesIn(BaseModel):
    timezone: str = Field(default="Asia/Kolkata", min_length=1, max_length=64)
    location_name: str | None = Field(default=None, max_length=120)
    diet_type: str = Field(
        default="any", pattern="^(any|vegetarian|vegan|eggetarian|non_vegetarian)$"
    )
    dietary_restrictions: list[str] = Field(default_factory=list)
    preferred_cuisines: list[str] = Field(default_factory=list)
    preferred_meal_types: list[str] = Field(default_factory=list)
    max_prep_minutes: int | None = Field(default=None, ge=5, le=240)
    recommendation_mode: RecommendationMode | None = None
    dismiss_personalisation_prompt: bool | None = None


class HouseholdRecipePreferencesOut(HouseholdRecipePreferencesIn):
    household_id: UUID
    recommendation_mode: RecommendationMode = "balanced"
    distinct_completed_recipes: int = 0
    should_show_personalisation_prompt: bool = False
    personalisation_prompt_dismissed: bool = False


class RecommendationModeUpdateRequest(BaseModel):
    recommendation_mode: RecommendationMode | None = None
    dismiss_personalisation_prompt: bool | None = None


class RecipeFeedbackIn(BaseModel):
    recipe_id: str = Field(min_length=1, max_length=128)
    recipe_title: str = Field(min_length=1, max_length=200)
    action: str = Field(pattern="^(like|dislike|cooked|hide)$")


class RecipeFeedbackOut(RecipeFeedbackIn):
    id: UUID
    household_id: UUID


class RecipeReferencesResponse(BaseModel):
    items: list[RecipeReferenceOut]


class RecipeChatTurnInput(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=2000)


class RecipeChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    servings: int = Field(default=2, ge=1, le=20)
    max_prep_minutes: int | None = Field(default=None, ge=1, le=480)
    dietary: str | None = Field(default=None, max_length=100)
    dietary_constraints: list[str] | None = None
    meal_type: str | None = Field(default=None, max_length=40)
    hunger_level: str | None = Field(default=None, max_length=32)
    preference_tags: list[str] = Field(default_factory=list)
    equipment: list[str] = Field(default_factory=list)
    recommendation_mode: RecommendationMode | None = None
    conversation_history: list[RecipeChatTurnInput] = Field(default_factory=list)


class RecipeChatIngredientOut(BaseModel):
    name: str
    canonical_item_id: UUID | None = None
    quantity: str
    base_unit: str
    essential: bool = True
    status: str = "missing"
    available: bool
    available_qty: str | None = None
    missing_qty: str | None = None


class RecipeChatExternalLinkOut(BaseModel):
    title: str
    url: str
    source: str | None = None
    link_type: str = "written_recipe"
    verified: bool = True


class RecipeChatSuggestionOut(BaseModel):
    id: str | None = None
    recipe_id: str | None = None
    title: str
    meal_type: str | None = None
    servings: int = 2
    prep_minutes: int | None = None
    cook_minutes: int | None = None
    difficulty: str | None = None
    source_type: str = "ai_generated"
    source_label: str = "AI-generated recipe"
    recommendation_type: str = "REQUIRES_SHOPPING"
    why_recommended: str | None = None
    summary: str | None = None
    available_ingredients: list[RecipeChatIngredientOut] = Field(default_factory=list)
    partial_ingredients: list[RecipeChatIngredientOut] = Field(default_factory=list)
    missing_ingredients: list[RecipeChatIngredientOut] = Field(default_factory=list)
    optional_ingredients: list[RecipeChatIngredientOut] = Field(default_factory=list)
    substitutions: list[str] = Field(default_factory=list)
    cooking_tips: list[str] = Field(default_factory=list)
    troubleshooting_tips: list[str] = Field(default_factory=list)
    serving_result: str | None = None
    food_safety_notes: list[str] = Field(default_factory=list)
    uses_expiring_items: list[str] = Field(default_factory=list)
    equipment_required: list[str] = Field(default_factory=list)
    preparation_steps: list[str] = Field(default_factory=list)
    instructions: list[str] = Field(default_factory=list)
    structured_steps: list[RecipeStepOut] = Field(default_factory=list)
    quality_warnings: list[str] = Field(default_factory=list)
    external_links: list[RecipeChatExternalLinkOut] = Field(default_factory=list)


class RecipeChatResponse(BaseModel):
    reply: str
    provider_status: str
    provider_message: str | None = None
    model: str | None = None
    empty_inventory: bool = False
    follow_up_questions: list[str] = Field(default_factory=list)
    suggestions: list[RecipeChatSuggestionOut] = Field(default_factory=list)
    external_links: list[RecipeChatExternalLinkOut] = Field(default_factory=list)
