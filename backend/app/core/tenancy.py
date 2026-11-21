from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, Header
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import AppError
from app.core.security import ClerkClaims, get_current_claims
from app.modules.auth.models import Household, HouseholdMember, User


@dataclass(frozen=True, slots=True)
class HouseholdContext:
    user: User
    household: Household
    role: str


def _is_postgresql(db: AsyncSession) -> bool:
    bind = db.get_bind()
    return bind is not None and bind.dialect.name == "postgresql"


async def set_pg_rls_context(
    db: AsyncSession,
    user_id: UUID,
    household_id: UUID | None = None,
) -> None:
    if not _is_postgresql(db):
        return
    await db.execute(
        text("SELECT set_config('app.current_user_id', :uid, true)"),
        {"uid": str(user_id)},
    )
    if household_id is not None:
        await db.execute(
            text("SELECT set_config('app.current_household_id', :hid, true)"),
            {"hid": str(household_id)},
        )


async def upsert_clerk_user(db: AsyncSession, claims: ClerkClaims) -> User:
    if _is_postgresql(db):
        result = await db.execute(
            text(
                "SELECT upsert_clerk_user(:clerk_user_id, :email, :display_name)"
            ),
            {
                "clerk_user_id": claims.sub,
                "email": claims.email,
                "display_name": claims.display_name,
            },
        )
        user_id = result.scalar_one()
        await set_pg_rls_context(db, user_id)
        user_res = await db.execute(select(User).where(User.id == user_id))
        return user_res.scalar_one()

    stmt = select(User).where(User.clerk_user_id == claims.sub)
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        if claims.email is not None and existing.email != claims.email:
            existing.email = claims.email
            await db.flush()
        return existing

    user = User(
        clerk_user_id=claims.sub,
        email=claims.email,
        display_name=claims.display_name,
    )
    db.add(user)
    try:
        await db.flush()
        return user
    except IntegrityError:
        await db.rollback()
        existing = (await db.execute(stmt)).scalar_one()
        return existing


def parse_household_id_header(raw_header: str | None) -> UUID | None:
    if raw_header is None or not raw_header.strip():
        return None
    try:
        return UUID(raw_header.strip())
    except ValueError as exc:
        raise AppError(
            status_code=400,
            code="VALIDATION_ERROR",
            message="Invalid X-Household-Id header; expected a valid UUID.",
        ) from exc


async def get_current_user(
    claims: ClerkClaims = Depends(get_current_claims),
    db: AsyncSession = Depends(get_db),
) -> User:
    user = await upsert_clerk_user(db, claims)
    await set_pg_rls_context(db, user.id)
    return user


async def get_household_context(
    user: User = Depends(get_current_user),
    x_household_id: str | None = Header(default=None, alias="X-Household-Id"),
    db: AsyncSession = Depends(get_db),
) -> HouseholdContext:
    requested_id = parse_household_id_header(x_household_id)

    if requested_id is not None:
        stmt = (
            select(HouseholdMember, Household)
            .join(Household, Household.id == HouseholdMember.household_id)
            .where(
                HouseholdMember.user_id == user.id,
                HouseholdMember.household_id == requested_id,
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
    else:
        stmt = (
            select(HouseholdMember, Household)
            .join(Household, Household.id == HouseholdMember.household_id)
            .where(HouseholdMember.user_id == user.id)
            .order_by(HouseholdMember.joined_at.asc())
        )
        rows = (await db.execute(stmt)).all()
        if not rows:
            raise AppError(
                status_code=403,
                code="FORBIDDEN",
                message="User does not belong to any household.",
            )
        if len(rows) > 1:
            raise AppError(
                status_code=400,
                code="VALIDATION_ERROR",
                message="Multiple households found; X-Household-Id header is required.",
            )
        membership, household = rows[0]

    await set_pg_rls_context(db, user.id, household.id)
    return HouseholdContext(user=user, household=household, role=membership.role)
