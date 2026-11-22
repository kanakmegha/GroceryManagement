from __future__ import annotations

import math
import re
import statistics
import time
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.cooking.models import CookingPlan
from app.modules.inventory.models import CanonicalItem, IngredientMapping, InventoryLot, StockLedger
from app.modules.purchases.models import Purchase
from app.modules.replenishment.models import ReplenishmentFeedback
from app.modules.replenishment.schemas import (
    ConfidenceLevel,
    DataQualityIssueOut,
    FeedbackAction,
    RecommendationGroup,
    ReplenishmentItemOut,
    ReplenishmentRecommendationsResponse,
    StockStatus,
)

# Lightweight in-process cache keyed by household_id (invalidated on mutations)
_CACHE_TTL_SECONDS = 30.0
_RECOMMENDATIONS_CACHE: dict[str, tuple[float, ReplenishmentRecommendationsResponse]] = {}


def invalidate_replenishment_cache(household_id: UUID | str) -> None:
    prefix = f"{household_id}:"
    for k in [
        key for key in _RECOMMENDATIONS_CACHE if key == str(household_id) or key.startswith(prefix)
    ]:
        _RECOMMENDATIONS_CACHE.pop(k, None)


def _round_to_known_packaging(net_req: float, modal_pack: float | None, unit: str) -> float:
    """Round net requirement up to whole known purchase packs without inventing arbitrary sizes."""
    if modal_pack is not None and modal_pack > 0:
        packs = max(1, math.ceil(net_req / modal_pack))
        rounded = round(packs * modal_pack, 3)
        if unit == "pcs" and rounded == int(rounded):
            return float(int(rounded))
        return rounded
    if unit == "pcs":
        return float(max(1, math.ceil(net_req)))
    return max(0.01, round(net_req, 2))


_UNIT_ALIASES: dict[str, str] = {
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
    "pack": "pcs",
    "packs": "pcs",
    "unit": "pcs",
    "units": "pcs",
}

_MASS_UNITS = {"g": 1.0, "kg": 1000.0}
_VOL_UNITS = {"ml": 1.0, "l": 1000.0}

_NON_FOOD_CATEGORIES = {
    "non_food",
    "non-food",
    "personal_care",
    "household",
    "cleaning",
    "pet",
    "toiletry",
    "stationery",
}

_NON_FOOD_NAME_RE = re.compile(
    r"\b(hair\s*oil|matchbox|matchhox|utensil|scrubber|detergent|dishwash|soap|shampoo|"
    r"toothpaste|toothbrush|toilet\s*paper|tissue|bleach|phenyl|mop|broom|sanitizer)\b",
    re.IGNORECASE,
)

_GARBLED_OCR_RE = re.compile(
    r"(^1\s+resh\b|^l'obusta\b|^mported\b|\bO1l\b|\bMatchhox\b|\bserbher\b|\bR\$$|\bKun\s+Fero\b|\bAcafnetidol\b)",
    re.IGNORECASE,
)


def _to_local_date(dt: datetime | None, local_tz: ZoneInfo, fallback: date) -> date:
    """Convert a stored UTC/aware timestamp to the household's local calendar date."""
    if dt is None:
        return fallback
    aware = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt
    return aware.astimezone(local_tz).date()


def normalize_unit(unit: str | None) -> str:
    if not unit:
        return "pcs"
    cleaned = unit.strip().lower()
    return _UNIT_ALIASES.get(cleaned, cleaned)


def are_units_compatible(unit_a: str | None, unit_b: str | None) -> bool:
    ua = normalize_unit(unit_a)
    ub = normalize_unit(unit_b)
    if ua == ub:
        return True
    if ua in _MASS_UNITS and ub in _MASS_UNITS:
        return True
    return bool(ua in _VOL_UNITS and ub in _VOL_UNITS)


