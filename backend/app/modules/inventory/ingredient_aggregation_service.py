"""Read-only ingredient-level pantry aggregation service (Phase 4C).

Uses the same active-lot, remaining_qty > 0, and non-expired filtering rules as
``get_inventory_summary`` so ingredient totals never disagree with product totals.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.inventory.models import (
    CanonicalIngredient,
    CanonicalItem,
    IngredientMapping,
    InventoryLot,
)
from app.modules.inventory.schemas import (
    IngredientInventorySummaryResponse,
    IngredientLotDetailOut,
    IngredientProductBreakdownOut,
    IngredientSummaryOut,
    IngredientUnitGroupOut,
    UnmappedPantryProductOut,
)

_UNIT_CANONICAL: dict[str, str] = {
    "g": "g",
    "gm": "g",
    "gms": "g",
    "gram": "g",
    "grams": "g",
    "kg": "kg",
    "kgs": "kg",
    "kilogram": "kg",
    "kilograms": "kg",
    "ml": "ml",
    "millilitre": "ml",
    "millilitres": "ml",
    "milliliter": "ml",
    "milliliters": "ml",
    "l": "l",
    "lt": "l",
    "ltr": "l",
    "liter": "l",
    "litre": "l",
    "liters": "l",
    "litres": "l",
    "pc": "pcs",
    "pcs": "pcs",
    "piece": "pcs",
    "pieces": "pcs",
    "no": "pcs",
    "nos": "pcs",
    "unit": "pcs",
    "units": "pcs",
    "dozen": "dozen",
    "dozens": "dozen",
    "doz": "dozen",
    "dz": "dozen",
    "pack": "pack",
    "packs": "pack",
    "pk": "pack",
    "bottle": "bottle",
    "bottles": "bottle",
    "btl": "bottle",
    "packet": "packet",
    "packets": "packet",
    "pkt": "packet",
    "box": "box",
    "boxes": "box",
    "jar": "jar",
    "jars": "jar",
    "can": "can",
    "cans": "can",
    "tin": "can",
    "tins": "can",
    "loaf": "loaf",
    "loaves": "loaf",
    "slice": "slice",
    "slices": "slice",
    "bunch": "bunch",
    "bunches": "bunch",
    "head": "head",
    "heads": "head",
    "cm": "cm",
    "m": "m",
}

_MASS_FACTORS_TO_G: dict[str, Decimal] = {"g": Decimal("1"), "kg": Decimal("1000")}
_VOL_FACTORS_TO_ML: dict[str, Decimal] = {"ml": Decimal("1"), "l": Decimal("1000")}
_COUNT_FACTORS_TO_PCS: dict[str, Decimal] = {"pcs": Decimal("1"), "dozen": Decimal("12")}
_LENGTH_FACTORS_TO_CM: dict[str, Decimal] = {"cm": Decimal("1"), "m": Decimal("100")}
_PACKAGING_UNITS: frozenset[str] = frozenset(
    {"pack", "bottle", "packet", "box", "jar", "can", "loaf", "slice", "bunch", "head"}
)


def get_unit_dimension(unit: str | None) -> str:
    """Return the measurement dimension: weight, volume, count, length, packaging, or other."""
    norm = normalize_pantry_unit(unit)
    if norm in _MASS_FACTORS_TO_G:
        return "weight"
    if norm in _VOL_FACTORS_TO_ML:
        return "volume"
    if norm in _COUNT_FACTORS_TO_PCS:
        return "count"
    if norm in _LENGTH_FACTORS_TO_CM:
        return "length"
    if norm in _PACKAGING_UNITS:
        return "packaging"
    return "other"


def suggest_units_for_product(
    name: str | None,
    current_unit: str | None = None,
) -> list[str]:
    """Return product-aware suitable measurement units without inventing conversions."""
    low = (name or "").strip().lower()
    curr = normalize_pantry_unit(current_unit) if current_unit else None
    suggested: list[str] = []

    if any(k in low for k in ("rice", "sugar", "flour", "atta", "dal", "lentil", "salt", "spice")):
        suggested = ["g", "kg"]
    elif any(k in low for k in ("milk", "oil", "ghee", "vinegar", "water", "broth")):
        suggested = ["ml", "l"]
    elif any(k in low for k in ("juice", "soda", "drink", "beverage", "sauce", "syrup")):
        suggested = ["ml", "l", "bottle", "pack"]
    elif any(k in low for k in ("egg",)):
        suggested = ["pcs", "dozen"]
    elif any(k in low for k in ("chocolate", "candy", "bar")):
        suggested = ["pcs", "g"]
    elif any(k in low for k in ("biscuit", "cookie", "cracker", "chips", "snack", "noodle")):
        suggested = ["pcs", "pack", "packet"]
    elif any(k in low for k in ("bread", "toast", "bun")):
        suggested = ["loaf", "slice"]
    elif any(k in low for k in ("curd", "yogurt", "yoghurt", "dahi", "cream")):
        suggested = ["g", "kg", "ml", "l"]
    elif curr in _MASS_FACTORS_TO_G:
        suggested = ["g", "kg"]
    elif curr in _VOL_FACTORS_TO_ML:
        suggested = ["ml", "l"]
    elif curr in _COUNT_FACTORS_TO_PCS:
        suggested = ["pcs", "dozen"]
    elif curr in _PACKAGING_UNITS:
        suggested = [curr, "pack", "packet", "bottle", "box", "jar", "can", "loaf", "slice"]
    else:
        suggested = ["g", "kg", "ml", "l", "pcs", "dozen", "pack"]

    out: list[str] = []
    if curr and curr not in suggested:
        out.append(curr)
    for u in suggested:
        if u not in out:
            out.append(u)
    return out


def normalize_pantry_unit(unit: str | None) -> str:
    """Normalize a unit string to its canonical lowercase token."""
    cleaned = (unit or "").strip().lower()
    if not cleaned:
        return "pcs"
    return _UNIT_CANONICAL.get(cleaned, cleaned)


def convert_to_target_unit(
    qty: Decimal,
    from_unit: str | None,
    target_unit: str | None,
) -> tuple[Decimal | None, str]:
    """Explicitly convert ``qty`` into ``target_unit`` if in the same unit family.

    Returns ``(converted_qty, group_unit)`` where ``converted_qty`` is ``None`` when
    ``from_unit`` is incompatible with ``target_unit`` (e.g., weight vs volume, pcs vs g,
    packaging vs weight/volume/count, or unknown units).
    """
    src = normalize_pantry_unit(from_unit)
    dst = normalize_pantry_unit(target_unit)

    if src in _MASS_FACTORS_TO_G and dst in _MASS_FACTORS_TO_G:
        in_g = qty * _MASS_FACTORS_TO_G[src]
        return (in_g / _MASS_FACTORS_TO_G[dst]).quantize(Decimal("0.001")), dst

    if src in _VOL_FACTORS_TO_ML and dst in _VOL_FACTORS_TO_ML:
        in_ml = qty * _VOL_FACTORS_TO_ML[src]
        return (in_ml / _VOL_FACTORS_TO_ML[dst]).quantize(Decimal("0.001")), dst

    if src in _COUNT_FACTORS_TO_PCS and dst in _COUNT_FACTORS_TO_PCS:
        in_pcs = qty * _COUNT_FACTORS_TO_PCS[src]
        return (in_pcs / _COUNT_FACTORS_TO_PCS[dst]).quantize(Decimal("0.001")), dst

    if src in _LENGTH_FACTORS_TO_CM and dst in _LENGTH_FACTORS_TO_CM:
        in_cm = qty * _LENGTH_FACTORS_TO_CM[src]
        return (in_cm / _LENGTH_FACTORS_TO_CM[dst]).quantize(Decimal("0.001")), dst

    if src in _PACKAGING_UNITS and dst == src:
        return qty.quantize(Decimal("0.001")), dst

    if src in _MASS_FACTORS_TO_G:
        return None, "g"
    if src in _VOL_FACTORS_TO_ML:
        return None, "ml"
    if src in _COUNT_FACTORS_TO_PCS:
        return None, "pcs"
    if src in _LENGTH_FACTORS_TO_CM:
        return None, "cm"
    return None, src


async def get_ingredient_inventory_summary(
    db: AsyncSession,
    household_id: UUID,
    *,
    q: str | None = None,
    category: str | None = None,
    include_zero_stock: bool = False,
    today: date | None = None,
) -> IngredientInventorySummaryResponse:
    """Return read-only household inventory aggregated by CanonicalIngredient."""
    ref_today = today or datetime.now(UTC).date()
    q_norm = q.strip().lower() if q and q.strip() else None
    cat_norm = category.strip().lower() if category and category.strip() else None

    # 1. Load all active CanonicalItems visible to this household (or soft-merged pointers)
    all_items = (
        (
            await db.execute(
                select(CanonicalItem).where(
                    (CanonicalItem.household_id == household_id)
                    | (CanonicalItem.household_id.is_(None))
                )
            )
        )
        .scalars()
        .all()
    )
    items_by_id: dict[UUID, CanonicalItem] = {it.id: it for it in all_items}

    def _resolve_survivor(item_id: UUID) -> CanonicalItem | None:
        cur = items_by_id.get(item_id)
        seen: set[UUID] = set()
        while cur is not None and (not cur.is_active or cur.merged_into_id is not None):
            if cur.merged_into_id is None or cur.id in seen:
                return None
            seen.add(cur.id)
            cur = items_by_id.get(cur.merged_into_id)
        if cur is not None and not cur.is_active:
            return None
        return cur

    # 2. Load IngredientMappings for this household (household overrides global; deduplicated per
    # canonical_item_id)
    mapping_rows = (
        (
            await db.execute(
                select(IngredientMapping).where(
                    (IngredientMapping.household_id == household_id)
                    | (IngredientMapping.household_id.is_(None))
                )
            )
        )
        .scalars()
        .all()
    )

    effective_mapping_by_item: dict[UUID, IngredientMapping] = {}
    for m in mapping_rows:
        survivor = _resolve_survivor(m.canonical_item_id)
        if survivor is None:
            continue
        target_item_id = survivor.id
        existing = effective_mapping_by_item.get(target_item_id)
        if existing is None:
            effective_mapping_by_item[target_item_id] = m
        else:
            # Prefer household-scoped mapping over global; prefer user_confirmed over unconfirmed
            existing_score = (
                1 if existing.household_id == household_id else 0,
                1 if existing.user_confirmed else 0,
            )
            new_score = (1 if m.household_id == household_id else 0, 1 if m.user_confirmed else 0)
            if new_score > existing_score:
                effective_mapping_by_item[target_item_id] = m

    # 3. Load active lots with remaining_qty > 0 for this household (same filter as
    # get_inventory_summary)
    lot_rows = (
        await db.execute(
            select(InventoryLot, CanonicalItem)
            .join(CanonicalItem, CanonicalItem.id == InventoryLot.canonical_item_id)
            .where(
                InventoryLot.household_id == household_id,
                InventoryLot.lot_status == "active",
                InventoryLot.remaining_qty > Decimal("0"),
            )
        )
    ).all()

    # Deduplicate lots by lot.id and group by surviving active CanonicalItem
    seen_lot_ids: set[UUID] = set()
    lots_by_survivor_id: dict[UUID, list[InventoryLot]] = defaultdict(list)
    for lot, raw_item in lot_rows:
        if lot.id in seen_lot_ids:
            continue
        seen_lot_ids.add(lot.id)
        survivor = _resolve_survivor(raw_item.id)
        if survivor is None:
            continue
        lots_by_survivor_id[survivor.id].append(lot)

    # 4. Load active CanonicalIngredients
    ingredients = (
        (
            await db.execute(
                select(CanonicalIngredient).where(CanonicalIngredient.is_active.is_(True))
            )
        )
        .scalars()
        .all()
    )
    ingredients_by_id: dict[UUID, CanonicalIngredient] = {ing.id: ing for ing in ingredients}

    # Map confirmed mappings per ingredient
    confirmed_items_by_ing: dict[UUID, list[tuple[CanonicalItem, IngredientMapping]]] = defaultdict(
        list
    )
    for item_id, mapping in effective_mapping_by_item.items():
        if not mapping.user_confirmed:
            continue
        item = items_by_id.get(item_id)
        if item is None or not item.is_active or item.merged_into_id is not None:
            continue
        if mapping.canonical_ingredient_id in ingredients_by_id:
            confirmed_items_by_ing[mapping.canonical_ingredient_id].append((item, mapping))

    # 5. Build ingredient summaries
    ingredient_outs: list[IngredientSummaryOut] = []
    for ing in sorted(ingredients, key=lambda x: x.name.lower()):
        mapped_pairs = confirmed_items_by_ing.get(ing.id, [])
        if not mapped_pairs and not include_zero_stock:
            continue
        if cat_norm and ing.category.strip().lower() != cat_norm:
            continue

        product_breakdowns: list[IngredientProductBreakdownOut] = []
        unit_group_totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        unit_group_counts: dict[str, int] = defaultdict(int)
        ing_has_expired = False
        ing_earliest_expiry: date | None = None
        total_lot_count = 0
        all_compatible = True

        for item, mapping in sorted(mapped_pairs, key=lambda p: p[0].name.lower()):
            item_lots = lots_by_survivor_id.get(item.id, [])
            prod_total = Decimal("0")
            prod_active_lots = 0
            prod_has_expired = False
            prod_earliest_exp: date | None = None
            lot_details: list[IngredientLotDetailOut] = []

            for lot in sorted(
                item_lots,
                key=lambda lot_item: (lot_item.expires_on or date.max, lot_item.purchased_at),
            ):
                is_expired = lot.expires_on is not None and lot.expires_on < ref_today
                if is_expired:
                    prod_has_expired = True
                    ing_has_expired = True
                    continue

                prod_total += lot.remaining_qty
                prod_active_lots += 1
                total_lot_count += 1
                if lot.expires_on is not None:
                    if prod_earliest_exp is None or lot.expires_on < prod_earliest_exp:
                        prod_earliest_exp = lot.expires_on
                    if ing_earliest_expiry is None or lot.expires_on < ing_earliest_expiry:
                        ing_earliest_expiry = lot.expires_on

                converted_qty, group_unit = convert_to_target_unit(
                    lot.remaining_qty, lot.base_unit, ing.standard_unit
                )
                if converted_qty is None:
                    all_compatible = False
                    # Keep raw unit group total in group_unit (base family or raw unknown unit)
                    if group_unit in ("g", "ml"):
                        conv_group, _ = convert_to_target_unit(
                            lot.remaining_qty, lot.base_unit, group_unit
                        )
                        unit_group_totals[group_unit] += (
                            conv_group if conv_group is not None else lot.remaining_qty
                        )
                    else:
                        unit_group_totals[group_unit] += lot.remaining_qty
                    unit_group_counts[group_unit] += 1
                else:
                    unit_group_totals[group_unit] += converted_qty
                    unit_group_counts[group_unit] += 1

                lot_details.append(
                    IngredientLotDetailOut(
                        lot_id=lot.id,
                        canonical_item_id=item.id,
                        purchase_item_id=lot.purchase_item_id,
                        initial_qty=lot.initial_qty,
                        remaining_qty=lot.remaining_qty,
                        base_unit=lot.base_unit,
                        converted_qty=converted_qty,
                        converted_unit=ing.standard_unit if converted_qty is not None else None,
                        unit_cost_paise=lot.unit_cost_paise,
                        purchased_at=lot.purchased_at,
                        expires_on=lot.expires_on,
                        lot_status=lot.lot_status,
                    )
                )

            product_breakdowns.append(
                IngredientProductBreakdownOut(
                    canonical_item_id=item.id,
                    name=item.name,
                    category=item.category,
                    base_unit=item.default_base_unit,
                    total_remaining_qty=prod_total.quantize(Decimal("0.001")),
                    active_lot_count=prod_active_lots,
                    has_expired_lots=prod_has_expired,
                    earliest_expires_on=prod_earliest_exp,
                    mapping_source=mapping.mapping_source,
                    matching_confidence=mapping.matching_confidence,
                    lots=lot_details,
                )
            )

        if q_norm:
            ing_match = q_norm in ing.name.lower() or q_norm in ing.normalized_name.lower()
            prod_match = any(q_norm in p.name.lower() for p in product_breakdowns)
            if not ing_match and not prod_match:
                continue

        unit_groups = [
            IngredientUnitGroupOut(
                unit=u,
                total_qty=unit_group_totals[u].quantize(Decimal("0.001")),
                lot_count=unit_group_counts[u],
            )
            for u in sorted(unit_group_totals.keys())
        ]

        if total_lot_count == 0:
            completeness = "empty"
            total_qty: Decimal | None = Decimal("0.000")
            note = "No active non-expired lots for mapped products."
        elif (
            all_compatible
            and len(unit_groups) == 1
            and unit_groups[0].unit == normalize_pantry_unit(ing.standard_unit)
        ):
            completeness = "complete"
            total_qty = unit_groups[0].total_qty
            note = (
                f"Summed {total_lot_count} active lot(s) across {len(product_breakdowns)} "
                f"product(s) in {ing.standard_unit}."
            )
        else:
            completeness = "incomplete"
            total_qty = None
            group_desc = ", ".join(f"{g.total_qty} {g.unit}" for g in unit_groups)
            note = (
                f"Incomplete combined total due to incompatible units ({group_desc}); "
                f"standard unit is {ing.standard_unit}."
            )

        ingredient_outs.append(
            IngredientSummaryOut(
                canonical_ingredient_id=ing.id,
                name=ing.name,
                normalized_name=ing.normalized_name,
                category=ing.category,
                subcategory=ing.subcategory,
                standard_unit=ing.standard_unit,
                total_remaining_qty=total_qty,
                unit_completeness_status=completeness,
                unit_groups=unit_groups,
                contributing_product_count=len(product_breakdowns),
                lot_count=total_lot_count,
                has_expired_lots=ing_has_expired,
                earliest_expires_on=ing_earliest_expiry,
                calculation_note=note,
                products=product_breakdowns,
            )
        )

    # 6. Collect unmapped / unconfirmed-suggestion pantry products (for transparency)
    unmapped_outs: list[UnmappedPantryProductOut] = []
    unmapped_lots_count = 0
    unconfirmed_suggestions_count = 0

    for item_id, item_lots in lots_by_survivor_id.items():
        item = items_by_id.get(item_id)
        if item is None:
            continue
        mapping = effective_mapping_by_item.get(item_id)
        if mapping is not None and mapping.user_confirmed:
            continue

        non_expired_lots = [
            lot
            for lot in item_lots
            if not (lot.expires_on is not None and lot.expires_on < ref_today)
        ]
        if not non_expired_lots:
            continue
        if cat_norm and item.category.strip().lower() != cat_norm:
            continue
        if q_norm and q_norm not in item.name.lower():
            continue

        is_suggested = mapping is not None and not mapping.user_confirmed
        if is_suggested:
            unconfirmed_suggestions_count += 1
        sugg_ing = ingredients_by_id.get(mapping.canonical_ingredient_id) if mapping else None
        unmapped_lots_count += len(non_expired_lots)
        unmapped_outs.append(
            UnmappedPantryProductOut(
                canonical_item_id=item.id,
                name=item.name,
                category=item.category,
                base_unit=item.default_base_unit,
                total_remaining_qty=sum(
                    (lot.remaining_qty for lot in non_expired_lots), Decimal("0")
                ).quantize(Decimal("0.001")),
                active_lot_count=len(non_expired_lots),
                suggestion_status="unconfirmed_suggestion" if is_suggested else "unmapped",
                suggested_ingredient_id=sugg_ing.id if sugg_ing else None,
                suggested_ingredient_name=sugg_ing.name if sugg_ing else None,
                suggestion_reason=(
                    f"Suggested from {mapping.mapping_source} (confidence "
                    f"{mapping.matching_confidence}); awaiting user confirmation."
                    if is_suggested and mapping is not None
                    else None
                ),
            )
        )

    unmapped_outs.sort(key=lambda x: x.name.lower())
    _non_food_cats = {
        "non_food",
        "non-food",
        "personal_care",
        "household",
        "cleaning",
        "pet",
        "toiletry",
        "stationery",
    }
    mapping_review_items = [
        u
        for u in unmapped_outs
        if u.suggestion_status == "unconfirmed_suggestion"
        and u.suggested_ingredient_id is not None
        and u.category.strip().lower() not in _non_food_cats
        and items_by_id.get(u.canonical_item_id) is not None
        and items_by_id[u.canonical_item_id].pantry_eligible
    ]

    return IngredientInventorySummaryResponse(
        ingredients=ingredient_outs,
        unmapped_products_count=len(unmapped_outs),
        unmapped_lots_count=unmapped_lots_count,
        unconfirmed_suggestions_count=unconfirmed_suggestions_count,
        mapping_review_count=len(mapping_review_items),
        mapping_review_items=mapping_review_items,
        unmapped_products=unmapped_outs,
    )
