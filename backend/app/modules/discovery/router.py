from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import AppError
from app.core.tenancy import HouseholdContext, get_household_context
from app.modules.discovery.daily_scheduler import (
    MANUAL_REGENERATION_DAILY_LIMIT,
    run_daily_plan_for_household,
)
from app.modules.discovery.daily_schemas import DailyMealCategory, DailyMealPlanResponse
from app.modules.discovery.daily_service import get_daily_plan_for_household
from app.modules.discovery.matcher import match_dishes_for_household
from app.modules.discovery.schemas import DishMatchResponse
from app.modules.recipes.service import is_household_preferences_completed

router = APIRouter(prefix="/discover", tags=["discover"])


@router.get("/dishes", response_model=DishMatchResponse)
async def list_discover_dishes(
    include_hidden: bool = Query(default=False),
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> DishMatchResponse:
    """Return read-only pantry-matched dish recommendations for the active household."""
    return await match_dishes_for_household(
        db,
        ctx.household.id,
        include_hidden=include_hidden,
    )


@router.get("/daily-plan", response_model=DailyMealPlanResponse)
async def get_daily_meal_plan(
    category: DailyMealCategory | None = Query(default=None),
    refresh_availability: bool = Query(default=False),
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> DailyMealPlanResponse:
    """Retrieve the household's stored daily 8-recipe meal plan (never triggers LLM)."""
    return await get_daily_plan_for_household(
        db,
        ctx.household.id,
        category=category,
        recompute_availability=refresh_availability,
        persist_availability=False,
    )


@router.post("/daily-plan/refresh-availability", response_model=DailyMealPlanResponse)
async def refresh_daily_plan_availability(
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> DailyMealPlanResponse:
    """Recompute pantry availability for the stored daily plan without regenerating recipes."""
    return await get_daily_plan_for_household(
        db,
        ctx.household.id,
        recompute_availability=True,
        persist_availability=True,
    )


@router.post("/daily-plan/regenerate", response_model=DailyMealPlanResponse)
async def regenerate_daily_meal_plan(
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> DailyMealPlanResponse:
    """Explicitly user-triggered regeneration (bounded to 1 per household per local day)."""
    if not await is_household_preferences_completed(db, ctx.household.id):
        raise AppError(
            status_code=409,
            code="PREFERENCES_REQUIRED",
            message="Save your household food preferences before generating a daily meal plan.",
        )
    plan = await run_daily_plan_for_household(
        db,
        ctx.household.id,
        is_manual_regeneration=True,
    )
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Daily manual regeneration limit ({MANUAL_REGENERATION_DAILY_LIMIT}/day) "
                "reached or generation is currently in progress."
            ),
        )
    return await get_daily_plan_for_household(db, ctx.household.id)
