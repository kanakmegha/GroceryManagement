from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.tenancy import HouseholdContext, get_household_context
from app.modules.inventory.ingredient_aggregation_service import (
    get_ingredient_inventory_summary,
)
from app.modules.inventory.schemas import (
    AdjustLotRequest,
    AdjustLotResponse,
    CanonicalIngredientOptionOut,
    CanonicalIngredientsListResponse,
    CatalogItemOut,
    CatalogItemsResponse,
    ConfirmIngredientMappingRequest,
    ConfirmIngredientMappingResponse,
    CreateCatalogItemRequest,
    CreateInventoryLotRequest,
    IngredientInventorySummaryResponse,
    InventoryLotOut,
    InventoryLotsResponse,
    InventorySummaryResponse,
    RejectIngredientMappingRequest,
    RejectIngredientMappingResponse,
    StockLedgerResponse,
    UpdateCanonicalItemNameRequest,
    UpdateCanonicalItemNameResponse,
    UpdateIngredientPantryItemRequest,
    UpdateIngredientPantryItemResponse,
)
from app.modules.inventory.service import (
    adjust_lot,
    create_catalog_item,
    create_manual_lot,
    get_inventory_summary,
    list_catalog_items,
    list_inventory_lots,
    list_stock_ledger,
)

router = APIRouter(prefix="/inventory", tags=["inventory"])
catalog_router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.patch(
    "/ingredients/{canonical_ingredient_id}", response_model=UpdateIngredientPantryItemResponse
)
async def update_pantry_ingredient_item(
    canonical_ingredient_id: UUID,
    payload: UpdateIngredientPantryItemRequest,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> UpdateIngredientPantryItemResponse:
    """Directly edit a confirmed ingredient's display name and/or aggregate stock via
    StockLedger.
    """
    import re
    from datetime import UTC, datetime
    from decimal import ROUND_HALF_UP, Decimal

    from sqlalchemy import func, select

    from app.core.errors import AppError
    from app.modules.inventory.ingredient_aggregation_service import convert_to_target_unit
    from app.modules.inventory.models import (
        CanonicalIngredient,
        CanonicalItem,
        IngredientMapping,
        InventoryLot,
    )

    ing = await db.get(CanonicalIngredient, canonical_ingredient_id)
    if ing is None or not ing.is_active:
        raise AppError(status_code=404, code="NOT_FOUND", message="Ingredient not found.")

    # Verify household owns at least one confirmed mapping to this ingredient
    mapped_item_ids = list(
        (
            await db.execute(
                select(IngredientMapping.canonical_item_id)
                .join(CanonicalItem, CanonicalItem.id == IngredientMapping.canonical_item_id)
                .where(
                    IngredientMapping.household_id == ctx.household.id,
                    IngredientMapping.canonical_ingredient_id == canonical_ingredient_id,
                    IngredientMapping.user_confirmed.is_(True),
                    CanonicalItem.is_active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    if not mapped_item_ids:
        raise AppError(
            status_code=404,
            code="NOT_FOUND",
            message="Ingredient is not mapped in this household's pantry.",
        )

    # Validate name uniqueness before any mutation
    new_name_clean: str | None = None
    new_norm: str | None = None
    if payload.name is not None:
        candidate_name = payload.name.strip()
        if not candidate_name:
            raise AppError(
                status_code=422, code="VALIDATION_ERROR", message="Ingredient name cannot be empty."
            )
        if candidate_name.lower() != ing.name.lower():
            candidate_norm = re.sub(r"[^a-z0-9]+", "_", candidate_name.lower()).strip("_")
            dup = (
                await db.execute(
                    select(CanonicalIngredient).where(
                        CanonicalIngredient.id != ing.id,
                        (func.lower(CanonicalIngredient.name) == candidate_name.lower())
                        | (CanonicalIngredient.normalized_name == candidate_norm),
                    )
                )
            ).scalar_one_or_none()
            if dup is not None:
                raise AppError(
                    status_code=409,
                    code="DUPLICATE_INGREDIENT",
                    message=f"Another ingredient '{dup.name}' already exists.",
                )
            new_name_clean = candidate_name
            new_norm = candidate_norm
        elif candidate_name != ing.name:
            new_name_clean = candidate_name

    # Validate unit & quantity conversion before any mutation
    today_utc = datetime.now(UTC).date()
    lots = list(
        (
            await db.execute(
                select(InventoryLot)
                .where(
                    InventoryLot.household_id == ctx.household.id,
                    InventoryLot.canonical_item_id.in_(mapped_item_ids),
                    InventoryLot.lot_status == "active",
                    InventoryLot.remaining_qty > 0,
                )
                .order_by(
                    InventoryLot.expires_on.asc().nullslast(),
                    InventoryLot.purchased_at.asc(),
                    InventoryLot.id.asc(),
                )
            )
        )
        .scalars()
        .all()
    )
    active_lots = [
        lot for lot in lots if not (lot.expires_on is not None and lot.expires_on < today_utc)
    ]

    current_std_total = Decimal("0.000")
    for lot in active_lots:
        conv, _ = convert_to_target_unit(lot.remaining_qty, lot.base_unit, ing.standard_unit)
        if conv is None:
            raise AppError(
                status_code=422,
                code="INCOMPATIBLE_UNIT",
                message=(
                    f"Existing lot unit '{lot.base_unit}' is incompatible with "
                    f"'{ing.standard_unit}'."
                ),
            )
        current_std_total += conv
    current_std_total = current_std_total.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)

    target_std_qty: Decimal | None = None
    if payload.target_quantity is not None:
        req_unit = (payload.unit or ing.standard_unit).strip().lower()
        conv_target, _ = convert_to_target_unit(
            payload.target_quantity, req_unit, ing.standard_unit
        )
        if conv_target is None:
            raise AppError(
                status_code=422,
                code="INCOMPATIBLE_UNIT",
                message=(
                    f"Cannot convert '{req_unit}' to '{ing.standard_unit}' without a known "
                    "package size or conversion factor. Choose a compatible unit or perform "
                    "an explicit stock correction."
                ),
            )
        target_std_qty = conv_target.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)

    # Apply name change only after all unit & quantity validations succeed
    if new_name_clean is not None:
        ing.name = new_name_clean
    if new_norm is not None:
        ing.normalized_name = new_norm

    adjusted_count = 0
    if target_std_qty is not None and target_std_qty != current_std_total:
        diff = (target_std_qty - current_std_total).quantize(
            Decimal("0.001"), rounding=ROUND_HALF_UP
        )
        if not active_lots:
            raise AppError(
                status_code=422,
                code="NO_ACTIVE_LOT",
                message="No active inventory lot is available to adjust.",
            )
        if diff > Decimal("0"):
            newest = max(active_lots, key=lambda lot: (lot.purchased_at, str(lot.id)))
            lot_delta, _ = convert_to_target_unit(diff, ing.standard_unit, newest.base_unit)
            if lot_delta is None:
                raise AppError(
                    status_code=422,
                    code="INCOMPATIBLE_UNIT",
                    message=(
                        f"Cannot convert '{ing.standard_unit}' to lot unit '{newest.base_unit}'."
                    ),
                )
            await adjust_lot(
                db,
                household_id=ctx.household.id,
                actor_user_id=ctx.user.id,
                lot_id=newest.id,
                payload=AdjustLotRequest(
                    event_type="ADJUSTMENT_IN",
                    quantity_delta=lot_delta.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP),
                    notes="User-requested pantry quantity correction",
                ),
            )
            adjusted_count = 1
        else:
            remaining_to_deduct = -diff
            for lot in active_lots:
                if remaining_to_deduct <= Decimal("0"):
                    break
                lot_in_std, _ = convert_to_target_unit(
                    lot.remaining_qty, lot.base_unit, ing.standard_unit
                )
                if lot_in_std is None or lot_in_std <= Decimal("0"):
                    continue
                deduct_std = min(lot_in_std, remaining_to_deduct).quantize(
                    Decimal("0.001"), rounding=ROUND_HALF_UP
                )
                if deduct_std <= Decimal("0"):
                    continue
                deduct_lot_unit, _ = convert_to_target_unit(
                    deduct_std, ing.standard_unit, lot.base_unit
                )
                if deduct_lot_unit is None or deduct_lot_unit <= Decimal("0"):
                    continue
                deduct_lot_q = min(lot.remaining_qty, deduct_lot_unit).quantize(
                    Decimal("0.001"), rounding=ROUND_HALF_UP
                )
                await adjust_lot(
                    db,
                    household_id=ctx.household.id,
                    actor_user_id=ctx.user.id,
                    lot_id=lot.id,
                    payload=AdjustLotRequest(
                        event_type="ADJUSTMENT_OUT",
                        quantity_delta=-deduct_lot_q,
                        notes="User-requested pantry quantity correction",
                    ),
                )
                remaining_to_deduct -= deduct_std
                adjusted_count += 1
        final_total = target_std_qty
    else:
        final_total = current_std_total

    await db.commit()
    return UpdateIngredientPantryItemResponse(
        canonical_ingredient_id=ing.id,
        name=ing.name,
        standard_unit=ing.standard_unit,
        total_remaining_qty=final_total,
        adjusted_lots_count=adjusted_count,
    )


@router.patch("/items/{canonical_item_id}", response_model=UpdateCanonicalItemNameResponse)
async def update_pantry_product_name(
    canonical_item_id: UUID,
    payload: UpdateCanonicalItemNameRequest,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> UpdateCanonicalItemNameResponse:
    """Update a household product's display name without modifying raw OCR or inventory
    quantities.
    """
    from sqlalchemy import select

    from app.core.errors import AppError
    from app.modules.catalog.product_naming_service import standardize_product_name
    from app.modules.inventory.models import CanonicalItem

    stmt = select(CanonicalItem).where(
        CanonicalItem.id == canonical_item_id,
        CanonicalItem.household_id == ctx.household.id,
        CanonicalItem.is_active.is_(True),
    )
    item = (await db.execute(stmt)).scalar_one_or_none()
    if item is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="Product not found.")

    cleaned = standardize_product_name(payload.name).display_name or payload.name.strip()
    item.name = cleaned
    await db.commit()
    return UpdateCanonicalItemNameResponse(canonical_item_id=item.id, name=item.name)


@router.get("/items/{canonical_item_id}/price-history")
async def get_item_price_history(
    canonical_item_id: UUID,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return normalized comparable unit price history (₹/kg, ₹/L, ₹/pc) for a canonical item."""
    from sqlalchemy import or_, select

    from app.core.errors import AppError
    from app.modules.inventory.models import CanonicalItem
    from app.modules.purchases.models import Purchase, PurchaseItem

    item_stmt = select(CanonicalItem).where(
        CanonicalItem.id == canonical_item_id,
        or_(CanonicalItem.household_id == ctx.household.id, CanonicalItem.household_id.is_(None)),
    )
    canonical = (await db.execute(item_stmt)).scalar_one_or_none()
    if canonical is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="Catalog item not found.")

    rows_stmt = (
        select(PurchaseItem, Purchase)
        .join(Purchase, Purchase.id == PurchaseItem.purchase_id)
        .where(
            PurchaseItem.household_id == ctx.household.id,
            PurchaseItem.canonical_item_id == canonical_item_id,
            Purchase.status == "confirmed",
        )
        .order_by(Purchase.purchased_on.desc(), Purchase.created_at.desc())
    )
    rows = (await db.execute(rows_stmt)).all()

    base_unit = canonical.default_base_unit
    std_label = "₹/kg" if base_unit == "g" else ("₹/L" if base_unit == "ml" else "₹/pc")
    factor = 1000.0 if base_unit in ("g", "ml") else 1.0

    entries: list[dict] = []
    std_prices: list[int] = []
    for p_item, purchase in rows:
        qty_f = float(p_item.normalized_qty or 0)
        if qty_f > 0 and p_item.base_unit == base_unit:
            std_price = int(round((int(p_item.line_total_paise) / qty_f) * factor))
            std_prices.append(std_price)
        else:
            std_price = int(p_item.unit_price_paise or p_item.line_total_paise)
        entries.append(
            {
                "purchase_id": str(purchase.id),
                "merchant_name": purchase.merchant_name,
                "purchased_on": purchase.purchased_on.isoformat(),
                "purchased_qty": float(p_item.normalized_qty),
                "base_unit": p_item.base_unit,
                "pack_size": p_item.pack_size,
                "raw_text": p_item.raw_text,
                "unit_price_paise": int(p_item.unit_price_paise),
                "unit_price_provenance": p_item.unit_price_provenance,
                "normalized_std_price_paise": std_price,
                "line_total_paise": int(p_item.line_total_paise),
                "currency_code": purchase.currency_code,
            }
        )

    latest = std_prices[0] if std_prices else None
    previous = std_prices[1] if len(std_prices) >= 2 else None
    avg = int(round(sum(std_prices) / len(std_prices))) if std_prices else None
    change_paise = (latest - previous) if (latest is not None and previous is not None) else None
    change_pct = (
        round((change_paise / previous) * 100.0, 2)
        if (change_paise is not None and previous and previous > 0)
        else None
    )

    return {
        "canonical_item_id": str(canonical.id),
        "product_name": canonical.name,
        "base_unit": base_unit,
        "standard_unit_label": std_label,
        "current_price_paise_per_std": latest,
        "latest_price_paise_per_std": latest,
        "avg_price_paise_per_std": avg,
        "price_change_paise": change_paise,
        "price_change_pct": change_pct,
        "entries": entries,
    }


@router.get("/summary", response_model=InventorySummaryResponse)
async def inventory_summary(
    q: str | None = Query(default=None),
    category: str | None = Query(default=None),
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> InventorySummaryResponse:
    items = await get_inventory_summary(db, ctx.household.id, q=q, category=category)
    return InventorySummaryResponse(items=items)


@router.get("/ingredients/summary", response_model=IngredientInventorySummaryResponse)
async def ingredient_inventory_summary(
    q: str | None = Query(default=None),
    category: str | None = Query(default=None),
    include_zero_stock: bool = Query(default=False),
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> IngredientInventorySummaryResponse:
    return await get_ingredient_inventory_summary(
        db,
        ctx.household.id,
        q=q,
        category=category,
        include_zero_stock=include_zero_stock,
    )


@router.get("/ingredients/catalog", response_model=CanonicalIngredientsListResponse)
async def list_canonical_ingredients_catalog(
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> CanonicalIngredientsListResponse:
    from sqlalchemy import select

    from app.modules.inventory.models import CanonicalIngredient

    rows = (
        (
            await db.execute(
                select(CanonicalIngredient)
                .where(CanonicalIngredient.is_active.is_(True))
                .order_by(CanonicalIngredient.name.asc())
            )
        )
        .scalars()
        .all()
    )
    return CanonicalIngredientsListResponse(
        items=[
            CanonicalIngredientOptionOut(
                id=r.id,
                name=r.name,
                normalized_name=r.normalized_name,
                category=r.category,
                standard_unit=r.standard_unit,
            )
            for r in rows
        ]
    )


@router.post("/ingredients/mappings/confirm", response_model=ConfirmIngredientMappingResponse)
async def confirm_pantry_ingredient_mapping(
    payload: ConfirmIngredientMappingRequest,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> ConfirmIngredientMappingResponse:
    from app.modules.catalog.ingredient_mapping_service import confirm_ingredient_mapping
    from app.modules.inventory.models import CanonicalIngredient

    saved = await confirm_ingredient_mapping(
        db,
        household_id=ctx.household.id,
        canonical_item_id=payload.canonical_item_id,
        canonical_ingredient_id=payload.canonical_ingredient_id,
        mapping_source="user_confirmed",
        allow_override=payload.allow_override,
    )
    ing = await db.get(CanonicalIngredient, saved.canonical_ingredient_id)
    await db.commit()
    return ConfirmIngredientMappingResponse(
        mapping_id=saved.id,
        canonical_item_id=saved.canonical_item_id,
        canonical_ingredient_id=saved.canonical_ingredient_id,
        canonical_ingredient_name=ing.name if ing else "",
        user_confirmed=saved.user_confirmed,
        mapping_source=saved.mapping_source,
        matching_confidence=saved.matching_confidence,
    )


@router.post("/ingredients/mappings/reject", response_model=RejectIngredientMappingResponse)
async def reject_pantry_ingredient_mapping(
    payload: RejectIngredientMappingRequest,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> RejectIngredientMappingResponse:
    from sqlalchemy import delete

    from app.modules.inventory.models import IngredientMapping

    res = await db.execute(
        delete(IngredientMapping).where(
            IngredientMapping.household_id == ctx.household.id,
            IngredientMapping.canonical_item_id == payload.canonical_item_id,
            IngredientMapping.user_confirmed.is_(False),
        )
    )
    await db.commit()
    return RejectIngredientMappingResponse(
        canonical_item_id=payload.canonical_item_id,
        rejected=(res.rowcount or 0) > 0,
    )


@router.get("/lots", response_model=InventoryLotsResponse)
async def inventory_lots(
    canonical_item_id: UUID | None = Query(default=None),
    status: str | None = Query(default="active"),
    expiring_within_days: int | None = Query(default=None, ge=0, le=365),
    q: str | None = Query(default=None),
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> InventoryLotsResponse:
    items = await list_inventory_lots(
        db,
        ctx.household.id,
        canonical_item_id=canonical_item_id,
        status_filter=status,
        expiring_within_days=expiring_within_days,
        q=q,
    )
    return InventoryLotsResponse(items=items)


@router.post("/lots/{lot_id}/adjust", response_model=AdjustLotResponse)
async def adjust_inventory_lot(
    lot_id: UUID,
    payload: AdjustLotRequest,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> AdjustLotResponse:
    lot_out, ledger_out = await adjust_lot(
        db,
        household_id=ctx.household.id,
        actor_user_id=ctx.user.id,
        lot_id=lot_id,
        payload=payload,
    )
    return AdjustLotResponse(lot=lot_out, ledger_entry=ledger_out)


@router.get("/ledger", response_model=StockLedgerResponse)
async def inventory_ledger(
    canonical_item_id: UUID | None = Query(default=None),
    lot_id: UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> StockLedgerResponse:
    items = await list_stock_ledger(
        db,
        ctx.household.id,
        canonical_item_id=canonical_item_id,
        lot_id=lot_id,
        limit=limit,
        offset=offset,
    )
    return StockLedgerResponse(items=items)


@router.post("/lots", response_model=InventoryLotOut, status_code=status.HTTP_201_CREATED)
async def create_inventory_lot(
    payload: CreateInventoryLotRequest,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> InventoryLotOut:
    return await create_manual_lot(
        db,
        household_id=ctx.household.id,
        actor_user_id=ctx.user.id,
        payload=payload,
    )


@catalog_router.get("/items", response_model=CatalogItemsResponse)
async def get_catalog_items(
    q: str | None = Query(default=None),
    category: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> CatalogItemsResponse:
    items, total = await list_catalog_items(
        db,
        ctx.household.id,
        q=q,
        category=category,
        limit=limit,
        offset=offset,
    )
    return CatalogItemsResponse(items=items, total=total, limit=limit, offset=offset)


@catalog_router.post("/items", response_model=CatalogItemOut, status_code=status.HTTP_201_CREATED)
async def post_catalog_item(
    payload: CreateCatalogItemRequest,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> CatalogItemOut:
    return await create_catalog_item(
        db,
        household_id=ctx.household.id,
        payload=payload,
    )


@catalog_router.get("/duplicates/dry-run")
async def get_catalog_duplicates_dry_run(
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from dataclasses import asdict

    from app.modules.catalog.duplicate_audit import run_dry_run_duplicate_audit

    report = await run_dry_run_duplicate_audit(db, household_id=ctx.household.id)
    return asdict(report)
