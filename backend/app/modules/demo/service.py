"""Read-only service layer and rate limiter for the isolated Public Product Demo API.

Security invariants:
- Resolves DEMO_HOUSEHOLD_ID exclusively from server-side configuration (never request params).
- Sets transaction-scoped PostgreSQL RLS context via ``set_pg_rls_context`` (``is_local=true``).
- Every query filters explicitly on ``household_id == demo_household_id``.
- Returns only explicit allowlisted DTOs; never returns raw ORM models or sensitive fields.
- Zero writes, zero AI/LLM calls, zero OCR calls, zero external network calls.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.core.errors import AppError
from app.core.tenancy import set_pg_rls_context
from app.modules.auth.models import Household, HouseholdMember
from app.modules.cooking.models import CookingPlan, MealHistory
from app.modules.demo.dataset_builder import DEMO_HOUSEHOLD_ID, DEMO_USER_ID
from app.modules.demo.schemas import (
    DemoAnalyticsResponse,
    DemoBudgetOut,
    DemoCategorySpendOut,
    DemoCookingPlanOut,
    DemoDailyMealPlanRecipeOut,
    DemoDailyMealPlanResponse,
    DemoDailyRecipeIngredientOut,
    DemoDailyRecipeStepOut,
    DemoDashboardResponse,
    DemoHouseholdInfoOut,
    DemoInventoryLotOut,
    DemoInventoryResponse,
    DemoInventorySummaryItemOut,
    DemoMealHistoryOut,
    DemoMerchantSpendOut,
    DemoMonthlySpendOut,
    DemoPurchaseItemOut,
    DemoPurchaseOut,
    DemoPurchasesResponse,
    DemoReceiptOut,
    DemoReceiptsResponse,
    DemoRecipePreferencesOut,
    DemoRecipesResponse,
)
from app.modules.discovery.daily_generator import DAILY_CATEGORIES
from app.modules.discovery.daily_models import DailyMealPlan, DailyMealPlanRecipe
from app.modules.discovery.daily_scheduler import compute_household_local_now
from app.modules.discovery.daily_schemas import DailyMealCategory
from app.modules.discovery.daily_service import get_active_meal_category
from app.modules.insights.models import HouseholdBudget
from app.modules.inventory.models import CanonicalItem, InventoryLot
from app.modules.purchases.models import FoodConsumption, Purchase, PurchaseItem
from app.modules.recipes.models import HouseholdRecipePreference


class DemoRateLimiter:
    """Thread-safe in-memory sliding-window per-IP rate limiter for anonymous demo traffic."""

    def __init__(self, max_requests: int = 120, window_seconds: int = 60) -> None:
        self._max_requests = max(1, max_requests)
        self._window_seconds = max(1, window_seconds)
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, client_key: str) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)``."""
        now = time.monotonic()
        cutoff = now - self._window_seconds
        with self._lock:
            bucket = self._buckets[client_key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self._max_requests:
                retry_after = max(1, int(self._window_seconds - (now - bucket[0])) + 1)
                return False, retry_after
            bucket.append(now)
            return True, 0


def resolve_configured_demo_household_id(settings: Settings) -> UUID:
    """Resolve the demo household UUID strictly from server config (never user input)."""
    raw = (settings.DEMO_HOUSEHOLD_ID or "").strip()
    if not raw:
        return DEMO_HOUSEHOLD_ID
    try:
        return UUID(raw)
    except ValueError as exc:
        raise AppError(
            status_code=503,
            code="DEMO_MISCONFIGURED",
            message="Public demo household is not properly configured.",
        ) from exc


async def resolve_demo_household(
    db: AsyncSession,
    settings: Settings,
) -> tuple[Household | None, UUID]:
    """Load the configured demo household and apply transaction-scoped RLS context."""
    if not settings.PUBLIC_DEMO_ENABLED:
        raise AppError(
            status_code=404,
            code="NOT_FOUND",
            message="Public demo mode is not enabled.",
        )

    demo_hh_id = resolve_configured_demo_household_id(settings)

    # Set transaction-scoped RLS context (is_local=true) before querying household tables
    member_user_id = (
        await db.execute(
            select(HouseholdMember.user_id)
            .where(HouseholdMember.household_id == demo_hh_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    await set_pg_rls_context(db, member_user_id or DEMO_USER_ID, demo_hh_id)

    household = (
        await db.execute(select(Household).where(Household.id == demo_hh_id))
    ).scalar_one_or_none()
    return household, demo_hh_id


def _map_purchase_item(item: PurchaseItem) -> DemoPurchaseItemOut:
    return DemoPurchaseItemOut(
        id=item.id,
        line_index=item.line_index,
        display_name=item.display_name,
        normalized_name=item.normalized_name,
        brand=item.brand,
        variant=item.variant,
        pack_size=item.pack_size,
        category=item.category,
        secondary_category=item.secondary_category,
        normalized_qty=item.normalized_qty,
        base_unit=item.base_unit,
        unit_price_paise=item.unit_price_paise,
        line_total_paise=item.line_total_paise,
        discount_paise=item.discount_paise,
        tax_paise=item.tax_paise,
        track_inventory=bool(item.track_inventory),
        expires_on=item.expires_on,
    )


def _map_purchase(purchase: Purchase, *, include_items: bool = True) -> DemoPurchaseOut:
    items_sorted = sorted(purchase.items or [], key=lambda x: x.line_index)
    return DemoPurchaseOut(
        id=purchase.id,
        status=purchase.status,
        source_type=purchase.source_type,
        document_type=purchase.document_type,
        merchant_name=purchase.merchant_name or "Demo Market",
        purchased_on=purchase.purchased_on,
        purchased_time=purchase.purchased_time,
        currency_code=purchase.currency_code,
        receipt_category=purchase.receipt_category,
        subtotal_paise=purchase.subtotal_paise,
        discount_paise=purchase.discount_paise,
        additional_charges_paise=purchase.additional_charges_paise,
        tax_paise=purchase.tax_paise,
        total_amount_paise=purchase.total_amount_paise,
        item_count=len(items_sorted),
        items=[_map_purchase_item(i) for i in items_sorted] if include_items else [],
    )


def _map_receipt(purchase: Purchase, *, include_items: bool = True) -> DemoReceiptOut:
    items_sorted = sorted(purchase.items or [], key=lambda x: x.line_index)
    return DemoReceiptOut(
        id=purchase.id,
        merchant_name=purchase.merchant_name or "Demo Market",
        purchased_on=purchase.purchased_on,
        purchased_time=purchase.purchased_time,
        currency_code=purchase.currency_code,
        receipt_category=purchase.receipt_category,
        document_type=purchase.document_type,
        subtotal_paise=purchase.subtotal_paise,
        discount_paise=purchase.discount_paise,
        tax_paise=purchase.tax_paise,
        total_amount_paise=purchase.total_amount_paise,
        item_count=len(items_sorted),
        items=[_map_purchase_item(i) for i in items_sorted] if include_items else [],
    )


def _map_daily_recipe(row: DailyMealPlanRecipe) -> DemoDailyMealPlanRecipeOut:
    rec = row.recipe_data or {}
    avail = row.availability_data or {}

    def _ing(d: dict[str, Any]) -> DemoDailyRecipeIngredientOut:
        return DemoDailyRecipeIngredientOut(
            name=str(d.get("name") or ""),
            quantity=str(d.get("quantity") or ""),
            base_unit=str(d.get("base_unit") or "pcs"),
            essential=bool(d.get("essential", True)),
            status=str(d.get("status") or "missing"),
            available=bool(d.get("available", False)),
        )

    steps: list[DemoDailyRecipeStepOut] = []
    for idx, st in enumerate(rec.get("structured_steps") or [], start=1):
        if not isinstance(st, dict):
            continue
        steps.append(
            DemoDailyRecipeStepOut(
                step_number=int(st.get("step_number") or idx),
                title=str(st.get("title") or f"Step {idx}"),
                instruction=str(st.get("instruction") or ""),
                ingredients_used=[str(x) for x in (st.get("ingredients_used") or [])],
                duration_minutes=st.get("duration_minutes"),
                heat_level=st.get("heat_level"),
                technique=st.get("technique"),
                equipment=[str(x) for x in (st.get("equipment") or [])],
                completion_cue=str(st.get("completion_cue") or ""),
                caution=st.get("caution"),
                next_action=st.get("next_action"),
            )
        )

    return DemoDailyMealPlanRecipeOut(
        id=row.id,
        meal_category=row.meal_category,  # type: ignore[arg-type]
        position=row.position,
        slug=str(rec.get("slug") or f"{row.meal_category}-{row.position}"),
        title=str(rec.get("title") or "Demo Recipe"),
        description=str(rec.get("description") or ""),
        servings=int(rec.get("servings") or 2),
        prep_minutes=int(rec.get("prep_minutes") or 15),
        cook_minutes=rec.get("cook_minutes"),
        difficulty=rec.get("difficulty"),
        cuisine=rec.get("cuisine"),
        dietary_tags=[str(x) for x in (rec.get("dietary_tags") or [])],
        equipment_required=[str(x) for x in (rec.get("equipment_required") or [])],
        preparation_steps=[str(x) for x in (rec.get("preparation_steps") or [])],
        structured_steps=steps,
        instructions=[str(x) for x in (rec.get("steps") or rec.get("instructions") or [])],
        substitutions=[str(x) for x in (rec.get("substitutions") or [])],
        cooking_tips=[str(x) for x in (rec.get("cooking_tips") or [])],
        troubleshooting_tips=[str(x) for x in (rec.get("troubleshooting_tips") or [])],
        serving_result=rec.get("serving_result"),
        availability_bucket=avail.get("availability_bucket", "explore_more"),
        available_ingredients=[_ing(x) for x in (avail.get("available_ingredients") or [])],
        partial_ingredients=[_ing(x) for x in (avail.get("partial_ingredients") or [])],
        missing_ingredients=[_ing(x) for x in (avail.get("missing_ingredients") or [])],
        optional_ingredients=[_ing(x) for x in (avail.get("optional_ingredients") or [])],
        quality_status=row.quality_status,
    )


async def get_demo_inventory(
    db: AsyncSession,
    demo_hh_id: UUID,
    *,
    category: str | None = None,
    lot_status: str = "active",
    expiring_within_days: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> DemoInventoryResponse:
    today = datetime.now(UTC).date()
    exp_cutoff = (
        today + timedelta(days=expiring_within_days)
        if expiring_within_days is not None
        else today + timedelta(days=7)
    )

    items_stmt = select(CanonicalItem).where(
        CanonicalItem.household_id == demo_hh_id,
        CanonicalItem.is_active.is_(True),
        CanonicalItem.pantry_eligible.is_(True),
    )
    if category:
        items_stmt = items_stmt.where(CanonicalItem.category == category)
    items = list((await db.execute(items_stmt.order_by(CanonicalItem.name.asc()))).scalars().all())
    item_by_id = {ci.id: ci for ci in items}

    lots_stmt = select(InventoryLot).where(InventoryLot.household_id == demo_hh_id)
    if lot_status != "all":
        lots_stmt = lots_stmt.where(InventoryLot.lot_status == lot_status)
    all_lots = list(
        (
            await db.execute(
                lots_stmt.order_by(
                    InventoryLot.expires_on.asc().nulls_last(),
                    InventoryLot.purchased_at.desc(),
                )
            )
        )
        .scalars()
        .all()
    )

    # Aggregate summary per canonical_item_id
    lots_by_ci: dict[UUID, list[InventoryLot]] = defaultdict(list)
    for lot in all_lots:
        lots_by_ci[lot.canonical_item_id].append(lot)

    summary_out: list[DemoInventorySummaryItemOut] = []
    for ci in items:
        ci_lots = lots_by_ci.get(ci.id, [])
        if not ci_lots:
            continue
        total_qty = sum((lot.remaining_qty for lot in ci_lots), Decimal("0"))
        exp_dates = [lot.expires_on for lot in ci_lots if lot.expires_on is not None]
        earliest = min(exp_dates) if exp_dates else None
        summary_out.append(
            DemoInventorySummaryItemOut(
                canonical_item_id=ci.id,
                name=ci.name,
                slug=ci.slug,
                category=ci.category,
                secondary_category=ci.secondary_category,
                total_remaining_qty=total_qty,
                base_unit=ci.default_base_unit,
                active_lot_count=len(ci_lots),
                earliest_expires_on=earliest,
                has_expired_lots=any(d < today for d in exp_dates),
            )
        )

    filtered_lots: list[InventoryLot] = []
    expiring_soon_count = 0
    for lot in all_lots:
        if lot.canonical_item_id not in item_by_id:
            continue
        is_exp_soon = lot.expires_on is not None and lot.expires_on <= exp_cutoff
        if is_exp_soon:
            expiring_soon_count += 1
        if expiring_within_days is not None and not is_exp_soon:
            continue
        filtered_lots.append(lot)

    paged_lots = filtered_lots[offset : offset + limit]
    lots_out = [
        DemoInventoryLotOut(
            id=lot.id,
            canonical_item_id=lot.canonical_item_id,
            item_name=item_by_id[lot.canonical_item_id].name,
            category=item_by_id[lot.canonical_item_id].category,
            initial_qty=lot.initial_qty,
            remaining_qty=lot.remaining_qty,
            base_unit=lot.base_unit,
            unit_cost_paise=lot.unit_cost_paise,
            purchased_at=lot.purchased_at,
            expires_on=lot.expires_on,
            lot_status=lot.lot_status,
        )
        for lot in paged_lots
    ]

    return DemoInventoryResponse(
        summary=summary_out,
        lots=lots_out,
        total_items=len(summary_out),
        total_lots=len(filtered_lots),
        expiring_soon_count=expiring_soon_count,
        limit=limit,
        offset=offset,
    )


async def get_demo_purchases(
    db: AsyncSession,
    demo_hh_id: UUID,
    *,
    limit: int = 20,
    offset: int = 0,
    include_items: bool = True,
) -> DemoPurchasesResponse:
    total = int(
        (
            await db.execute(
                select(func.count(Purchase.id)).where(
                    Purchase.household_id == demo_hh_id,
                    Purchase.status != "draft",
                )
            )
        ).scalar_one()
    )
    rows = list(
        (
            await db.execute(
                select(Purchase)
                .options(selectinload(Purchase.items))
                .where(
                    Purchase.household_id == demo_hh_id,
                    Purchase.status != "draft",
                )
                .order_by(Purchase.purchased_on.desc(), Purchase.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return DemoPurchasesResponse(
        items=[_map_purchase(p, include_items=include_items) for p in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


async def get_demo_purchase_detail(
    db: AsyncSession,
    demo_hh_id: UUID,
    purchase_id: UUID,
) -> DemoPurchaseOut:
    purchase = (
        await db.execute(
            select(Purchase)
            .options(selectinload(Purchase.items))
            .where(
                Purchase.id == purchase_id,
                Purchase.household_id == demo_hh_id,
                Purchase.status != "draft",
            )
        )
    ).scalar_one_or_none()
    if purchase is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="Demo purchase not found.")
    return _map_purchase(purchase, include_items=True)


async def get_demo_receipts(
    db: AsyncSession,
    demo_hh_id: UUID,
    *,
    limit: int = 20,
    offset: int = 0,
) -> DemoReceiptsResponse:
    total = int(
        (
            await db.execute(
                select(func.count(Purchase.id)).where(
                    Purchase.household_id == demo_hh_id,
                    Purchase.status != "draft",
                )
            )
        ).scalar_one()
    )
    rows = list(
        (
            await db.execute(
                select(Purchase)
                .options(selectinload(Purchase.items))
                .where(
                    Purchase.household_id == demo_hh_id,
                    Purchase.status != "draft",
                )
                .order_by(Purchase.purchased_on.desc(), Purchase.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return DemoReceiptsResponse(
        items=[_map_receipt(p, include_items=True) for p in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


async def get_demo_receipt_detail(
    db: AsyncSession,
    demo_hh_id: UUID,
    receipt_id: UUID,
) -> DemoReceiptOut:
    purchase = (
        await db.execute(
            select(Purchase)
            .options(selectinload(Purchase.items))
            .where(
                Purchase.id == receipt_id,
                Purchase.household_id == demo_hh_id,
                Purchase.status != "draft",
            )
        )
    ).scalar_one_or_none()
    if purchase is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="Demo receipt not found.")
    return _map_receipt(purchase, include_items=True)


async def get_demo_daily_plan(
    db: AsyncSession,
    demo_hh_id: UUID,
    *,
    category: DailyMealCategory | None = None,
) -> DemoDailyMealPlanResponse:
    pref_tz = (
        await db.execute(
            select(HouseholdRecipePreference.timezone).where(
                HouseholdRecipePreference.household_id == demo_hh_id
            )
        )
    ).scalar_one_or_none()
    resolved_tz, local_dt = compute_household_local_now(pref_tz)
    active_cat = category or get_active_meal_category(local_dt)

    # Select latest ready plan for demo household (never triggers LLM; works on any date)
    plan = (
        await db.execute(
            select(DailyMealPlan)
            .options(selectinload(DailyMealPlan.recipes))
            .where(
                DailyMealPlan.household_id == demo_hh_id,
                DailyMealPlan.status == "ready",
            )
            .order_by(DailyMealPlan.local_date.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if plan is None:
        return DemoDailyMealPlanResponse(
            status="empty",
            local_date=local_dt.date(),
            timezone=resolved_tz,
            active_meal_category=active_cat,
        )

    categories: dict[DailyMealCategory, list[DemoDailyMealPlanRecipeOut]] = {
        cat: [] for cat in DAILY_CATEGORIES
    }
    recipes_out: list[DemoDailyMealPlanRecipeOut] = []
    for row in plan.recipes:
        if category is not None and row.meal_category != category:
            continue
        mapped = _map_daily_recipe(row)
        recipes_out.append(mapped)
        if row.meal_category in categories:
            categories[row.meal_category].append(mapped)  # type: ignore[index]

    return DemoDailyMealPlanResponse(
        status="ready",
        local_date=plan.local_date,
        timezone=plan.timezone,
        active_meal_category=active_cat,
        generated_at=plan.generation_completed_at,
        categories=categories,
        recipes=recipes_out,
    )


async def get_demo_recipes(
    db: AsyncSession,
    demo_hh_id: UUID,
    *,
    limit: int = 20,
    offset: int = 0,
) -> DemoRecipesResponse:
    pref = (
        await db.execute(
            select(HouseholdRecipePreference).where(
                HouseholdRecipePreference.household_id == demo_hh_id
            )
        )
    ).scalar_one_or_none()
    prefs_out = (
        DemoRecipePreferencesOut(
            timezone=pref.timezone,
            diet_type=pref.diet_type,
            dietary_restrictions=list(pref.dietary_restrictions or []),
            preferred_cuisines=list(pref.preferred_cuisines or []),
            preferred_meal_types=list(pref.preferred_meal_types or []),
            max_prep_minutes=pref.max_prep_minutes,
            recommendation_mode=pref.recommendation_mode,
        )
        if pref is not None
        else DemoRecipePreferencesOut()
    )

    daily = await get_demo_daily_plan(db, demo_hh_id)

    total_cp = int(
        (
            await db.execute(
                select(func.count(CookingPlan.id)).where(CookingPlan.household_id == demo_hh_id)
            )
        ).scalar_one()
    )
    cp_rows = list(
        (
            await db.execute(
                select(CookingPlan)
                .where(CookingPlan.household_id == demo_hh_id)
                .order_by(CookingPlan.started_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    total_mh = int(
        (
            await db.execute(
                select(func.count(MealHistory.id)).where(MealHistory.household_id == demo_hh_id)
            )
        ).scalar_one()
    )
    mh_rows = list(
        (
            await db.execute(
                select(MealHistory)
                .where(MealHistory.household_id == demo_hh_id)
                .order_by(MealHistory.cooked_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )

    return DemoRecipesResponse(
        preferences=prefs_out,
        daily_recipes=daily.recipes,
        cooking_plans=[
            DemoCookingPlanOut(
                id=cp.id,
                recipe_title=cp.recipe_title,
                status=cp.status,
                planned_servings=cp.planned_servings,
                meal_type=cp.meal_type,
                cuisine=cp.cuisine,
                prep_minutes=cp.prep_minutes,
                cook_minutes=cp.cook_minutes,
                started_at=cp.started_at,
                completed_at=cp.completed_at,
            )
            for cp in cp_rows
        ],
        meal_history=[
            DemoMealHistoryOut(
                id=mh.id,
                recipe_title=mh.recipe_title,
                meal_type=mh.meal_type,
                cuisine=mh.cuisine,
                servings=mh.servings,
                prep_minutes=mh.prep_minutes,
                cook_minutes=mh.cook_minutes,
                cooked_at=mh.cooked_at,
                rating=mh.rating,
                feedback_tags=list(mh.feedback_tags or []),
                would_make_again=mh.would_make_again,
            )
            for mh in mh_rows
        ],
        total_cooking_plans=total_cp,
        total_meal_history=total_mh,
    )


async def get_demo_analytics(
    db: AsyncSession,
    demo_hh_id: UUID,
    *,
    currency_code: str = "INR",
    year: int | None = None,
    month: int | None = None,
) -> DemoAnalyticsResponse:
    p_stmt = select(Purchase).where(
        Purchase.household_id == demo_hh_id,
        Purchase.status != "draft",
    )
    purchases = list((await db.execute(p_stmt)).scalars().all())
    if year is not None:
        purchases = [p for p in purchases if p.purchased_on.year == year]
    if month is not None:
        purchases = [p for p in purchases if p.purchased_on.month == month]

    p_ids = [p.id for p in purchases]
    items: list[PurchaseItem] = []
    if p_ids:
        items = list(
            (
                await db.execute(
                    select(PurchaseItem).where(
                        PurchaseItem.household_id == demo_hh_id,
                        PurchaseItem.purchase_id.in_(p_ids),
                    )
                )
            )
            .scalars()
            .all()
        )

    fc_rows = list(
        (
            await db.execute(
                select(FoodConsumption).where(FoodConsumption.household_id == demo_hh_id)
            )
        )
        .scalars()
        .all()
    )
    if year is not None:
        fc_rows = [f for f in fc_rows if f.consumed_on.year == year]
    if month is not None:
        fc_rows = [f for f in fc_rows if f.consumed_on.month == month]
    fc_by_purchase = {f.purchase_id for f in fc_rows}

    total_spend = 0
    grocery_spend = 0
    outside_spend = 0
    merchant_totals: dict[str, list[int]] = {}
    monthly_buckets: dict[tuple[int, int], list[int]] = {}

    for p in purchases:
        amt = int(p.total_amount_paise or 0)
        is_outside = p.receipt_category == "restaurant_food" or p.id in fc_by_purchase
        total_spend += amt
        if is_outside:
            outside_spend += amt
        else:
            grocery_spend += amt

        m_name = p.merchant_name or "Demo Market"
        m_entry = merchant_totals.setdefault(m_name, [0, 0])
        m_entry[0] += amt
        m_entry[1] += 1

        ym = (p.purchased_on.year, p.purchased_on.month)
        ym_entry = monthly_buckets.setdefault(ym, [0, 0, 0, 0])
        ym_entry[0] += amt
        if is_outside:
            ym_entry[2] += amt
        else:
            ym_entry[1] += amt
        ym_entry[3] += 1

    cat_totals: dict[str, list[int]] = {}
    for it in items:
        cat = it.category or "general"
        c_entry = cat_totals.setdefault(cat, [0, 0])
        c_entry[0] += int(it.line_total_paise or 0)
        c_entry[1] += 1

    budgets = list(
        (
            await db.execute(
                select(HouseholdBudget)
                .where(HouseholdBudget.household_id == demo_hh_id)
                .order_by(HouseholdBudget.effective_from.desc())
            )
        )
        .scalars()
        .all()
    )

    return DemoAnalyticsResponse(
        currency_code=currency_code,
        total_spend_paise=total_spend,
        grocery_spend_paise=grocery_spend,
        outside_food_spend_paise=outside_spend,
        purchase_count=len(purchases),
        category_breakdown=[
            DemoCategorySpendOut(category=cat, total_paise=vals[0], item_count=vals[1])
            for cat, vals in sorted(cat_totals.items(), key=lambda kv: kv[1][0], reverse=True)
        ],
        merchant_breakdown=[
            DemoMerchantSpendOut(merchant_name=m, total_paise=vals[0], purchase_count=vals[1])
            for m, vals in sorted(merchant_totals.items(), key=lambda kv: kv[1][0], reverse=True)
        ],
        monthly_trend=[
            DemoMonthlySpendOut(
                year=ym[0],
                month=ym[1],
                total_paise=vals[0],
                grocery_paise=vals[1],
                outside_food_paise=vals[2],
                purchase_count=vals[3],
            )
            for ym, vals in sorted(monthly_buckets.items())
        ],
        budgets=[
            DemoBudgetOut(
                id=b.id,
                period_type=b.period_type,
                overall_limit_paise=b.overall_limit_paise,
                grocery_limit_paise=b.grocery_limit_paise,
                outside_food_limit_paise=b.outside_food_limit_paise,
                effective_from=b.effective_from,
            )
            for b in budgets
        ],
    )


async def get_demo_dashboard(
    db: AsyncSession,
    household: Household | None,
    demo_hh_id: UUID,
) -> DemoDashboardResponse:
    hh_info = DemoHouseholdInfoOut(
        name=household.name if household is not None else "Demo Kitchen",
        currency_code=household.currency_code if household is not None else "INR",
    )
    inv_all = await get_demo_inventory(
        db, demo_hh_id, lot_status="active", limit=1, offset=0
    )
    inv_exp = await get_demo_inventory(
        db, demo_hh_id, lot_status="active", expiring_within_days=7, limit=10, offset=0
    )
    recent_p = await get_demo_purchases(db, demo_hh_id, limit=5, offset=0, include_items=False)
    daily = await get_demo_daily_plan(db, demo_hh_id)
    analytics = await get_demo_analytics(db, demo_hh_id, currency_code=hh_info.currency_code)

    return DemoDashboardResponse(
        household=hh_info,
        inventory_item_count=inv_all.total_items,
        active_lot_count=inv_all.total_lots,
        expiring_soon_lots=inv_exp.lots,
        recent_purchases=recent_p.items,
        daily_plan=daily,
        analytics=analytics,
    )
