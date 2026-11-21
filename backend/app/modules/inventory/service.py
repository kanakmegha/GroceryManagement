import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.inventory.models import CanonicalItem, InventoryLot, StockLedger
from app.modules.inventory.schemas import (
    AdjustLotRequest,
    CatalogItemOut,
    CreateCatalogItemRequest,
    CreateInventoryLotRequest,
    InventoryLotOut,
    InventorySummaryItemOut,
    StockLedgerEntryOut,
)


@dataclass(frozen=True, slots=True)
class FEFODeduction:
    lot_id: UUID
    canonical_item_id: UUID
    deducted_qty: Decimal
    base_unit: str
    ledger_entry_id: UUID


def _is_postgresql(db: AsyncSession) -> bool:
    bind = db.get_bind()
    return bind is not None and bind.dialect.name == "postgresql"


async def allocate_fefo(
    db: AsyncSession,
    *,
    household_id: UUID,
    canonical_item_id: UUID,
    required_qty: Decimal,
    actor_user_id: UUID,
    event_type: str = "COOK_OUT",
    reference_type: str | None = None,
    reference_id: UUID | None = None,
    notes: str | None = None,
    today: date | None = None,
) -> list[FEFODeduction]:
    """Allocate and deduct `required_qty` across active non-expired lots in FEFO order."""
    if required_qty <= Decimal("0"):
        return []

    ref_today = today or datetime.now(UTC).date()

    if _is_postgresql(db):
        lock_key = f"{household_id}:{canonical_item_id}"
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": lock_key},
        )

    stmt = (
        select(InventoryLot)
        .where(
            InventoryLot.household_id == household_id,
            InventoryLot.canonical_item_id == canonical_item_id,
            InventoryLot.lot_status == "active",
            InventoryLot.remaining_qty > Decimal("0"),
            or_(InventoryLot.expires_on.is_(None), InventoryLot.expires_on >= ref_today),
        )
        .order_by(
            InventoryLot.expires_on.asc().nulls_last(),
            InventoryLot.purchased_at.asc(),
            InventoryLot.id.asc(),
        )
    )
    if _is_postgresql(db):
        stmt = stmt.with_for_update()

    lots = list((await db.execute(stmt)).scalars().all())
    total_available = sum((lot.remaining_qty for lot in lots), Decimal("0"))
    if total_available < required_qty:
        raise AppError(
            status_code=409,
            code="INSUFFICIENT_STOCK",
            message=(
                f"Insufficient active stock for item {canonical_item_id}: "
                f"requested {required_qty}, available {total_available}."
            ),
            details={
                "canonical_item_id": str(canonical_item_id),
                "requested_qty": str(required_qty),
                "available_qty": str(total_available),
            },
        )

    remaining_to_deduct = required_qty
    deductions: list[FEFODeduction] = []

    for lot in lots:
        if remaining_to_deduct <= Decimal("0"):
            break
        take = min(lot.remaining_qty, remaining_to_deduct)
        lot.remaining_qty = lot.remaining_qty - take
        if lot.remaining_qty == Decimal("0"):
            lot.lot_status = "depleted"

        ledger_entry = StockLedger(
            household_id=household_id,
            lot_id=lot.id,
            canonical_item_id=canonical_item_id,
            event_type=event_type,
            quantity_delta=-take,
            base_unit=lot.base_unit,
            balance_after_lot=lot.remaining_qty,
            unit_cost_paise=lot.unit_cost_paise,
            reference_type=reference_type,
            reference_id=reference_id,
            actor_user_id=actor_user_id,
            notes=notes,
        )
        db.add(ledger_entry)
        await db.flush()

        deductions.append(
            FEFODeduction(
                lot_id=lot.id,
                canonical_item_id=canonical_item_id,
                deducted_qty=take,
                base_unit=lot.base_unit,
                ledger_entry_id=ledger_entry.id,
            )
        )
        remaining_to_deduct -= take

    return deductions