def convert_compatible_quantity(
    qty: float | Decimal,
    from_unit: str | None,
    to_unit: str | None,
) -> float | None:
    """Explicitly convert compatible units; return None if incompatible."""
    ua = normalize_unit(from_unit)
    ub = normalize_unit(to_unit)
    val = float(qty)
    if ua == ub:
        return val
    if ua in _MASS_UNITS and ub in _MASS_UNITS:
        return val * (_MASS_UNITS[ua] / _MASS_UNITS[ub])
    if ua in _VOL_UNITS and ub in _VOL_UNITS:
        return val * (_VOL_UNITS[ua] / _VOL_UNITS[ub])
    return None


def _check_canonical_eligibility(
    item: CanonicalItem,
) -> tuple[bool, str | None, str | None]:
    """Return (is_eligible, issue_type, detail)."""
    cat = str(item.category or "").strip().lower()
    if (
        not item.pantry_eligible
        or item.is_prepared_food
        or item.budget_treatment == "outside_food_expenditure"
        or cat in _NON_FOOD_CATEGORIES
        or _NON_FOOD_NAME_RE.search(item.name or "")
    ):
        return (
            False,
            "excluded_non_food",
            f"'{item.name}' is classified or detected as non-food/prepared-food and "
            "excluded from grocery replenishment.",
        )

    name = (item.name or "").strip()
    if len(name) > 85 or _GARBLED_OCR_RE.search(name):
        return (
            True,
            "uncertain_identity",
            f"'{name}' appears to have OCR-garbled or merged text; confident frequency "
            "predictions are suppressed.",
        )

    return True, None, None


