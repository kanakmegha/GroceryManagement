from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.tenancy import HouseholdContext, get_household_context
from app.modules.cooking.schemas import (
    MealFeedbackRequest,
    MealHistoryListResponse,
    MealHistoryOut,
)
from app.modules.cooking.service import (
    get_meal_history_detail,
    list_meal_history,
    update_meal_feedback,
)

router = APIRouter(prefix="/meals", tags=["meals"])


@router.get("/history", response_model=MealHistoryListResponse)
async def meal_history_list(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    meal_type: str | None = Query(default=None),
    min_rating: int | None = Query(default=None, ge=1, le=5),
    max_rating: int | None = Query(default=None, ge=1, le=5),
    would_make_again: bool | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> MealHistoryListResponse:
    return await list_meal_history(
        db,
        ctx.household.id,
        date_from=date_from,
        date_to=date_to,
        meal_type=meal_type,
        min_rating=min_rating,
        max_rating=max_rating,
        would_make_again=would_make_again,
        limit=limit,
        offset=offset,
    )


@router.get("/history/{meal_id}", response_model=MealHistoryOut)
async def meal_history_detail(
    meal_id: UUID,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> MealHistoryOut:
    return await get_meal_history_detail(db, ctx.household.id, meal_id)


@router.patch("/history/{meal_id}/feedback", response_model=MealHistoryOut)
async def meal_history_feedback(
    meal_id: UUID,
    payload: MealFeedbackRequest,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> MealHistoryOut:
    return await update_meal_feedback(
        db,
        household_id=ctx.household.id,
        meal_id=meal_id,
        payload=payload,
    )