async def get_inventory_summary(
    db: AsyncSession,
    household_id: UUID,
    *,
    q: str | None = None,
    category: str | None = None,
    today: date | None = None,
) -> list[InventorySummaryItemOut]:
    ref_today = today or datetime.now(UTC).date()
    stmt = (
        select(InventoryLot, CanonicalItem)
        .join(CanonicalItem, CanonicalItem.id == InventoryLot.canonical_item_id)
        .where(
            InventoryLot.household_id == household_id,
            InventoryLot.lot_status == "active",
            InventoryLot.remaining_qty > Decimal("0"),
        )
    )
    if q and q.strip():
        pattern = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                CanonicalItem.name.ilike(pattern),
                CanonicalItem.slug.ilike(pattern),
            )
        )
    if category and category.strip():
        stmt = stmt.where(CanonicalItem.category == category.strip().lower())

    rows = (await db.execute(stmt)).all()

    # Preload household/global items to resolve any soft-merged CanonicalItem pointers safely
    all_items_by_id: dict[UUID, CanonicalItem] = {
        it.id: it
        for it in (
            await db.execute(
                select(CanonicalItem).where(
                    or_(CanonicalItem.household_id == household_id, CanonicalItem.household_id.is_(None))
                )
            )
        ).scalars().all()
    }

    def _active_survivor(item_obj: CanonicalItem) -> CanonicalItem | None:
        cur: CanonicalItem | None = item_obj
        seen: set[UUID] = set()
        while cur is not None and (not cur.is_active or cur.merged_into_id is not None):
            if cur.merged_into_id is None or cur.id in seen:
                return None
            seen.add(cur.id)
            cur = all_items_by_id.get(cur.merged_into_id)
        return cur if (cur is not None and cur.is_active) else None

    grouped: dict[UUID, dict[str, object]] = {}
    seen_lot_ids: set[UUID] = set()
    for lot, raw_item in rows:
        if lot.id in seen_lot_ids:
            continue
        seen_lot_ids.add(lot.id)
        item = _active_survivor(raw_item)
        if item is None:
            continue
        is_expired = lot.expires_on is not None and lot.expires_on < ref_today
        entry = grouped.get(item.id)
        if entry is None:
            entry = {
                "canonical_item_id": item.id,
                "name": item.name,
                "slug": item.slug,
                "category": item.category,
                "secondary_category": item.secondary_category,
                "total_remaining_qty": Decimal("0"),
                "base_unit": item.default_base_unit,
                "active_lot_count": 0,
                "earliest_expires_on": None,
                "has_expired_lots": False,
            }
            grouped[item.id] = entry

        if is_expired:
            entry["has_expired_lots"] = True
        else:
            entry["total_remaining_qty"] = (
                entry["total_remaining_qty"] + lot.remaining_qty  # type: ignore[operator]
            )
            entry["active_lot_count"] = int(entry["active_lot_count"]) + 1  # type: ignore[arg-type]
            if lot.expires_on is not None:
                cur_earliest = entry["earliest_expires_on"]
                if cur_earliest is None or lot.expires_on < cur_earliest:  # type: ignore[operator]
                    entry["earliest_expires_on"] = lot.expires_on

    result = [
        InventorySummaryItemOut(**data)  # type: ignore[arg-type]
        for data in grouped.values()
        if data["active_lot_count"] > 0 or data["has_expired_lots"]  # type: ignore[operator]
    ]
    result.sort(key=lambda x: x.name.lower())
    return result


