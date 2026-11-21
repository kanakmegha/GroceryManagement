from datetime import datetime
from typing import Literal
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.tenancy import HouseholdContext, get_household_context
from app.modules.recipes.schemas import (
    HouseholdRecipePreferencesIn,
    HouseholdRecipePreferencesOut,
    RecipeChatRequest,
    RecipeChatResponse,
    RecipeFeedbackIn,
    RecipeFeedbackOut,
    RecipeRecommendationsResponse,
    RecipeReferencesResponse,
)
from app.modules.recipes.service import (
    _invoke_discover_references,
    chat_recipes,
    get_household_recipe_preferences,
    get_recipe_recommendations,
    list_recipe_feedback,
    record_recipe_feedback,
    upsert_household_recipe_preferences,
)

router = APIRouter(prefix="/recipes", tags=["recipes"])


@router.get("/recommendations", response_model=RecipeRecommendationsResponse)
async def recipe_recommendations(
    request: Request,
    limit: int = Query(default=10, ge=1, le=20),
    include_references: bool = Query(default=True),
    refresh: bool = Query(default=False),
    mode: Literal["progressive", "fast", "refined"] = Query(default="progressive"),
    at_time: datetime | None = Query(default=None),
    timezone: str | None = Query(default=None, max_length=64),
    temperature_c: float | None = Query(default=None),
    rainfall_mm: float | None = Query(default=None, ge=0.0),
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> RecipeRecommendationsResponse:
    settings: Settings = getattr(request.app.state, "settings", None) or get_settings()
    return await get_recipe_recommendations(
        db,
        household_id=ctx.household.id,
        settings=settings,
        limit=limit,
        include_references=include_references,
        force_refresh=refresh,
        mode=mode,
        at_time=at_time,
        timezone_override=timezone,
        temperature_c=temperature_c,
        rainfall_mm=rainfall_mm,
    )


@router.get("/preferences", response_model=HouseholdRecipePreferencesOut)
async def read_recipe_preferences(
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> HouseholdRecipePreferencesOut:
    return await get_household_recipe_preferences(db, ctx.household.id)


@router.put("/preferences", response_model=HouseholdRecipePreferencesOut)
async def save_recipe_preferences(
    payload: HouseholdRecipePreferencesIn,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> HouseholdRecipePreferencesOut:
    return await upsert_household_recipe_preferences(db, ctx.household.id, payload)


@router.get("/feedback", response_model=list[RecipeFeedbackOut])
async def get_recipe_feedback(
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> list[RecipeFeedbackOut]:
    return await list_recipe_feedback(db, ctx.household.id)


@router.post("/feedback", response_model=RecipeFeedbackOut, status_code=201)
async def submit_recipe_feedback(
    payload: RecipeFeedbackIn,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> RecipeFeedbackOut:
    return await record_recipe_feedback(db, ctx.household.id, payload)


@router.post("/chat", response_model=RecipeChatResponse)
async def recipe_chat(
    payload: RecipeChatRequest,
    request: Request,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> RecipeChatResponse:
    settings: Settings = getattr(request.app.state, "settings", None) or get_settings()
    return await chat_recipes(
        db,
        household_id=ctx.household.id,
        settings=settings,
        payload=payload,
    )


@router.get("/references", response_model=RecipeReferencesResponse)
async def recipe_references(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=5, ge=1, le=10),
    ctx: HouseholdContext = Depends(get_household_context),
) -> RecipeReferencesResponse:
    refs = await _invoke_discover_references(q, limit=limit)
    return RecipeReferencesResponse(items=refs)
