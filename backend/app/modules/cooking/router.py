from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.tenancy import HouseholdContext, get_household_context
from app.modules.cooking.schemas import (
    CompleteCookingPlanRequest,
    CookingPlanListResponse,
    CookingPlanOut,
    CreateCookingPlanRequest,
)
from app.modules.cooking.service import (
    cancel_cooking_plan,
    complete_cooking_plan,
    create_cooking_plan,
    get_cooking_plan,
    list_cooking_plans,
)

router = APIRouter(prefix="/cooking-plans", tags=["cooking"])


@router.post("", response_model=CookingPlanOut, status_code=status.HTTP_201_CREATED)
async def start_cooking_plan(
    payload: CreateCookingPlanRequest,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> CookingPlanOut:
    return await create_cooking_plan(
        db,
        household_id=ctx.household.id,
        user_id=ctx.user.id,
        payload=payload,
    )


@router.get("", response_model=CookingPlanListResponse)
async def get_cooking_plans(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> CookingPlanListResponse:
    items, total = await list_cooking_plans(
        db,
        ctx.household.id,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )
    return CookingPlanListResponse(items=items, total=total)


@router.get("/{plan_id}", response_model=CookingPlanOut)
async def get_cooking_plan_detail(
    plan_id: UUID,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> CookingPlanOut:
    return await get_cooking_plan(db, ctx.household.id, plan_id)


@router.post("/{plan_id}/complete", response_model=CookingPlanOut)
async def complete_plan(
    plan_id: UUID,
    payload: CompleteCookingPlanRequest | None = Body(default=None),
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> CookingPlanOut:
    return await complete_cooking_plan(
        db,
        household_id=ctx.household.id,
        user_id=ctx.user.id,
        plan_id=plan_id,
        payload=payload,
    )


@router.post("/{plan_id}/cancel", response_model=CookingPlanOut)
async def cancel_plan(
    plan_id: UUID,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> CookingPlanOut:
    return await cancel_cooking_plan(
        db,
        household_id=ctx.household.id,
        plan_id=plan_id,
    )