async def list_inventory_lots(
    db: AsyncSession,
    household_id: UUID,
    *,
    canonical_item_id: UUID | None = None,
    status_filter: str | None = "active",
    expiring_within_days: int | None = None,
    q: str | None = None,
    today: date | None = None,
) -> list[InventoryLotOut]:
    ref_today = today or datetime.now(UTC).date()
    stmt = (
        select(InventoryLot, CanonicalItem.name)
        .join(CanonicalItem, CanonicalItem.id == InventoryLot.canonical_item_id)
        .where(InventoryLot.household_id == household_id)
    )
    if canonical_item_id is not None:
        stmt = stmt.where(InventoryLot.canonical_item_id == canonical_item_id)
    if status_filter:
        stmt = stmt.where(InventoryLot.lot_status == status_filter)
    if expiring_within_days is not None:
        cutoff = ref_today + timedelta(days=expiring_within_days)
        stmt = stmt.where(
            and_(
                InventoryLot.expires_on.is_not(None),
                InventoryLot.expires_on <= cutoff,
                InventoryLot.expires_on >= ref_today,
            )
        )
    if q and q.strip():
        pattern = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                CanonicalItem.name.ilike(pattern),
                CanonicalItem.slug.ilike(pattern),
            )
        )

    stmt = stmt.order_by(
        InventoryLot.expires_on.asc().nulls_last(),
        InventoryLot.purchased_at.asc(),
        InventoryLot.id.asc(),
    )
    rows = (await db.execute(stmt)).all()
    out: list[InventoryLotOut] = []
    for lot, item_name in rows:
        lot_out = InventoryLotOut.model_validate(lot)
        lot_out.canonical_item_name = item_name
        lot_out.item_name = item_name
        lot_out.status = lot.lot_status
        lot_out.received_at = lot.purchased_at
        out.append(lot_out)
    return out


async def adjust_lot(
    db: AsyncSession,
    *,
    household_id: UUID,
    actor_user_id: UUID,
    lot_id: UUID,
    payload: AdjustLotRequest,
) -> tuple[InventoryLotOut, StockLedgerEntryOut]:
    stmt = select(InventoryLot).where(
        InventoryLot.id == lot_id,
        InventoryLot.household_id == household_id,
    )
    if _is_postgresql(db):
        stmt = stmt.with_for_update()
    lot = (await db.execute(stmt)).scalar_one_or_none()
    if lot is None:
        raise AppError(
            status_code=404,
            code="NOT_FOUND",
            message="Inventory lot not found.",
        )

    delta = payload.quantity_delta
    if delta == Decimal("0"):
        raise AppError(
            status_code=422,
            code="VALIDATION_ERROR",
            message="quantity_delta must be non-zero.",
        )

    if payload.event_type == "ADJUSTMENT_IN" and delta <= Decimal("0"):
        raise AppError(
            status_code=422,
            code="VALIDATION_ERROR",
            message="ADJUSTMENT_IN requires a positive quantity_delta.",
        )
    if payload.event_type in ("ADJUSTMENT_OUT", "WASTE_OUT") and delta >= Decimal("0"):
        raise AppError(
            status_code=422,
            code="VALIDATION_ERROR",
            message=f"{payload.event_type} requires a negative quantity_delta.",
        )

    new_remaining = lot.remaining_qty + delta
    if new_remaining < Decimal("0"):
        raise AppError(
            status_code=409,
            code="INSUFFICIENT_STOCK",
            message=(
                f"Cannot deduct {-delta} from lot {lot.id}; "
                f"only {lot.remaining_qty} remaining."
            ),
        )

    if new_remaining > lot.initial_qty:
        lot.initial_qty = new_remaining
    lot.remaining_qty = new_remaining
    if new_remaining == Decimal("0"):
        lot.lot_status = "written_off" if payload.event_type == "WASTE_OUT" else "depleted"
    elif lot.lot_status in ("depleted", "written_off"):
        lot.lot_status = "active"

    ledger_entry = StockLedger(
        household_id=household_id,
        lot_id=lot.id,
        canonical_item_id=lot.canonical_item_id,
        event_type=payload.event_type,
        quantity_delta=delta,
        base_unit=lot.base_unit,
        balance_after_lot=lot.remaining_qty,
        unit_cost_paise=lot.unit_cost_paise,
        reference_type="spoilage" if payload.event_type == "WASTE_OUT" else "manual_adjustment",
        reference_id=lot.id,
        actor_user_id=actor_user_id,
        notes=payload.notes,
    )
    db.add(ledger_entry)
    await db.commit()
    await db.refresh(lot)
    await db.refresh(ledger_entry)

    return InventoryLotOut.model_validate(lot), StockLedgerEntryOut.model_validate(ledger_entry)


