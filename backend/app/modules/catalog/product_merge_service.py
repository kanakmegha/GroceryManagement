"""Controlled, auditable, reversible CanonicalItem merge service (Phase 3B)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.inventory.models import (
    CanonicalItem,
    CanonicalMergeLog,
    IngredientMapping,
    InventoryLot,
    ItemAlias,
    StockLedger,
)
from app.modules.purchases.models import PurchaseItem


@dataclass
class ProductMergeDryRunReport:
    household_id: UUID
    survivor_id: UUID
    survivor_name: str
    duplicate_ids: list[UUID]
    duplicate_names: list[str]
    purchase_items_to_reassign: int = 0
    inventory_lots_to_reassign: int = 0
    stock_ledger_to_reassign: int = 0
    aliases_to_reassign: int = 0
    ingredient_mappings_to_reconcile: int = 0
    ingredient_mapping_conflicts: list[str] = field(default_factory=list)
    total_remaining_qty_before: Decimal = Decimal("0.000")
    total_remaining_qty_after: Decimal = Decimal("0.000")
    price_history_count_before: int = 0
    price_history_count_after: int = 0
    reassigned_purchase_item_ids: list[UUID] = field(default_factory=list)
    reassigned_lot_ids: list[UUID] = field(default_factory=list)
    reassigned_ledger_ids: list[UUID] = field(default_factory=list)


@dataclass
class ProductMergeResult:
    survivor_id: UUID
    merged_duplicate_ids: list[UUID]
    merge_log_ids: list[UUID]
    total_remaining_qty_after: Decimal
    price_history_count_after: int


async def _validate_merge_inputs(
    db: AsyncSession,
    *,
    household_id: UUID,
    survivor_id: UUID,
    duplicate_ids: list[UUID],
) -> tuple[CanonicalItem, list[CanonicalItem]]:
    if not duplicate_ids:
        raise AppError(
            status_code=422, code="VALIDATION_ERROR", message="duplicate_ids cannot be empty."
        )
    if survivor_id in duplicate_ids:
        raise AppError(
            status_code=422,
            code="VALIDATION_ERROR",
            message="survivor_id cannot be in duplicate_ids.",
        )

    survivor = await db.get(CanonicalItem, survivor_id)
    if survivor is None:
        raise AppError(
            status_code=404, code="NOT_FOUND", message="Survivor CanonicalItem not found."
        )
    if survivor.household_id != household_id:
        raise AppError(
            status_code=403,
            code="FORBIDDEN",
            message="Cross-household merge is forbidden.",
        )
    if not survivor.is_active or survivor.merged_into_id is not None:
        raise AppError(
            status_code=409,
            code="ALREADY_MERGED",
            message="Survivor CanonicalItem is inactive or already merged.",
        )

    duplicates: list[CanonicalItem] = []
    for dup_id in duplicate_ids:
        dup = await db.get(CanonicalItem, dup_id)
        if dup is None:
            raise AppError(
                status_code=404, code="NOT_FOUND", message=f"Duplicate {dup_id} not found."
            )
        if dup.household_id != household_id:
            raise AppError(
                status_code=403,
                code="FORBIDDEN",
                message="Cross-household merge is forbidden.",
            )
        if not dup.is_active or dup.merged_into_id is not None:
            raise AppError(
                status_code=409,
                code="ALREADY_MERGED",
                message=f"CanonicalItem {dup_id} was already merged.",
            )
        if dup.default_base_unit != survivor.default_base_unit:
            raise AppError(
                status_code=409,
                code="UNIT_MISMATCH",
                message=(
                    f"Incompatible units: {dup.default_base_unit} vs {survivor.default_base_unit}."
                ),
            )
        duplicates.append(dup)

    return survivor, duplicates


async def dry_run_product_merge(
    db: AsyncSession,
    *,
    household_id: UUID,
    survivor_id: UUID,
    duplicate_ids: list[UUID],
) -> ProductMergeDryRunReport:
    """Read-only dry-run report for consolidating duplicate CanonicalItems."""
    survivor, duplicates = await _validate_merge_inputs(
        db, household_id=household_id, survivor_id=survivor_id, duplicate_ids=duplicate_ids
    )
    all_ids = [survivor.id, *[d.id for d in duplicates]]

    # Totals before merge across survivor + duplicates
    qty_before = Decimal(
        str(
            (
                await db.execute(
                    select(func.coalesce(func.sum(InventoryLot.remaining_qty), 0)).where(
                        InventoryLot.household_id == household_id,
                        InventoryLot.canonical_item_id.in_(all_ids),
                    )
                )
            ).scalar_one()
        )
    )
    pi_before = int(
        (
            await db.execute(
                select(func.count(PurchaseItem.id)).where(
                    PurchaseItem.household_id == household_id,
                    PurchaseItem.canonical_item_id.in_(all_ids),
                )
            )
        ).scalar_one()
    )

    pi_ids = list(
        (
            await db.execute(
                select(PurchaseItem.id).where(
                    PurchaseItem.household_id == household_id,
                    PurchaseItem.canonical_item_id.in_(duplicate_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    lot_ids = list(
        (
            await db.execute(
                select(InventoryLot.id).where(
                    InventoryLot.household_id == household_id,
                    InventoryLot.canonical_item_id.in_(duplicate_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    led_ids = list(
        (
            await db.execute(
                select(StockLedger.id).where(
                    StockLedger.household_id == household_id,
                    StockLedger.canonical_item_id.in_(duplicate_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    alias_cnt = int(
        (
            await db.execute(
                select(func.count(ItemAlias.id)).where(
                    ItemAlias.household_id == household_id,
                    ItemAlias.canonical_item_id.in_(duplicate_ids),
                )
            )
        ).scalar_one()
    )

    surv_map = (
        await db.execute(
            select(IngredientMapping).where(
                IngredientMapping.household_id == household_id,
                IngredientMapping.canonical_item_id == survivor.id,
            )
        )
    ).scalar_one_or_none()
    dup_maps = list(
        (
            await db.execute(
                select(IngredientMapping).where(
                    IngredientMapping.household_id == household_id,
                    IngredientMapping.canonical_item_id.in_(duplicate_ids),
                )
            )
        )
        .scalars()
        .all()
    )

    conflicts: list[str] = []
    seen_ing = surv_map.canonical_ingredient_id if surv_map else None
    for dm in dup_maps:
        if seen_ing is None:
            seen_ing = dm.canonical_ingredient_id
        elif dm.canonical_ingredient_id != seen_ing:
            conflicts.append(
                f"CanonicalItem {dm.canonical_item_id} maps to "
                f"{dm.canonical_ingredient_id} != {seen_ing}"
            )

    return ProductMergeDryRunReport(
        household_id=household_id,
        survivor_id=survivor.id,
        survivor_name=survivor.name,
        duplicate_ids=[d.id for d in duplicates],
        duplicate_names=[d.name for d in duplicates],
        purchase_items_to_reassign=len(pi_ids),
        inventory_lots_to_reassign=len(lot_ids),
        stock_ledger_to_reassign=len(led_ids),
        aliases_to_reassign=alias_cnt,
        ingredient_mappings_to_reconcile=len(dup_maps),
        ingredient_mapping_conflicts=conflicts,
        total_remaining_qty_before=qty_before,
        total_remaining_qty_after=qty_before,
        price_history_count_before=pi_before,
        price_history_count_after=pi_before,
        reassigned_purchase_item_ids=pi_ids,
        reassigned_lot_ids=lot_ids,
        reassigned_ledger_ids=led_ids,
    )


async def merge_canonical_products(
    db: AsyncSession,
    *,
    household_id: UUID,
    survivor_id: UUID,
    duplicate_ids: list[UUID],
    merged_by: UUID | None = None,
    confidence: Decimal = Decimal("0.980"),
) -> ProductMergeResult:
    """Execute a soft-merge of duplicate CanonicalItems into survivor_id."""
    survivor, duplicates = await _validate_merge_inputs(
        db, household_id=household_id, survivor_id=survivor_id, duplicate_ids=duplicate_ids
    )

    surv_map = (
        await db.execute(
            select(IngredientMapping).where(
                IngredientMapping.household_id == household_id,
                IngredientMapping.canonical_item_id == survivor.id,
            )
        )
    ).scalar_one_or_none()

    merge_log_ids: list[UUID] = []

    for dup in duplicates:
        # 1. Reconcile IngredientMapping without conflicts
        dup_map = (
            await db.execute(
                select(IngredientMapping).where(
                    IngredientMapping.household_id == household_id,
                    IngredientMapping.canonical_item_id == dup.id,
                )
            )
        ).scalar_one_or_none()
        if dup_map is not None:
            if surv_map is None:
                dup_map.canonical_item_id = survivor.id
                surv_map = dup_map
            elif surv_map.canonical_ingredient_id == dup_map.canonical_ingredient_id:
                if dup_map.user_confirmed and not surv_map.user_confirmed:
                    surv_map.user_confirmed = True
                    surv_map.mapping_source = dup_map.mapping_source
                await db.delete(dup_map)
            else:
                raise AppError(
                    status_code=409,
                    code="MAPPING_CONFLICT",
                    message=(
                        f"Ingredient mapping conflict between survivor {survivor.id} "
                        f"and duplicate {dup.id}."
                    ),
                )

        # 2. Reassign PurchaseItems, InventoryLots, StockLedger, ItemAliases (preserve IDs & qty)
        pis = list(
            (
                await db.execute(
                    select(PurchaseItem).where(
                        PurchaseItem.household_id == household_id,
                        PurchaseItem.canonical_item_id == dup.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        for pi in pis:
            pi.canonical_item_id = survivor.id

        lots = list(
            (
                await db.execute(
                    select(InventoryLot).where(
                        InventoryLot.household_id == household_id,
                        InventoryLot.canonical_item_id == dup.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        for lot in lots:
            lot.canonical_item_id = survivor.id

        ledgers = list(
            (
                await db.execute(
                    select(StockLedger).where(
                        StockLedger.household_id == household_id,
                        StockLedger.canonical_item_id == dup.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        for led in ledgers:
            led.canonical_item_id = survivor.id

        aliases = list(
            (
                await db.execute(
                    select(ItemAlias).where(
                        ItemAlias.household_id == household_id,
                        ItemAlias.canonical_item_id == dup.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        reassigned_alias_ids: list[str] = []
        for al in aliases:
            al.canonical_item_id = survivor.id
            reassigned_alias_ids.append(str(al.id))

        # 3. Soft-merge duplicate (never delete) & write audit log
        dup.merged_into_id = survivor.id
        dup.is_active = False

        evidence: dict[str, Any] = {
            "duplicate_name": dup.name,
            "survivor_name": survivor.name,
            "reassigned_purchase_item_ids": [str(pi.id) for pi in pis],
            "reassigned_lot_ids": [str(lot.id) for lot in lots],
            "reassigned_ledger_ids": [str(led.id) for led in ledgers],
            "reassigned_alias_ids": reassigned_alias_ids,
        }
        log = CanonicalMergeLog(
            household_id=household_id,
            duplicate_canonical_id=dup.id,
            survivor_canonical_id=survivor.id,
            evidence_json=evidence,
            confidence=confidence,
            merged_by=merged_by,
        )
        db.add(log)
        await db.flush()
        merge_log_ids.append(log.id)

    qty_after = Decimal(
        str(
            (
                await db.execute(
                    select(func.coalesce(func.sum(InventoryLot.remaining_qty), 0)).where(
                        InventoryLot.household_id == household_id,
                        InventoryLot.canonical_item_id == survivor.id,
                    )
                )
            ).scalar_one()
        )
    )
    pi_after = int(
        (
            await db.execute(
                select(func.count(PurchaseItem.id)).where(
                    PurchaseItem.household_id == household_id,
                    PurchaseItem.canonical_item_id == survivor.id,
                )
            )
        ).scalar_one()
    )

    return ProductMergeResult(
        survivor_id=survivor.id,
        merged_duplicate_ids=[d.id for d in duplicates],
        merge_log_ids=merge_log_ids,
        total_remaining_qty_after=qty_after,
        price_history_count_after=pi_after,
    )


async def rollback_canonical_product_merge(
    db: AsyncSession,
    *,
    household_id: UUID,
    merge_log_id: UUID,
) -> None:
    """Reverse a recorded CanonicalMergeLog entry using stored evidence_json."""
    log = await db.get(CanonicalMergeLog, merge_log_id)
    if log is None or log.household_id != household_id:
        raise AppError(status_code=404, code="NOT_FOUND", message="Merge log not found.")
    if log.reversed_at is not None:
        raise AppError(status_code=409, code="ALREADY_REVERSED", message="Merge already reversed.")

    dup = await db.get(CanonicalItem, log.duplicate_canonical_id)
    if dup is None:
        raise AppError(
            status_code=404, code="NOT_FOUND", message="Duplicate CanonicalItem not found."
        )

    ev = log.evidence_json or {}
    for pi_id_str in ev.get("reassigned_purchase_item_ids", []):
        pi = await db.get(PurchaseItem, UUID(pi_id_str))
        if pi is not None:
            pi.canonical_item_id = dup.id
    for lot_id_str in ev.get("reassigned_lot_ids", []):
        lot = await db.get(InventoryLot, UUID(lot_id_str))
        if lot is not None:
            lot.canonical_item_id = dup.id
    for led_id_str in ev.get("reassigned_ledger_ids", []):
        led = await db.get(StockLedger, UUID(led_id_str))
        if led is not None:
            led.canonical_item_id = dup.id
    for al_id_str in ev.get("reassigned_alias_ids", []):
        al = await db.get(ItemAlias, UUID(al_id_str))
        if al is not None:
            al.canonical_item_id = dup.id

    dup.merged_into_id = None
    dup.is_active = True
    log.reversed_at = datetime.now(UTC)
    await db.flush()
