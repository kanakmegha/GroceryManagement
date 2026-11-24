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


_UNIT_FAMILY_SCALE: dict[str, tuple[str, Decimal]] = {
    "g": ("mass", Decimal("1")),
    "kg": ("mass", Decimal("1000")),
    "ml": ("volume", Decimal("1")),
    "l": ("volume", Decimal("1000")),
    "pcs": ("count", Decimal("1")),
    "dozen": ("count", Decimal("12")),
}


def _convert_compatible_unit(qty: Decimal, from_unit: str | None, to_unit: str | None) -> Decimal | None:
    """Convert qty within the same physical dimension only; return None across dimensions."""
    f = (from_unit or "").strip().lower()
    t = (to_unit or "").strip().lower()
    if not f or not t:
        return None
    if f == t:
        return qty
    f_info = _UNIT_FAMILY_SCALE.get(f)
    t_info = _UNIT_FAMILY_SCALE.get(t)
    if f_info is None or t_info is None or f_info[0] != t_info[0]:
        return None
    return (qty * f_info[1]) / t_info[1]


@dataclass(frozen=True, slots=True)
class BatchFEFORequest:
    canonical_item_id: UUID
    required_qty: Decimal
    base_unit: str | None = None
    event_type: str = "COOK_OUT"
    notes: str | None = None


async def batch_allocate_fefo(
    db: AsyncSession,
    *,
    household_id: UUID,
    requests: list[BatchFEFORequest],
    actor_user_id: UUID,
    reference_type: str | None = None,
    reference_id: UUID | None = None,
    today: date | None = None,
) -> list[tuple[BatchFEFORequest, list[FEFODeduction]]]:
    """Allocate and deduct stock across multiple items in FEFO order with one batched lot query."""
    if not requests:
        return []

    ref_today = today or datetime.now(UTC).date()
    positive_cids = sorted(
        {req.canonical_item_id for req in requests if req.required_qty > Decimal("0")},
        key=str,
    )
    if not positive_cids:
        return [(req, []) for req in requests]

    if _is_postgresql(db):
        for cid in positive_cids:
            lock_key = f"{household_id}:{cid}"
            await db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": lock_key},
            )

    stmt = (
        select(InventoryLot)
        .where(
            InventoryLot.household_id == household_id,
            InventoryLot.canonical_item_id.in_(positive_cids),
            InventoryLot.lot_status == "active",
            InventoryLot.remaining_qty > Decimal("0"),
            or_(InventoryLot.expires_on.is_(None), InventoryLot.expires_on >= ref_today),
        )
        .order_by(
            InventoryLot.canonical_item_id.asc(),
            InventoryLot.expires_on.asc().nulls_last(),
            InventoryLot.purchased_at.asc(),
            InventoryLot.id.asc(),
        )
    )
    if _is_postgresql(db):
        stmt = stmt.with_for_update()

    lots = list((await db.execute(stmt)).scalars().all())
    lots_by_cid: dict[UUID, list[InventoryLot]] = {}
    for lot in lots:
        lots_by_cid.setdefault(lot.canonical_item_id, []).append(lot)

    pending_entries: list[
        tuple[BatchFEFORequest, list[tuple[StockLedger, UUID, UUID, Decimal, str]]]
    ] = []
    for req in requests:
        if req.required_qty <= Decimal("0"):
            pending_entries.append((req, []))
            continue

        req_unit = (req.base_unit or "").strip().lower() or None
        item_lots = [
            lot
            for lot in lots_by_cid.get(req.canonical_item_id, [])
            if lot.remaining_qty > Decimal("0")
            and (
                req_unit is None
                or _convert_compatible_unit(Decimal("1"), lot.base_unit, req_unit) is not None
            )
        ]
        if req_unit is not None:
            total_available = sum(
                (
                    _convert_compatible_unit(lot.remaining_qty, lot.base_unit, req_unit)
                    or Decimal("0")
                    for lot in item_lots
                ),
                Decimal("0"),
            )
        else:
            total_available = sum((lot.remaining_qty for lot in item_lots), Decimal("0"))
        if total_available < req.required_qty:
            raise AppError(
                status_code=409,
                code="INSUFFICIENT_STOCK",
                message=(
                    f"Insufficient active stock for item {req.canonical_item_id}: "
                    f"requested {req.required_qty}"
                    f"{f' {req_unit}' if req_unit else ''}, "
                    f"available {total_available}"
                    f"{f' {req_unit}' if req_unit else ''}."
                ),
                details={
                    "canonical_item_id": str(req.canonical_item_id),
                    "requested_qty": str(req.required_qty),
                    "available_qty": str(total_available),
                    "base_unit": req_unit,
                },
            )

        remaining_to_deduct = req.required_qty
        req_entries: list[tuple[StockLedger, UUID, UUID, Decimal, str]] = []
        for lot in item_lots:
            if remaining_to_deduct <= Decimal("0"):
                break
            if req_unit is not None:
                lot_avail_in_req = _convert_compatible_unit(
                    lot.remaining_qty, lot.base_unit, req_unit
                )
                if lot_avail_in_req is None or lot_avail_in_req <= Decimal("0"):
                    continue
                take_in_req = min(lot_avail_in_req, remaining_to_deduct)
                take = _convert_compatible_unit(take_in_req, req_unit, lot.base_unit)
                if take is None or take <= Decimal("0"):
                    continue
                take = min(lot.remaining_qty, take.quantize(Decimal("0.001")))
                remaining_to_deduct -= take_in_req
            else:
                take = min(lot.remaining_qty, remaining_to_deduct)
                remaining_to_deduct -= take
            lot.remaining_qty = lot.remaining_qty - take
            if lot.remaining_qty == Decimal("0"):
                lot.lot_status = "depleted"

            ledger_entry = StockLedger(
                household_id=household_id,
                lot_id=lot.id,
                canonical_item_id=req.canonical_item_id,
                event_type=req.event_type,
                quantity_delta=-take,
                base_unit=lot.base_unit,
                balance_after_lot=lot.remaining_qty,
                unit_cost_paise=lot.unit_cost_paise,
                reference_type=reference_type,
                reference_id=reference_id,
                actor_user_id=actor_user_id,
                notes=req.notes,
            )
            db.add(ledger_entry)
            req_entries.append((ledger_entry, lot.id, req.canonical_item_id, take, lot.base_unit))

        pending_entries.append((req, req_entries))

    await db.flush()

    results: list[tuple[BatchFEFORequest, list[FEFODeduction]]] = []
    for req, req_entries in pending_entries:
        deductions = [
            FEFODeduction(
                lot_id=lot_id,
                canonical_item_id=cid,
                deducted_qty=take,
                base_unit=base_unit,
                ledger_entry_id=ledger_entry.id,
            )
            for ledger_entry, lot_id, cid, take, base_unit in req_entries
        ]
        results.append((req, deductions))

    return results


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
                    or_(
                        CanonicalItem.household_id == household_id,
                        CanonicalItem.household_id.is_(None),
                    )
                )
            )
        )
        .scalars()
        .all()
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
                f"Cannot deduct {-delta} from lot {lot.id}; only {lot.remaining_qty} remaining."
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
        (
            await db.execute(select(func.count()).select_from(CanonicalItem).where(*filters))
        ).scalar_one()
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