async def list_stock_ledger(
    db: AsyncSession,
    household_id: UUID,
    *,
    canonical_item_id: UUID | None = None,
    lot_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[StockLedgerEntryOut]:
    stmt = select(StockLedger).where(StockLedger.household_id == household_id)
    if canonical_item_id is not None:
        stmt = stmt.where(StockLedger.canonical_item_id == canonical_item_id)
    if lot_id is not None:
        stmt = stmt.where(StockLedger.lot_id == lot_id)
    stmt = stmt.order_by(StockLedger.created_at.desc()).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    return [StockLedgerEntryOut.model_validate(row) for row in rows]


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "item"


async def list_catalog_items(
    db: AsyncSession,
    household_id: UUID,
    *,
    q: str | None = None,
    category: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[CatalogItemOut], int]:
    filters = [
        CanonicalItem.is_active.is_(True),
        or_(CanonicalItem.household_id == household_id, CanonicalItem.household_id.is_(None)),
    ]
    if q and q.strip():
        pattern = f"%{q.strip().lower()}%"
        filters.append(or_(CanonicalItem.name.ilike(pattern), CanonicalItem.slug.ilike(pattern)))
    if category and category.strip():
        filters.append(CanonicalItem.category == category.strip().lower())

    total = int(
        (await db.execute(select(func.count()).select_from(CanonicalItem).where(*filters)))
        .scalar_one()
    )
    stmt = (
        select(CanonicalItem)
        .where(*filters)
        .order_by(CanonicalItem.name.asc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(stmt)).scalars().all()
    items = []
    for row in rows:
        out = CatalogItemOut.model_validate(row)
        out.is_custom = row.household_id is not None
        items.append(out)
    return items, total


async def create_catalog_item(
    db: AsyncSession,
    *,
    household_id: UUID,
    payload: CreateCatalogItemRequest,
) -> CatalogItemOut:
    from app.modules.catalog.product_identity_service import get_or_create_canonical_item

    item, _res, _created = await get_or_create_canonical_item(
        db,
        household_id=household_id,
        raw_name=payload.name.strip(),
        category=payload.category.strip().lower(),
        default_base_unit=payload.default_base_unit,
        default_shelf_life_days=payload.default_shelf_life_days,
        low_stock_threshold_qty=payload.low_stock_threshold_qty,
        slug_override=_slugify(payload.name),
    )
    await db.commit()
    await db.refresh(item)
    out = CatalogItemOut.model_validate(item)
    out.is_custom = True
    return out


async def create_manual_lot(
    db: AsyncSession,
    *,
    household_id: UUID,
    actor_user_id: UUID,
    payload: CreateInventoryLotRequest,
) -> InventoryLotOut:
    item = (
        await db.execute(
            select(CanonicalItem).where(
                CanonicalItem.id == payload.canonical_item_id,
                or_(
                    CanonicalItem.household_id == household_id,
                    CanonicalItem.household_id.is_(None),
                ),
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="Canonical item not found.")

    now = datetime.now(UTC)
    lot = InventoryLot(
        household_id=household_id,
        canonical_item_id=item.id,
        purchase_item_id=None,
        initial_qty=payload.remaining_qty,
        remaining_qty=payload.remaining_qty,
        base_unit=payload.base_unit,
        unit_cost_paise=payload.unit_cost_paise,
        purchased_at=now,
        expires_on=payload.expires_on,
        lot_status="active",
    )
    db.add(lot)
    await db.flush()

    ledger = StockLedger(
        household_id=household_id,
        lot_id=lot.id,
        canonical_item_id=item.id,
        event_type="ADJUSTMENT_IN",
        quantity_delta=payload.remaining_qty,
        base_unit=payload.base_unit,
        balance_after_lot=lot.remaining_qty,
        unit_cost_paise=payload.unit_cost_paise,
        reference_type="manual_adjustment",
        reference_id=lot.id,
        actor_user_id=actor_user_id,
        notes="Manual lot entry",
    )
    db.add(ledger)
    await db.commit()
    await db.refresh(lot)

    out = InventoryLotOut.model_validate(lot)
    out.canonical_item_name = item.name
    out.item_name = item.name
    out.status = lot.lot_status
    out.received_at = lot.purchased_at
    return out
