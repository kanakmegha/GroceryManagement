from fastapi import APIRouter, Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import AppError
from app.core.tenancy import (
    get_current_user,
    parse_household_id_header,
    set_pg_rls_context,
)
from app.modules.auth.models import Household, HouseholdMember, User
from app.modules.auth.schemas import (
    AuthMeResponse,
    HouseholdSummaryOut,
    UpdateMeRequest,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me", response_model=AuthMeResponse)
async def get_me(
    user: User = Depends(get_current_user),
    x_household_id: str | None = Header(default=None, alias="X-Household-Id"),
    db: AsyncSession = Depends(get_db),
) -> AuthMeResponse:
    requested_id = parse_household_id_header(x_household_id)

    stmt = (
        select(HouseholdMember, Household)
        .join(Household, Household.id == HouseholdMember.household_id)
        .where(HouseholdMember.user_id == user.id)
        .order_by(HouseholdMember.joined_at.asc())
    )
    rows = (await db.execute(stmt)).all()

    if not rows:
        default_household = Household(
            name="My Kitchen",
            currency_code="INR",
            created_by=user.id,
        )
        db.add(default_household)
        await db.flush()

        default_membership = HouseholdMember(
            household_id=default_household.id,
            user_id=user.id,
            role="owner",
        )
        db.add(default_membership)
        await db.flush()
        rows = [(default_membership, default_household)]

    households = [
        HouseholdSummaryOut(
            id=household.id,
            name=household.name,
            currency_code=household.currency_code,
            role=membership.role,
        )
        for membership, household in rows
    ]

    if requested_id is not None:
        active = next((h for h in households if h.id == requested_id), None)
        if active is None:
            raise AppError(
                status_code=403,
                code="FORBIDDEN",
                message="You are not a member of the requested household.",
            )
    else:
        active = households[0]

    await set_pg_rls_context(db, user.id, active.id)
    await db.commit()
    await db.refresh(user)

    return AuthMeResponse(
        user=UserOut.model_validate(user),
        active_household=active,
        households=households,
    )


@router.patch("/me", response_model=UserOut)
async def update_me(
    payload: UpdateMeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    user.display_name = payload.display_name
    await db.commit()
    await db.refresh(user)
    return UserOut.model_validate(user)
