from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.tenancy import HouseholdContext, get_household_context
from app.modules.inventory.models import CanonicalItem
from app.modules.replenishment.engine import (
    compute_replenishment_recommendations,
    convert_compatible_quantity,
    invalidate_replenishment_cache,
    normalize_unit,
)
from app.modules.replenishment.models import ReplenishmentFeedback, ShoppingListItem
from app.modules.replenishment.schemas import (
    ReplenishmentFeedbackOut,
    ReplenishmentFeedbackRequest,
    ReplenishmentRecommendationsResponse,
    ShoppingListItemCreate,
    ShoppingListItemOut,
    ShoppingListItemUpdate,
    ShoppingListResponse,
    ShoppingListSourceEntry,
)

replenishment_router = APIRouter(prefix="/replenishment", tags=["replenishment"])
shopping_list_router = APIRouter(prefix="/shopping-list", tags=["shopping-list"])


def _shopping_item_to_out(
    row: ShoppingListItem,
    unit_conflict_note: str | None = None,
) -> ShoppingListItemOut:
    sources = [
        ShoppingListSourceEntry.model_validate(s)
        for s in (row.sources_json or [])
        if isinstance(s, dict)
    ]
    return ShoppingListItemOut(
        id=str(row.id),
        household_id=str(row.household_id),
        canonical_item_id=str(row.canonical_item_id) if row.canonical_item_id else None,
        item_name=row.item_name,
        quantity=float(row.quantity) if row.quantity is not None else None,
        unit=row.unit,
        primary_source=row.primary_source,  # type: ignore[arg-type]
        sources=sources,
        notes=row.notes,
        status=row.status,  # type: ignore[arg-type]
        unit_conflict_note=unit_conflict_note,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@replenishment_router.get(
    "/recommendations",
    response_model=ReplenishmentRecommendationsResponse,
)
async def get_replenishment_recommendations(
    upcoming_days: int = Query(default=7, ge=1, le=60),
    refresh: bool = Query(default=False),
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> ReplenishmentRecommendationsResponse:
    tz_name = getattr(ctx.household, "timezone", None) or "Asia/Kolkata"
    return await compute_replenishment_recommendations(
        db,
        ctx.household.id,
        tz_name,
        upcoming_days=upcoming_days,
        use_cache=not refresh,
    )


@replenishment_router.post(
    "/{canonical_item_id}/feedback",
    response_model=ReplenishmentFeedbackOut,
    status_code=status.HTTP_201_CREATED,
)
async def submit_replenishment_feedback(
    canonical_item_id: UUID,
    payload: ReplenishmentFeedbackRequest,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> ReplenishmentFeedbackOut:
    item = (
        await db.execute(
            select(CanonicalItem).where(
                CanonicalItem.id == canonical_item_id,
                CanonicalItem.household_id == ctx.household.id,
            )
        )
    ).scalars().first()
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Canonical item not found for this household.",
        )

    # Deactivate prior active feedback for this item so the latest decision governs
    await db.execute(
        update(ReplenishmentFeedback)
        .where(
            ReplenishmentFeedback.household_id == ctx.household.id,
            ReplenishmentFeedback.canonical_item_id == canonical_item_id,
            ReplenishmentFeedback.is_active.is_(True),
        )
        .values(is_active=False)
    )

    remind_after = None
    if payload.action == "remind_later":
        tz_name = getattr(ctx.household, "timezone", None) or "Asia/Kolkata"
        try:
            local_tz = ZoneInfo(tz_name)
        except Exception:
            local_tz = ZoneInfo("Asia/Kolkata")
        today_local = datetime.now(UTC).astimezone(local_tz).date()
        remind_after = today_local + timedelta(days=payload.remind_days or 3)

    fb = ReplenishmentFeedback(
        household_id=ctx.household.id,
        canonical_item_id=canonical_item_id,
        action=payload.action,
        remind_after=remind_after,
        is_active=True,
        reason_snapshot=payload.reason_snapshot,
        actor_user_id=ctx.user.id,
    )
    db.add(fb)
    await db.commit()
    await db.refresh(fb)
    invalidate_replenishment_cache(ctx.household.id)

    return ReplenishmentFeedbackOut(
        id=str(fb.id),
        canonical_item_id=str(fb.canonical_item_id),
        action=fb.action,  # type: ignore[arg-type]
        remind_after=fb.remind_after,
        is_active=fb.is_active,
        created_at=fb.created_at,
    )


@replenishment_router.post(
    "/{canonical_item_id}/restore",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def restore_replenishment_item(
    canonical_item_id: UUID,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> None:
    await db.execute(
        update(ReplenishmentFeedback)
        .where(
            ReplenishmentFeedback.household_id == ctx.household.id,
            ReplenishmentFeedback.canonical_item_id == canonical_item_id,
            ReplenishmentFeedback.is_active.is_(True),
        )
        .values(is_active=False)
    )
    await db.commit()
    invalidate_replenishment_cache(ctx.household.id)


@shopping_list_router.get("", response_model=ShoppingListResponse)
async def get_shopping_list(
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> ShoppingListResponse:
    rows = (
        await db.execute(
            select(ShoppingListItem)
            .where(ShoppingListItem.household_id == ctx.household.id)
            .order_by(ShoppingListItem.updated_at.desc(), ShoppingListItem.created_at.desc())
        )
    ).scalars().all()

    pending: list[ShoppingListItemOut] = []
    purchased: list[ShoppingListItemOut] = []
    dismissed: list[ShoppingListItemOut] = []
    for r in rows:
        out = _shopping_item_to_out(r)
        if r.status == "purchased":
            purchased.append(out)
        elif r.status == "dismissed":
            dismissed.append(out)
        else:
            pending.append(out)
    return ShoppingListResponse(pending=pending, purchased=purchased, dismissed=dismissed)


@shopping_list_router.post(
    "/items",
    response_model=ShoppingListItemOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_shopping_list_item(
    payload: ShoppingListItemCreate,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> ShoppingListItemOut:
    canonical_uuid: UUID | None = None
    if payload.canonical_item_id:
        try:
            canonical_uuid = UUID(payload.canonical_item_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Invalid canonical_item_id.",
            ) from exc
        c_item = (
            await db.execute(
                select(CanonicalItem).where(
                    CanonicalItem.id == canonical_uuid,
                    CanonicalItem.household_id == ctx.household.id,
                )
            )
        ).scalars().first()
        if c_item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Canonical item not found for this household.",
            )

    norm_unit = normalize_unit(payload.unit) if payload.unit else None
    new_source = {
        "source": payload.source,
        "ref_id": payload.source_ref_id,
        "added_qty": payload.quantity,
        "added_unit": norm_unit,
        "added_at": datetime.now(UTC).isoformat(),
    }

    if canonical_uuid is not None:
        existing = (
            await db.execute(
                select(ShoppingListItem).where(
                    ShoppingListItem.household_id == ctx.household.id,
                    ShoppingListItem.canonical_item_id == canonical_uuid,
                    ShoppingListItem.status == "pending",
                )
            )
        ).scalars().first()
        if existing is not None:
            sources = list(existing.sources_json or [])
            already_same_source = any(
                isinstance(s, dict)
                and s.get("source") == payload.source
                and s.get("ref_id") == payload.source_ref_id
                for s in sources
            )
            if not already_same_source:
                sources.append(new_source)
                existing.sources_json = sources

            unit_conflict_note: str | None = None
            if payload.quantity is not None:
                if existing.quantity is None:
                    existing.quantity = Decimal(str(payload.quantity))
                    existing.unit = norm_unit
                elif norm_unit is None or existing.unit is None or normalize_unit(existing.unit) == norm_unit:
                    existing.quantity = Decimal(str(float(existing.quantity) + payload.quantity))
                    if existing.unit is None:
                        existing.unit = norm_unit
                else:
                    converted = convert_compatible_quantity(
                        payload.quantity,
                        norm_unit,
                        existing.unit,
                    )
                    if converted is not None:
                        existing.quantity = Decimal(str(round(float(existing.quantity) + converted, 3)))
                    else:
                        unit_conflict_note = (
                            f"Added {payload.quantity} {norm_unit} ({payload.source}) could not be merged "
                            f"with existing {float(existing.quantity):g} {existing.unit}."
                        )
                        existing.notes = (
                            f"{existing.notes} | {unit_conflict_note}"
                            if existing.notes
                            else unit_conflict_note
                        )

            if payload.notes and not unit_conflict_note:
                existing.notes = (
                    f"{existing.notes} | {payload.notes}"
                    if existing.notes and payload.notes not in existing.notes
                    else payload.notes
                )

            await db.commit()
            await db.refresh(existing)
            return _shopping_item_to_out(existing, unit_conflict_note=unit_conflict_note)

    row = ShoppingListItem(
        household_id=ctx.household.id,
        canonical_item_id=canonical_uuid,
        item_name=payload.item_name.strip(),
        quantity=Decimal(str(payload.quantity)) if payload.quantity is not None else None,
        unit=norm_unit,
        primary_source=payload.source,
        sources_json=[new_source],
        notes=payload.notes,
        status="pending",
        created_by=ctx.user.id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _shopping_item_to_out(row)


@shopping_list_router.patch("/items/{item_id}", response_model=ShoppingListItemOut)
async def update_shopping_list_item(
    item_id: UUID,
    payload: ShoppingListItemUpdate,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> ShoppingListItemOut:
    row = (
        await db.execute(
            select(ShoppingListItem).where(
                ShoppingListItem.id == item_id,
                ShoppingListItem.household_id == ctx.household.id,
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Shopping list item not found.",
        )

    if payload.status is not None:
        row.status = payload.status
    if payload.quantity is not None:
        row.quantity = Decimal(str(payload.quantity))
    if payload.unit is not None:
        row.unit = normalize_unit(payload.unit)
    if payload.notes is not None:
        row.notes = payload.notes

    await db.commit()
    await db.refresh(row)
    return _shopping_item_to_out(row)


@shopping_list_router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_shopping_list_item(
    item_id: UUID,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> None:
    row = (
        await db.execute(
            select(ShoppingListItem).where(
                ShoppingListItem.id == item_id,
                ShoppingListItem.household_id == ctx.household.id,
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Shopping list item not found.",
        )
    await db.delete(row)
    await db.commit()
