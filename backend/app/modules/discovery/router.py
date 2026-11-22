from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.tenancy import HouseholdContext, get_household_context
from app.modules.discovery.matcher import match_dishes_for_household
from app.modules.discovery.schemas import DishMatchResponse

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
