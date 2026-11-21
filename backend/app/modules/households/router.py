from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import AppError
from app.core.tenancy import get_current_user, set_pg_rls_context
from app.modules.auth.models import Household, HouseholdMember, User
from app.modules.households.schemas import (
    CreateHouseholdRequest,
    HouseholdDetailOut,
    HouseholdItemOut,
    HouseholdListResponse,
)

router = APIRouter(prefix="/households", tags=["households"])


@router.get("", response_model=HouseholdListResponse)
async def list_households(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HouseholdListResponse:
    stmt = (
        select(HouseholdMember, Household)
        .join(Household, Household.id == HouseholdMember.household_id)
        .where(HouseholdMember.user_id == user.id)
        .order_by(HouseholdMember.joined_at.asc())
    )
    rows = (await db.execute(stmt)).all()
    items = [
        HouseholdItemOut(
            id=household.id,
            name=household.name,
            currency_code=household.currency_code,
            role=membership.role,
            joined_at=membership.joined_at,
        )
        for membership, household in rows
    ]
    return HouseholdListResponse(items=items)


@router.post(
    "",
    response_model=HouseholdDetailOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_household(
    payload: CreateHouseholdRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HouseholdDetailOut:
    household = Household(
        name=payload.name,
        currency_code=payload.currency_code,
        created_by=user.id,
    )
    db.add(household)
    await db.flush()

    membership = HouseholdMember(
        household_id=household.id,
        user_id=user.id,
        role="owner",
    )
    db.add(membership)
    await set_pg_rls_context(db, user.id, household.id)
    await db.commit()
    await db.refresh(household)

    return HouseholdDetailOut(
        id=household.id,
        name=household.name,
        currency_code=household.currency_code,
        role=membership.role,
    )


@router.get("/{household_id}", response_model=HouseholdDetailOut)
async def get_household(
    household_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HouseholdDetailOut:
    try:
        parsed_id = UUID(household_id)
    except ValueError as exc:
        raise AppError(
            status_code=400,
            code="VALIDATION_ERROR",
            message="Invalid household_id; expected a valid UUID.",
        ) from exc

    stmt = (
        select(HouseholdMember, Household)
        .join(Household, Household.id == HouseholdMember.household_id)
        .where(
            HouseholdMember.household_id == parsed_id,
            HouseholdMember.user_id == user.id,
        )
    )
    row = (await db.execute(stmt)).first()
    if row is None:
        raise AppError(
            status_code=403,
            code="FORBIDDEN",
            message="You are not a member of the requested household.",
        )

    membership, household = row
    await set_pg_rls_context(db, user.id, household.id)

    return HouseholdDetailOut(
        id=household.id,
        name=household.name,
        currency_code=household.currency_code,
        role=membership.role,
    )