async def compute_replenishment_recommendations(
    db: AsyncSession,
    household_id: UUID,
    timezone_name: str = "Asia/Kolkata",
    *,
    upcoming_days: int = 7,
    use_cache: bool = True,
) -> ReplenishmentRecommendationsResponse:
    prediction_horizon_days = max(1, int(upcoming_days))
    cache_key = f"{household_id}:{prediction_horizon_days}"
    now_mono = time.monotonic()
    if use_cache:
        cached = _RECOMMENDATIONS_CACHE.get(cache_key)
        if cached and (now_mono - cached[0]) < _CACHE_TTL_SECONDS:
            return cached[1]

    try:
        local_tz = ZoneInfo(timezone_name or "Asia/Kolkata")
    except Exception:
        local_tz = ZoneInfo("Asia/Kolkata")
    now_utc = datetime.now(UTC)
    today_local = now_utc.astimezone(local_tz).date()

    # 1. Load canonical items for household
    ci_rows = (
        (await db.execute(select(CanonicalItem).where(CanonicalItem.household_id == household_id)))
        .scalars()
        .all()
    )
    canonical_by_id: dict[UUID, CanonicalItem] = {c.id: c for c in ci_rows}

    # 2. Load confirmed non-restaurant purchases & items
    purchase_rows = (
        (
            await db.execute(
                select(Purchase)
                .options(selectinload(Purchase.items))
                .where(
                    Purchase.household_id == household_id,
                    Purchase.status == "confirmed",
                )
                .order_by(Purchase.purchased_on.asc(), Purchase.created_at.asc())
            )
        )
        .scalars()
        .all()
    )

    purchases_by_item: dict[UUID, list[tuple[date, float, str, str | None]]] = defaultdict(list)
    for p in purchase_rows:
        if str(p.receipt_category or "").strip().lower() == "restaurant":
            continue
        for it in p.items:
            if it.canonical_item_id is None:
                continue
            if not it.track_inventory:
                continue
            if str(it.budget_treatment or "").strip().lower() == "outside_food_expenditure":
                continue
            if str(it.food_state or "").strip().lower() == "cooked":
                continue
            purchases_by_item[it.canonical_item_id].append(
                (
                    p.purchased_on,
                    float(it.normalized_qty or 1),
                    normalize_unit(it.base_unit),
                    p.merchant_name,
                )
            )

    # 3. Load inventory lots
    lot_rows = (
        (await db.execute(select(InventoryLot).where(InventoryLot.household_id == household_id)))
        .scalars()
        .all()
    )
    lots_by_item: dict[UUID, list[InventoryLot]] = defaultdict(list)
    for lot in lot_rows:
        lots_by_item[lot.canonical_item_id].append(lot)

    # 4. Load planned cooking-plan reservations (exclude completed/cancelled)
    planned_cooking = (
        (
            await db.execute(
                select(CookingPlan).where(
                    CookingPlan.household_id == household_id,
                    CookingPlan.status == "planned",
                )
            )
        )
        .scalars()
        .all()
    )
    reserved_by_item: dict[UUID, list[tuple[float, str]]] = defaultdict(list)
    for plan in planned_cooking:
        for ing in plan.ingredients or []:
            cid_raw = ing.get("canonical_item_id")
            if not cid_raw:
                continue
            try:
                cid = UUID(str(cid_raw))
            except Exception:
                continue
            qty_val = float(ing.get("qty") or ing.get("quantity") or 0)
            if qty_val > 0:
                reserved_by_item[cid].append(
                    (qty_val, normalize_unit(str(ing.get("unit") or "pcs")))
                )

    # 5. Load stock ledger movements for actual consumption velocity
    ledger_rows = (
        (
            await db.execute(
                select(StockLedger)
                .where(StockLedger.household_id == household_id)
                .order_by(StockLedger.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    ledger_by_item: dict[UUID, list[StockLedger]] = defaultdict(list)
    for entry in ledger_rows:
        ledger_by_item[entry.canonical_item_id].append(entry)

    # 6. Load replenishment feedback
    fb_rows = (
        (
            await db.execute(
                select(ReplenishmentFeedback)
                .where(ReplenishmentFeedback.household_id == household_id)
                .order_by(ReplenishmentFeedback.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    active_fb_by_item: dict[UUID, list[ReplenishmentFeedback]] = defaultdict(list)
    dismissal_count_by_item: dict[UUID, int] = defaultdict(int)
    for fb in fb_rows:
        if fb.action in ("dismissed", "not_relevant"):
            dismissal_count_by_item[fb.canonical_item_id] += 1
        if fb.is_active:
            active_fb_by_item[fb.canonical_item_id].append(fb)

    # 7. Evaluate every tracked canonical item
    buy_now: list[ReplenishmentItemOut] = []
    buy_soon: list[ReplenishmentItemOut] = []
    regular_purchases: list[ReplenishmentItemOut] = []
    dismissed: list[ReplenishmentItemOut] = []
    needs_more_data: list[ReplenishmentItemOut] = []
    data_quality_issues: list[DataQualityIssueOut] = []

    candidate_ids = sorted(
        set(canonical_by_id.keys()) | set(purchases_by_item.keys()) | set(lots_by_item.keys()),
        key=lambda u: canonical_by_id[u].name.lower() if u in canonical_by_id else str(u),
    )

    # Precompute confirmed ingredient mappings and per-ingredient usable stock (no double-counting)
    confirmed_maps = (
        (
            await db.execute(
                select(IngredientMapping).where(
                    IngredientMapping.user_confirmed.is_(True),
                    (IngredientMapping.household_id == household_id)
                    | (IngredientMapping.household_id.is_(None)),
                )
            )
        )
        .scalars()
        .all()
    )
    item_to_ing: dict[UUID, UUID] = {}
    for m in confirmed_maps:
        if m.household_id == household_id or m.canonical_item_id not in item_to_ing:
            item_to_ing[m.canonical_item_id] = m.canonical_ingredient_id

    ing_usable_totals: dict[UUID, list[tuple[float, str]]] = defaultdict(list)
    for pre_cid in candidate_ids:
        pre_item = canonical_by_id.get(pre_cid)
        if pre_item is None or pre_cid not in item_to_ing:
            continue
        pre_unit = normalize_unit(pre_item.default_base_unit)
        pre_avail = 0.0
        for lot in lots_by_item.get(pre_cid, []):
            if lot.lot_status != "active" or float(lot.remaining_qty) <= 0:
                continue
            if lot.expires_on is not None and lot.expires_on < today_local:
                continue
            conv_l = convert_compatible_quantity(lot.remaining_qty, lot.base_unit, pre_unit)
            if conv_l is not None:
                pre_avail += conv_l
        pre_res = 0.0
        for r_qty, r_unit in reserved_by_item.get(pre_cid, []):
            conv_r = convert_compatible_quantity(r_qty, r_unit, pre_unit)
            if conv_r is not None:
                pre_res += conv_r
        ing_usable_totals[item_to_ing[pre_cid]].append((max(0.0, pre_avail - pre_res), pre_unit))

    for cid in candidate_ids:
        c_item = canonical_by_id.get(cid)
        if c_item is None:
            continue

        eligible, dq_type, dq_detail = _check_canonical_eligibility(c_item)
        if dq_type and dq_detail:
            data_quality_issues.append(
                DataQualityIssueOut(
                    canonical_item_id=str(cid),
                    item_name=c_item.name,
                    issue_type=dq_type,  # type: ignore[arg-type]
                    detail=dq_detail,
                )
            )
        if not eligible:
            continue

        item_purchases = purchases_by_item.get(cid, [])
        item_lots = lots_by_item.get(cid, [])
        if not item_purchases and not item_lots:
            continue

        raw_default_unit = (c_item.default_base_unit or "").strip().lower()
        unit_unknown = (not raw_default_unit) or (
            raw_default_unit in {"unknown", "unk", "n/a", "na", "none"}
        )
        base_unit = normalize_unit(c_item.default_base_unit)
        uncertain_identity = dq_type == "uncertain_identity"
        unit_mismatch = False
        unit_notes: list[str] = []
        if unit_unknown:
            unit_notes.append("Base unit is unknown or missing.")

        # --- Purchase-pattern calculations ---
        # Collapse same-day purchases into daily purchase events for interval computation
        daily_qty_by_date: dict[date, float] = defaultdict(float)
        compatible_qtys: list[float] = []
        last_purchase_date: date | None = None
        last_merchant: str | None = None

        for p_date, p_qty, p_unit, p_merchant in item_purchases:
            last_purchase_date = p_date
            last_merchant = p_merchant
            conv = convert_compatible_quantity(p_qty, p_unit, base_unit)
            if conv is None:
                unit_mismatch = True
                unit_notes.append(f"Purchase {p_qty} {p_unit} incompatible with {base_unit}.")
            else:
                daily_qty_by_date[p_date] += conv
                compatible_qtys.append(conv)

        distinct_dates = (
            sorted(daily_qty_by_date.keys())
            if daily_qty_by_date
            else sorted({p[0] for p in item_purchases})
        )
        purchase_count = len(item_purchases)

        avg_interval: float | None = None
        median_interval: float | None = None
        interval_std: float | None = None
        est_next_date: date | None = None

        if len(distinct_dates) >= 2:
            intervals = [
                (distinct_dates[i] - distinct_dates[i - 1]).days
                for i in range(1, len(distinct_dates))
            ]
            intervals = [iv for iv in intervals if iv > 0]
            if intervals:
                median_interval = round(float(statistics.median(intervals)), 2)
                avg_interval = round(sum(intervals) / len(intervals), 2)
                if len(intervals) >= 2:
                    interval_std = round(float(statistics.pstdev(intervals)), 2)
                if last_purchase_date is not None:
                    est_next_date = last_purchase_date + timedelta(days=round(median_interval))

        avg_qty: float | None = None
        modal_qty: float | None = None
        if compatible_qtys:
            avg_qty = round(sum(compatible_qtys) / len(compatible_qtys), 3)
            modal_qty = Counter(round(q, 3) for q in compatible_qtys).most_common(1)[0][0]

        # --- Confidence evaluation ---
        if uncertain_identity:
            confidence: ConfidenceLevel = "insufficient_data"
            confidence_reason = "Uncertain/OCR-garbled product identity; needs user confirmation."
        elif unit_unknown or unit_mismatch:
            confidence = "insufficient_data"
            confidence_reason = (
                "Base unit is unknown or missing; cannot compute normalized stock or demand."
                if unit_unknown
                else "Incompatible units across purchase/inventory records."
            )
        elif len(distinct_dates) < 2 or median_interval is None:
            confidence = "insufficient_data"
            confidence_reason = (
                f"Only {purchase_count} confirmed purchase(s); at least 2 separate purchase "
                "dates needed."
            )
        elif len(distinct_dates) == 2:
            confidence = "low"
            confidence_reason = "Based on 2 purchase dates; more history will improve accuracy."
        elif (
            interval_std is not None
            and median_interval > 0
            and interval_std > 0.5 * median_interval
        ):
            confidence = "low"
            confidence_reason = "Purchase intervals vary widely across trips."
        elif len(distinct_dates) >= 5 and (
            interval_std is None or interval_std <= 0.35 * median_interval
        ):
            confidence = "high"
            confidence_reason = f"Consistent pattern across {len(distinct_dates)} purchase dates."
        else:
            confidence = "medium"
            confidence_reason = f"Moderate pattern across {len(distinct_dates)} purchase dates."

        # --- Inventory & FEFO usable stock ---
        available_qty = 0.0
        expired_qty = 0.0
        earliest_expires: date | None = None
        for lot in item_lots:
            if lot.lot_status != "active" or float(lot.remaining_qty) <= 0:
                continue
            conv_lot = convert_compatible_quantity(lot.remaining_qty, lot.base_unit, base_unit)
            if conv_lot is None:
                unit_mismatch = True
                unit_notes.append(
                    f"Lot {lot.remaining_qty} {lot.base_unit} incompatible with {base_unit}."
                )
                continue
            if lot.expires_on is not None and lot.expires_on < today_local:
                expired_qty += conv_lot
                continue
            available_qty += conv_lot
            if lot.expires_on is not None and (
                earliest_expires is None or lot.expires_on < earliest_expires
            ):
                earliest_expires = lot.expires_on

        reserved_qty = 0.0
        for r_qty, r_unit in reserved_by_item.get(cid, []):
            conv_r = convert_compatible_quantity(r_qty, r_unit, base_unit)
            if conv_r is None:
                unit_mismatch = True
                unit_notes.append(f"Planned recipe {r_qty} {r_unit} incompatible with {base_unit}.")
            else:
                reserved_qty += conv_r

        usable_qty = max(0.0, round(available_qty - reserved_qty, 3))
        available_qty = round(available_qty, 3)
        reserved_qty = round(reserved_qty, 3)
        expired_qty = round(expired_qty, 3)
        threshold = float(c_item.low_stock_threshold_qty or 0)

        # --- Actual consumption velocity from stock_ledger ---
        item_ledger = ledger_by_item.get(cid, [])
        total_consumed = 0.0
        recent_7d_consumed = 0.0
        earliest_consumption_date: date | None = None
        for entry in item_ledger:
            if entry.event_type not in ("COOK_OUT", "WASTE_OUT", "ADJUSTMENT_OUT"):
                continue
            delta_abs = abs(float(entry.quantity_delta))
            if delta_abs <= 0:
                continue
            conv_d = convert_compatible_quantity(delta_abs, entry.base_unit, base_unit)
            if conv_d is None:
                unit_mismatch = True
                unit_notes.append(
                    f"Ledger {delta_abs} {entry.base_unit} incompatible with {base_unit}."
                )
                continue
            e_date = _to_local_date(entry.created_at, local_tz, today_local)
            if earliest_consumption_date is None or e_date < earliest_consumption_date:
                earliest_consumption_date = e_date
            total_consumed += conv_d
            if (today_local - e_date).days < 7:
                recent_7d_consumed += conv_d

        avg_daily_consumption: float | None = None
        recent_trend: str | None = None
        est_days_remaining: float | None = None
        if total_consumed > 0 and earliest_consumption_date is not None and not unit_mismatch:
            span_days = max(1, (today_local - earliest_consumption_date).days + 1)
            avg_daily_consumption = round(total_consumed / span_days, 4)
            if avg_daily_consumption > 0:
                recent_daily = recent_7d_consumed / 7.0
                if recent_daily > avg_daily_consumption * 1.25:
                    recent_trend = "faster_than_usual"
                elif recent_daily < avg_daily_consumption * 0.75:
                    recent_trend = "slower_than_usual"
                else:
                    recent_trend = "steady"
                if usable_qty > 0:
                    est_days_remaining = round(usable_qty / avg_daily_consumption, 1)

        if confidence == "high" and total_consumed <= 0:
            confidence = "medium"
            confidence_reason = (
                "Consistent purchase dates, but no verified consumption records yet."
            )

        # Ingredient-level usable stock when item has a confirmed IngredientMapping
        ingredient_usable_qty: float | None = None
        if cid in item_to_ing:
            ing_sum = 0.0
            all_ing_compat = True
            for val, val_unit in ing_usable_totals.get(item_to_ing[cid], []):
                conv_i = convert_compatible_quantity(val, val_unit, base_unit)
                if conv_i is None:
                    all_ing_compat = False
                    break
                ing_sum += conv_i
            if all_ing_compat:
                ingredient_usable_qty = round(ing_sum, 3)

        effective_usable_qty = (
            ingredient_usable_qty if ingredient_usable_qty is not None else usable_qty
        )

        if unit_mismatch:
            stock_status: StockStatus = "unknown"
        elif effective_usable_qty <= 0:
            stock_status = "depleted"
        elif threshold > 0 and effective_usable_qty <= threshold:
            stock_status = "low_stock"
        elif est_days_remaining is not None and est_days_remaining <= upcoming_days:
            stock_status = "running_out_soon"
        else:
            stock_status = "adequate"

        # --- Recommended quantity & Phase 5B.1 strict HIGH-confidence stock-aware eligibility ---
        unit_normalized = (
            (not unit_unknown)
            and (not unit_mismatch)
            and (normalize_unit(base_unit) in {"g", "ml", "pcs"})
        )
        is_high_confidence = confidence == "high"

        predicted_requirement: float | None = None
        if avg_daily_consumption is not None and avg_daily_consumption > 0:
            predicted_requirement = round(avg_daily_consumption * prediction_horizon_days, 3)

        if is_high_confidence and (predicted_requirement is None or predicted_requirement <= 0):
            confidence = "medium"
            is_high_confidence = False
            confidence_reason = (
                "Consistent purchase dates, but insufficient verified consumption velocity."
            )

        net_requirement: float | None = (
            round(predicted_requirement - effective_usable_qty, 3)
            if predicted_requirement is not None
            else None
        )

        recommended_qty: float | None = None
        recommended_unit: str | None = base_unit if unit_normalized else None
        quantity_basis: str = "insufficient_data"
        evidence_basis: str = "insufficient_data"
        suppression_reason: str | None = None

        if unit_normalized and stock_status != "unknown":
            if (
                is_high_confidence
                and predicted_requirement is not None
                and net_requirement is not None
            ):
                if net_requirement > 0:
                    recommended_qty = _round_to_known_packaging(
                        net_requirement, modal_qty, base_unit
                    )
                    quantity_basis = "consumption_prediction"
                    evidence_basis = "consumption_based"
                else:
                    suppression_reason = (
                        f"Sufficient usable stock ({effective_usable_qty:g} {base_unit}) covers "
                        f"{prediction_horizon_days}-day predicted demand "
                        f"({predicted_requirement:g} {base_unit})."
                    )
            else:
                suppression_reason = (
                    f"Confidence is {confidence}; only HIGH-confidence, consumption-verified "
                    "items are eligible."
                )
        else:
            suppression_reason = "Unknown unit or incompatible unit mismatch."

        is_actionable_eligible = (
            unit_normalized
            and stock_status != "unknown"
            and is_high_confidence
            and net_requirement is not None
            and net_requirement > 0
            and recommended_qty is not None
            and recommended_qty > 0
        )
        if not is_actionable_eligible:
            recommended_qty = None
            recommended_unit = None
            quantity_basis = "insufficient_data"
            evidence_basis = "insufficient_data"
            if suppression_reason is None:
                suppression_reason = "Insufficient evidence or zero net requirement."

        eligibility_status = "ELIGIBLE" if is_actionable_eligible else "SUPPRESSED"
        if stock_status == "unknown":
            norm_stock_status = "UNKNOWN"
        elif effective_usable_qty <= 0:
            norm_stock_status = "OUT_OF_STOCK"
        elif stock_status in ("low_stock", "running_out_soon") or (
            net_requirement is not None and net_requirement > 0
        ):
            norm_stock_status = "LOW"
        else:
            norm_stock_status = "SUFFICIENT"

        # --- Active feedback & categorisation ---
        active_fbs = active_fb_by_item.get(cid, [])
        latest_fb = active_fbs[0] if active_fbs else None
        fb_action: FeedbackAction | None = latest_fb.action if latest_fb else None  # type: ignore[assignment]
        fb_remind_after: date | None = latest_fb.remind_after if latest_fb else None

        is_suppressed = False
        if latest_fb is not None:
            if (
                latest_fb.action in ("dismissed", "not_relevant")
                or latest_fb.action == "remind_later"
                and (latest_fb.remind_after is None or latest_fb.remind_after > today_local)
            ):
                is_suppressed = True
            elif latest_fb.action == "already_have_enough":
                fb_date = _to_local_date(latest_fb.created_at, local_tz, today_local)
                if (today_local - fb_date).days < 3:
                    is_suppressed = True

        # --- Determine group & human-readable reason ---
        group: RecommendationGroup
        reason: str
        interval_txt = (
            f"every {round(median_interval)} days" if median_interval is not None else None
        )
        last_txt = last_purchase_date.isoformat() if last_purchase_date is not None else None

        if is_suppressed:
            group = "dismissed"
            reason = f"Suppressed due to user feedback ({fb_action})."
        elif not is_actionable_eligible:
            group = "regular_purchases"
            if unit_mismatch or not unit_normalized:
                reason = (
                    "Unit mismatch or unnormalized unit detected; clarify units before "
                    "recommending."
                )
            elif usable_qty <= 0:
                reason = (
                    "Out of stock with insufficient demand history or user target; "
                    "no purchase quantity guessed."
                )
            else:
                reason = (
                    f"{c_item.name} has {usable_qty:g} {base_unit} in stock; insufficient "
                    "history for a recommendation."
                )
        elif stock_status == "depleted":
            group = "buy_now"
            if interval_txt:
                reason = (
                    f"{c_item.name} is depleted. Your household usually purchases it "
                    f"{interval_txt}."
                )
            else:
                reason = f"{c_item.name} is depleted based on recorded inventory lots."
        elif stock_status == "low_stock":
            group = "buy_now"
            reason = (
                f"{c_item.name} is below the configured minimum ({usable_qty:g} {base_unit} "
                f"left vs {threshold:g} {base_unit})."
            )
        elif est_days_remaining is not None and est_days_remaining <= 2.0:
            group = "buy_now"
            reason = (
                f"{c_item.name}: approximately {est_days_remaining:g} days of stock remaining "
                "based on recorded consumption."
            )
        elif stock_status == "running_out_soon":
            group = "buy_soon"
            reason = (
                f"{c_item.name}: approximately {est_days_remaining:g} days of stock remaining "
                "based on recorded consumption."
            )
        elif (
            est_next_date is not None
            and (est_next_date - today_local).days <= 3
            and confidence in ("medium", "high")
            and usable_qty <= (avg_qty or 1) * 0.5
        ):
            group = "buy_soon"
            days_ago = (today_local - last_purchase_date).days if last_purchase_date else 0
            reason = (
                f"{c_item.name} is usually purchased {interval_txt}. Last purchase was "
                f"{days_ago} days ago."
            )
        elif purchase_count >= 2 or stock_status == "adequate":
            group = "regular_purchases"
            if interval_txt and last_txt:
                reason = (
                    f"{c_item.name} is purchased {interval_txt} (last: {last_txt}); "
                    f"{usable_qty:g} {base_unit} remaining."
                )
            else:
                reason = (
                    f"{c_item.name} has {usable_qty:g} {base_unit} in stock; insufficient "
                    "repeat history for interval prediction."
                )
        else:
            group = "regular_purchases"
            reason = "Insufficient purchase and consumption history."

        out = ReplenishmentItemOut(
            canonical_item_id=str(cid),
            item_name=c_item.name,
            category=c_item.category,
            base_unit=base_unit,
            group=group,
            stock_status=stock_status,
            available_qty=available_qty,
            reserved_qty=reserved_qty,
            usable_qty=usable_qty,
            ingredient_usable_qty=ingredient_usable_qty,
            expired_qty=expired_qty,
            normalized_stock_status=norm_stock_status,  # type: ignore[arg-type]
            eligibility_status=eligibility_status,  # type: ignore[arg-type]
            suppression_reason=suppression_reason if not is_actionable_eligible else None,
            low_stock_threshold_qty=threshold,
            earliest_expires_on=earliest_expires,
            purchase_count=purchase_count,
            last_purchase_date=last_purchase_date,
            last_merchant=last_merchant,
            avg_interval_days=avg_interval,
            median_interval_days=median_interval,
            interval_variability_days=interval_std,
            estimated_next_purchase_date=est_next_date,
            avg_daily_consumption=avg_daily_consumption,
            recent_consumption_trend=recent_trend,
            estimated_days_remaining=est_days_remaining,
            prediction_horizon_days=prediction_horizon_days,
            predicted_requirement_qty=predicted_requirement,
            net_requirement_qty=net_requirement,
            recommended_qty=recommended_qty,
            recommended_unit=recommended_unit,
            quantity_basis=quantity_basis,  # type: ignore[arg-type]
            evidence_basis=evidence_basis,  # type: ignore[arg-type]
            unit_mismatch=unit_mismatch,
            unit_clarification_note="; ".join(dict.fromkeys(unit_notes)) if unit_notes else None,
            confidence=confidence,
            confidence_reason=confidence_reason,
            reason=reason,
            active_feedback_action=fb_action,
            remind_after=fb_remind_after,
            dismissal_count=dismissal_count_by_item.get(cid, 0),
        )

        if group == "dismissed":
            dismissed.append(out)
        elif not is_actionable_eligible:
            needs_more_data.append(out)
        elif group == "buy_now":
            buy_now.append(out)
        elif group == "buy_soon":
            buy_soon.append(out)
        else:
            regular_purchases.append(out)

    resp = ReplenishmentRecommendationsResponse(
        generated_at=now_utc,
        buy_now=buy_now,
        buy_soon=buy_soon,
        regular_purchases=regular_purchases,
        dismissed=dismissed,
        needs_more_data=needs_more_data,
        data_quality_issues=data_quality_issues,
        total_tracked_items=len(buy_now) + len(buy_soon) + len(regular_purchases) + len(dismissed),
    )
    if use_cache:
        _RECOMMENDATIONS_CACHE[cache_key] = (now_mono, resp)
    return resp
