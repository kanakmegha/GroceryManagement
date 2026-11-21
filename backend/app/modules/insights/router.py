from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db import get_db
from app.core.money import paise_to_inr_display
from app.core.tenancy import HouseholdContext, get_household_context
from app.modules.insights.models import HouseholdBudget
from app.modules.insights.schemas import (
    BudgetConfigOut,
    BudgetListResponse,
    BudgetPeriodType,
    BudgetStatusLevel,
    BudgetStatusOut,
    BudgetUpsertRequest,
    CategorySpendOut,
    InsightsSummaryResponse,
    MealSpendOut,
    MerchantSpendOut,
    PeriodBudgetEvaluationOut,
    PeriodPurchaseOut,
    ProductSpendOut,
    SpendingTrendOut,
)
from app.modules.inventory.models import InventoryLot, StockLedger
from app.modules.purchases.models import FoodConsumption, Purchase, PurchaseItem
from app.modules.purchases.schemas import FoodConsumptionOut

router = APIRouter(prefix="/insights", tags=["insights"])


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    """Return inclusive month start and exclusive next-month start."""
    start = date(year, month, 1)
    end_exclusive = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, end_exclusive


def _week_bounds(anchor: date) -> tuple[date, date]:
    """Return inclusive Monday start and exclusive next-Monday end."""
    start = anchor - timedelta(days=anchor.weekday())
    return start, start + timedelta(days=7)


def _year_bounds(year: int) -> tuple[date, date]:
    """Return inclusive Jan 1 start and exclusive next-year Jan 1 end."""
    return date(year, 1, 1), date(year + 1, 1, 1)


def _purchase_category_split(
    p: Purchase,
    fc_by_pid: dict[str, list[FoodConsumption]],
) -> tuple[int, int, int]:
    """Return (grocery_paise, outside_food_paise, unclassified_paise) without double-counting."""
    p_total = int(p.total_amount_paise)
    linked_fcs = fc_by_pid.get(p.id)
    if linked_fcs:
        outside = sum(int(fc.amount_paise) for fc in linked_fcs)
        grocery = max(0, p_total - outside) if p.receipt_category == "mixed" else max(0, p_total - outside)
        return grocery, outside, 0

    cat = str(p.receipt_category or "").strip().lower()
    if cat == "restaurant":
        return 0, p_total, 0
    if cat == "mixed":
        outside = 0
        unclassified = 0
        for it in p.items:
            bt = str(it.budget_treatment or "").strip().lower()
            fs = str(it.food_state or "").strip().lower()
            if bt == "outside_food_expenditure" or fs == "cooked":
                outside += int(it.line_total_paise)
            elif bt == "uncertain" or (not bt and fs in ("", "uncertain")):
                unclassified += int(it.line_total_paise)
        grocery = max(0, p_total - outside - unclassified)
        return grocery, outside, unclassified
    if cat == "grocery":
        return p_total, 0, 0
    return 0, 0, p_total


def _purchase_outside_food_paise(
    p: Purchase,
    fc_by_pid: dict[str, list[FoodConsumption]],
) -> int:
    """Compute outside-food expenditure for a confirmed purchase without double-counting."""
    _, outside, _ = _purchase_category_split(p, fc_by_pid)
    return outside


def _evaluate_budget_status(limit_paise: int, actual_spend_paise: int) -> BudgetStatusOut:
    """Evaluate budget utilisation and status using exact integer threshold comparisons."""
    remaining_paise = limit_paise - actual_spend_paise
    exceeded_paise = max(0, actual_spend_paise - limit_paise)
    percentage_used = round((actual_spend_paise / limit_paise) * 100, 2)
    if actual_spend_paise > limit_paise:
        level: BudgetStatusLevel = "exceeded"
    elif actual_spend_paise == limit_paise:
        level = "fully_used"
    elif actual_spend_paise * 100 >= 80 * limit_paise:
        level = "approaching_limit"
    else:
        level = "within_budget"
    return BudgetStatusOut(
        limit_paise=limit_paise,
        actual_spend_paise=actual_spend_paise,
        remaining_paise=remaining_paise,
        exceeded_paise=exceeded_paise,
        percentage_used=percentage_used,
        status=level,
    )


def _canonical_effective_from(period_type: BudgetPeriodType, anchor: date) -> date:
    """Normalize any date to the canonical start date of its period."""
    if period_type == "daily":
        return anchor
    if period_type == "weekly":
        return _week_bounds(anchor)[0]
    if period_type == "monthly":
        return date(anchor.year, anchor.month, 1)
    return date(anchor.year, 1, 1)


def _budget_to_out(b: HouseholdBudget) -> BudgetConfigOut:
    return BudgetConfigOut(
        id=str(b.id),
        household_id=str(b.household_id),
        period_type=b.period_type,  # type: ignore[arg-type]
        overall_limit_paise=int(b.overall_limit_paise),
        grocery_limit_paise=int(b.grocery_limit_paise) if b.grocery_limit_paise is not None else None,
        outside_food_limit_paise=(
            int(b.outside_food_limit_paise) if b.outside_food_limit_paise is not None else None
        ),
        effective_from=b.effective_from,
        created_at=b.created_at,
        updated_at=b.updated_at,
    )


async def _find_applicable_budget(
    db: AsyncSession,
    household_id: UUID,
    period_type: str,
    period_start: date,
) -> HouseholdBudget | None:
    stmt = (
        select(HouseholdBudget)
        .where(
            HouseholdBudget.household_id == household_id,
            HouseholdBudget.period_type == period_type,
            HouseholdBudget.effective_from <= period_start,
        )
        .order_by(HouseholdBudget.effective_from.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalars().first()


@router.get("/summary", response_model=InsightsSummaryResponse)
async def insights_summary(
    period: Literal["daily", "weekly", "monthly", "yearly"] | None = Query(default=None),
    date_str: str | None = Query(default=None, alias="date", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    days: int | None = Query(default=None, ge=1, le=365),
    year: int | None = Query(default=None, ge=2000, le=2100),
    month: int | None = Query(default=None, ge=1, le=12),
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> InsightsSummaryResponse:
    tz_name = getattr(ctx.household, "timezone", None) or "Asia/Kolkata"
    try:
        local_tz = ZoneInfo(tz_name)
    except Exception:
        local_tz = ZoneInfo("Asia/Kolkata")

    now = datetime.now(local_tz)
    today = now.date()
    week_since = today - timedelta(days=today.weekday())
    month_since = today.replace(day=1)
    soon_date = today + timedelta(days=3)
    anchor_date = date.fromisoformat(date_str) if date_str else today

    effective_period: str
    if period == "daily":
        effective_period = "daily"
        sel_year = anchor_date.year
        sel_month = anchor_date.month
        since_date = anchor_date
        end_exclusive = anchor_date + timedelta(days=1)
        prev_since = anchor_date - timedelta(days=1)
        prev_end = anchor_date
        effective_days = 1
        since_dt = datetime(since_date.year, since_date.month, since_date.day, tzinfo=local_tz).astimezone(UTC)
    elif period == "weekly":
        effective_period = "weekly"
        since_date, end_exclusive = _week_bounds(anchor_date)
        sel_year = since_date.year
        sel_month = since_date.month
        prev_since = since_date - timedelta(days=7)
        prev_end = since_date
        effective_days = 7
        since_dt = datetime(since_date.year, since_date.month, since_date.day, tzinfo=local_tz).astimezone(UTC)
    elif period == "yearly":
        effective_period = "yearly"
        sel_year = year if year is not None else anchor_date.year
        sel_month = None
        since_date, end_exclusive = _year_bounds(sel_year)
        prev_since, prev_end = _year_bounds(sel_year - 1)
        effective_days = (end_exclusive - since_date).days
        since_dt = datetime(since_date.year, since_date.month, since_date.day, tzinfo=local_tz).astimezone(UTC)
    elif period == "monthly" or year is not None or month is not None or days is None:
        effective_period = "monthly"
        sel_year = year if year is not None else (anchor_date.year if date_str else today.year)
        sel_month = month if month is not None else (anchor_date.month if date_str else today.month)
        since_date, end_exclusive = _month_bounds(sel_year, sel_month)
        prev_year = sel_year - 1 if sel_month == 1 else sel_year
        prev_month = 12 if sel_month == 1 else sel_month - 1
        prev_since, prev_end = _month_bounds(prev_year, prev_month)
        effective_days = (end_exclusive - since_date).days
        since_dt = datetime(since_date.year, since_date.month, since_date.day, tzinfo=local_tz).astimezone(UTC)
    else:
        effective_period = "rolling"
        sel_year = None
        sel_month = None
        since_date = today - timedelta(days=days)
        end_exclusive = today + timedelta(days=1)
        prev_since = since_date - timedelta(days=days)
        prev_end = since_date
        effective_days = days
        since_dt = now.astimezone(UTC) - timedelta(days=days)

    # Fetch all confirmed purchases for household so monthly, prev-month, and all-time totals reconcile cleanly
    confirmed_stmt = (
        select(Purchase)
        .options(selectinload(Purchase.items))
        .where(
            Purchase.household_id == ctx.household.id,
            Purchase.status == "confirmed",
        )
        .order_by(Purchase.purchased_on.asc(), Purchase.created_at.asc())
    )
    all_confirmed = (await db.execute(confirmed_stmt)).scalars().all()

    spend_today_paise = sum(
        int(p.total_amount_paise) for p in all_confirmed if p.purchased_on == today
    )
    spend_this_week_paise = sum(
        int(p.total_amount_paise) for p in all_confirmed if p.purchased_on >= week_since
    )
    spend_this_month_paise = sum(
        int(p.total_amount_paise) for p in all_confirmed if p.purchased_on >= month_since
    )

    period_purchases = [
        p for p in all_confirmed if since_date <= p.purchased_on < end_exclusive
    ]
    total_spend_paise = sum(int(p.total_amount_paise) for p in period_purchases)

    gross_subtotal_paise = 0
    total_discounts_paise = 0
    total_tax_paise = 0
    total_cgst_paise = 0
    total_sgst_paise = 0
    total_igst_paise = 0
    total_additional_charges_paise = 0
    total_rounding_paise = 0

    category_totals: dict[str, int] = defaultdict(int)
    merchant_stats: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # [total, count]
    daily_trends: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # [total, count]
    product_history: dict[tuple[str, str], list[tuple[int, float, int]]] = defaultdict(list)

    for p in period_purchases:
        items_sum = sum(int(it.line_total_paise) for it in p.items)
        gross_subtotal_paise += int(p.subtotal_paise) if p.subtotal_paise is not None else items_sum
        total_discounts_paise += int(p.discount_paise or 0)
        total_cgst_paise += int(p.cgst_paise or 0)
        total_sgst_paise += int(p.sgst_paise or 0)
        total_igst_paise += int(p.igst_paise or 0)
        total_tax_paise += int(p.tax_paise or 0)
        total_additional_charges_paise += int(p.additional_charges_paise or 0)
        total_rounding_paise += int(p.rounding_paise or 0)

        m_name = (p.merchant_name or "Unknown Merchant").strip() or "Unknown Merchant"
        merchant_stats[m_name][0] += int(p.total_amount_paise)
        merchant_stats[m_name][1] += 1

        d_key = p.purchased_on.isoformat()
        daily_trends[d_key][0] += int(p.total_amount_paise)
        daily_trends[d_key][1] += 1

        for it in p.items:
            cat = str(it.category or "general").strip().lower()
            category_totals[cat] += int(it.line_total_paise)
            p_key = ((it.normalized_name or it.display_name).strip(), cat)
            qty_f = float(it.normalized_qty or 1)
            unit_p = int(it.unit_price_paise or 0)
            if unit_p == 0 and qty_f > 0:
                unit_p = round(int(it.line_total_paise) / qty_f)
            product_history[p_key].append((int(it.line_total_paise), qty_f, unit_p))

    spend_by_category = [
        CategorySpendOut(category=cat, total_paise=amt)
        for cat, amt in sorted(category_totals.items(), key=lambda kv: kv[1], reverse=True)
    ]
    spend_by_merchant = [
        MerchantSpendOut(
            merchant_name=m,
            total_paise=vals[0],
            purchase_count=vals[1],
        )
        for m, vals in sorted(merchant_stats.items(), key=lambda kv: kv[1][0], reverse=True)
    ]

    spend_by_product: list[ProductSpendOut] = []
    for (prod_name, cat), entries in product_history.items():
        tot_paise = sum(e[0] for e in entries)
        tot_qty = sum(e[1] for e in entries)
        avg_unit = round(sum(e[2] for e in entries) / len(entries)) if entries else 0
        latest_unit = entries[-1][2] if entries else 0
        first_unit = entries[0][2] if entries else 0
        price_change = latest_unit - first_unit if len(entries) >= 2 else 0
        # Find base_unit from last matching item
        base_unit = "pcs"
        for p in reversed(period_purchases):
            for it in p.items:
                if (it.normalized_name or it.display_name).strip() == prod_name:
                    base_unit = it.base_unit
                    break
            else:
                continue
            break
        spend_by_product.append(
            ProductSpendOut(
                product_name=prod_name,
                category=cat,
                total_paise=tot_paise,
                total_qty=round(tot_qty, 3),
                base_unit=base_unit,
                avg_unit_price_paise=avg_unit,
                latest_unit_price_paise=latest_unit,
                price_change_paise=price_change,
            )
        )
    spend_by_product.sort(key=lambda x: x.total_paise, reverse=True)

    if effective_period == "weekly":
        spending_trends = []
        for offset in range(7):
            d_iso = (since_date + timedelta(days=offset)).isoformat()
            vals = daily_trends.get(d_iso, [0, 0])
            spending_trends.append(
                SpendingTrendOut(date=d_iso, total_paise=vals[0], purchase_count=vals[1])
            )
    elif effective_period == "yearly" and sel_year is not None:
        monthly_totals: dict[str, list[int]] = {
            f"{sel_year}-{m:02d}": [0, 0] for m in range(1, 13)
        }
        for p in period_purchases:
            m_key = f"{p.purchased_on.year}-{p.purchased_on.month:02d}"
            if m_key in monthly_totals:
                monthly_totals[m_key][0] += int(p.total_amount_paise)
                monthly_totals[m_key][1] += 1
        spending_trends = [
            SpendingTrendOut(date=m_key, total_paise=vals[0], purchase_count=vals[1])
            for m_key, vals in sorted(monthly_totals.items())
        ]
    else:
        spending_trends = [
            SpendingTrendOut(date=d, total_paise=vals[0], purchase_count=vals[1])
            for d, vals in sorted(daily_trends.items())
        ]

    waste_stmt = select(StockLedger).where(
        StockLedger.household_id == ctx.household.id,
        StockLedger.event_type == "WASTE_OUT",
        StockLedger.created_at >= since_dt,
    )
    waste_entries = (await db.execute(waste_stmt)).scalars().all()
    total_waste_paise = sum(int(entry.unit_cost_paise) for entry in waste_entries)

    expiring_stmt = select(func.count()).select_from(InventoryLot).where(
        and_(
            InventoryLot.household_id == ctx.household.id,
            InventoryLot.lot_status == "active",
            InventoryLot.remaining_qty > Decimal("0"),
            InventoryLot.expires_on.is_not(None),
            InventoryLot.expires_on >= today,
            InventoryLot.expires_on <= soon_date,
        )
    )
    expiring_soon_count = int((await db.execute(expiring_stmt)).scalar_one())

    # Estimated replenishment expenditure for active lots below 25% of initial_qty or expiring soon
    low_lots_stmt = select(InventoryLot).where(
        and_(
            InventoryLot.household_id == ctx.household.id,
            InventoryLot.lot_status == "active",
            InventoryLot.remaining_qty > Decimal("0"),
        )
    )
    active_lots = (await db.execute(low_lots_stmt)).scalars().all()
    estimated_replenishment_paise = 0
    for lot in active_lots:
        is_low = lot.initial_qty > 0 and (lot.remaining_qty / lot.initial_qty) <= Decimal("0.25")
        is_expiring = lot.expires_on is not None and lot.expires_on <= soon_date
        if is_low or is_expiring:
            estimated_replenishment_paise += int(lot.unit_cost_paise or 0)

    purchase_count = len(period_purchases)
    average_purchase_paise = (
        round(total_spend_paise / purchase_count) if purchase_count > 0 else 0
    )
    if end_exclusive <= today:
        elapsed_days = effective_days
    elif since_date <= today < end_exclusive:
        elapsed_days = (today - since_date).days + 1
    else:
        elapsed_days = effective_days
    average_daily_spend_paise = (
        round(total_spend_paise / elapsed_days) if elapsed_days > 0 else 0
    )

    all_fc_rows = (
        await db.execute(
            select(FoodConsumption).where(FoodConsumption.household_id == ctx.household.id)
        )
    ).scalars().all()
    fc_by_pid: dict[str, list[FoodConsumption]] = defaultdict(list)
    for fc in all_fc_rows:
        fc_by_pid[fc.purchase_id].append(fc)

    grocery_spend_paise = 0
    outside_food_spend_paise = 0
    unclassified_spend_paise = 0
    outside_food_count = 0
    meal_totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    period_purchase_outs: list[PeriodPurchaseOut] = []

    for p in period_purchases:
        p_grocery, p_outside, p_unclassified = _purchase_category_split(p, fc_by_pid)
        p_total = int(p.total_amount_paise)
        grocery_spend_paise += p_grocery
        unclassified_spend_paise += p_unclassified

        linked_fcs = fc_by_pid.get(p.id)
        if linked_fcs:
            outside_food_spend_paise += p_outside
            outside_food_count += len(linked_fcs)
            for fc in linked_fcs:
                mt = str(fc.meal_type or "unknown")
                meal_totals[mt][0] += int(fc.amount_paise)
                meal_totals[mt][1] += 1
        elif p_outside > 0:
            outside_food_spend_paise += p_outside
            outside_food_count += 1
            meal_totals["unknown"][0] += p_outside
            meal_totals["unknown"][1] += 1

        period_purchase_outs.append(
            PeriodPurchaseOut(
                id=str(p.id),
                merchant_name=(p.merchant_name or "Unknown Merchant").strip() or "Unknown Merchant",
                purchased_on=p.purchased_on.isoformat(),
                receipt_category=str(p.receipt_category or "grocery"),
                total_amount_paise=p_total,
                grocery_paise=p_grocery,
                outside_food_paise=p_outside,
                unclassified_paise=p_unclassified,
                discount_paise=int(p.discount_paise or 0),
                tax_paise=int(p.tax_paise or 0),
                additional_charges_paise=int(p.additional_charges_paise or 0),
                item_count=len(p.items),
            )
        )

    accounting_discrepancy_paise = total_spend_paise - (
        grocery_spend_paise + outside_food_spend_paise
    )
    spend_by_meal = [
        MealSpendOut(meal_type=mt, total_paise=vals[0], count=vals[1])
        for mt, vals in sorted(meal_totals.items(), key=lambda kv: kv[1][0], reverse=True)
    ]

    applicable_budget = (
        await _find_applicable_budget(db, ctx.household.id, effective_period, since_date)
        if effective_period in ("daily", "weekly", "monthly", "yearly")
        else None
    )
    budget_evaluation: PeriodBudgetEvaluationOut | None = None
    if applicable_budget is not None:
        budget_evaluation = PeriodBudgetEvaluationOut(
            id=str(applicable_budget.id),
            period_type=applicable_budget.period_type,  # type: ignore[arg-type]
            effective_from=applicable_budget.effective_from,
            overall=_evaluate_budget_status(
                int(applicable_budget.overall_limit_paise),
                total_spend_paise,
            ),
            grocery=(
                _evaluate_budget_status(
                    int(applicable_budget.grocery_limit_paise),
                    grocery_spend_paise,
                )
                if applicable_budget.grocery_limit_paise is not None
                else None
            ),
            outside_food=(
                _evaluate_budget_status(
                    int(applicable_budget.outside_food_limit_paise),
                    outside_food_spend_paise,
                )
                if applicable_budget.outside_food_limit_paise is not None
                else None
            ),
        )

    # Previous period comparison (only when confirmed purchases exist in the previous window)
    prev_purchases = [
        p for p in all_confirmed if prev_since <= p.purchased_on < prev_end
    ]
    prev_period_spend_paise: int | None = None
    prev_grocery_paise: int | None = None
    prev_outside_paise: int | None = None
    if prev_purchases:
        prev_period_spend_paise = sum(int(p.total_amount_paise) for p in prev_purchases)
        prev_outside_paise = sum(
            _purchase_outside_food_paise(p, fc_by_pid) for p in prev_purchases
        )
        prev_grocery_paise = max(0, prev_period_spend_paise - prev_outside_paise)

    # All-time totals (deduplicated by purchase_id)
    all_time_spend_paise = sum(int(p.total_amount_paise) for p in all_confirmed)
    all_time_outside_food_spend_paise = sum(
        _purchase_outside_food_paise(p, fc_by_pid) for p in all_confirmed
    )
    all_time_grocery_spend_paise = max(0, all_time_spend_paise - all_time_outside_food_spend_paise)

    return InsightsSummaryResponse(
        period_days=effective_days,
        total_spend_paise=total_spend_paise,
        total_spend_display=paise_to_inr_display(total_spend_paise),
        total_waste_paise=total_waste_paise,
        total_waste_display=paise_to_inr_display(total_waste_paise),
        expiring_soon_count=expiring_soon_count,
        spend_by_category=spend_by_category,
        spend_today_paise=spend_today_paise,
        spend_this_week_paise=spend_this_week_paise,
        spend_this_month_paise=spend_this_month_paise,
        gross_subtotal_paise=gross_subtotal_paise,
        total_discounts_paise=total_discounts_paise,
        total_tax_paise=total_tax_paise,
        total_cgst_paise=total_cgst_paise,
        total_sgst_paise=total_sgst_paise,
        total_igst_paise=total_igst_paise,
        total_additional_charges_paise=total_additional_charges_paise,
        total_rounding_paise=total_rounding_paise,
        final_amount_paid_paise=total_spend_paise,
        purchase_count=purchase_count,
        average_purchase_paise=average_purchase_paise,
        estimated_replenishment_paise=estimated_replenishment_paise,
        spend_by_merchant=spend_by_merchant,
        spend_by_product=spend_by_product,
        spending_trends=spending_trends,
        outside_food_spend_paise=outside_food_spend_paise,
        grocery_spend_paise=grocery_spend_paise,
        outside_food_count=outside_food_count,
        spend_by_meal=spend_by_meal,
        previous_period_spend_paise=prev_period_spend_paise,
        previous_grocery_spend_paise=prev_grocery_paise,
        previous_outside_food_spend_paise=prev_outside_paise,
        selected_year=sel_year,
        selected_month=sel_month,
        period_type=effective_period,
        period_start=since_date.isoformat(),
        period_end=(end_exclusive - timedelta(days=1)).isoformat(),
        elapsed_days=elapsed_days,
        average_daily_spend_paise=average_daily_spend_paise,
        unclassified_spend_paise=unclassified_spend_paise,
        accounting_discrepancy_paise=accounting_discrepancy_paise,
        budget=budget_evaluation,
        period_purchases=list(reversed(period_purchase_outs)),
        all_time_spend_paise=all_time_spend_paise,
        all_time_grocery_spend_paise=all_time_grocery_spend_paise,
        all_time_outside_food_spend_paise=all_time_outside_food_spend_paise,
    )


@router.get("/outside-food", response_model=list[FoodConsumptionOut])
async def list_outside_food(
    days: int = Query(default=30, ge=1, le=365),
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> list[FoodConsumptionOut]:
    since_date = datetime.now(UTC).date() - timedelta(days=days)
    stmt = (
        select(FoodConsumption)
        .where(
            FoodConsumption.household_id == ctx.household.id,
            FoodConsumption.consumed_on >= since_date,
        )
        .order_by(FoodConsumption.consumed_on.desc(), FoodConsumption.created_at.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [FoodConsumptionOut.model_validate(r) for r in rows]


def _resolve_budget_effective_date(
    period_type: BudgetPeriodType,
    effective_from: date | None,
    tz_name: str,
) -> date:
    if effective_from is not None:
        return _canonical_effective_from(period_type, effective_from)
    try:
        local_tz = ZoneInfo(tz_name or "Asia/Kolkata")
    except Exception:
        local_tz = ZoneInfo("Asia/Kolkata")
    today = datetime.now(local_tz).date()
    return _canonical_effective_from(period_type, today)


def _validate_budget_limits(payload: BudgetUpsertRequest) -> None:
    if (
        payload.grocery_limit_paise is not None
        and payload.grocery_limit_paise > payload.overall_limit_paise
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="grocery_limit_paise cannot exceed overall_limit_paise.",
        )
    if (
        payload.outside_food_limit_paise is not None
        and payload.outside_food_limit_paise > payload.overall_limit_paise
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="outside_food_limit_paise cannot exceed overall_limit_paise.",
        )
    if (
        payload.grocery_limit_paise is not None
        and payload.outside_food_limit_paise is not None
        and (payload.grocery_limit_paise + payload.outside_food_limit_paise)
        > payload.overall_limit_paise
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Combined grocery_limit_paise and outside_food_limit_paise cannot exceed overall_limit_paise.",
        )


async def _upsert_budget_record(
    payload: BudgetUpsertRequest,
    ctx: HouseholdContext,
    db: AsyncSession,
) -> BudgetConfigOut:
    _validate_budget_limits(payload)
    tz_name = getattr(ctx.household, "timezone", None) or "Asia/Kolkata"
    canonical_start = _resolve_budget_effective_date(
        payload.period_type,
        payload.effective_from,
        tz_name,
    )
    existing = (
        await db.execute(
            select(HouseholdBudget).where(
                HouseholdBudget.household_id == ctx.household.id,
                HouseholdBudget.period_type == payload.period_type,
                HouseholdBudget.effective_from == canonical_start,
            )
        )
    ).scalars().first()

    if existing is not None:
        existing.overall_limit_paise = payload.overall_limit_paise
        existing.grocery_limit_paise = payload.grocery_limit_paise
        existing.outside_food_limit_paise = payload.outside_food_limit_paise
        existing.updated_by = ctx.user.id
        await db.commit()
        await db.refresh(existing)
        return _budget_to_out(existing)

    record = HouseholdBudget(
        household_id=ctx.household.id,
        period_type=payload.period_type,
        overall_limit_paise=payload.overall_limit_paise,
        grocery_limit_paise=payload.grocery_limit_paise,
        outside_food_limit_paise=payload.outside_food_limit_paise,
        effective_from=canonical_start,
        created_by=ctx.user.id,
        updated_by=ctx.user.id,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return _budget_to_out(record)


@router.get("/budgets", response_model=BudgetListResponse)
async def list_budgets(
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> BudgetListResponse:
    rows = (
        await db.execute(
            select(HouseholdBudget)
            .where(HouseholdBudget.household_id == ctx.household.id)
            .order_by(HouseholdBudget.period_type.asc(), HouseholdBudget.effective_from.desc())
        )
    ).scalars().all()
    items = [_budget_to_out(r) for r in rows]

    tz_name = getattr(ctx.household, "timezone", None) or "Asia/Kolkata"
    try:
        local_tz = ZoneInfo(tz_name)
    except Exception:
        local_tz = ZoneInfo("Asia/Kolkata")
    today = datetime.now(local_tz).date()

    current_by_period: dict[str, BudgetConfigOut] = {}
    for pt in ("daily", "weekly", "monthly", "yearly"):
        canon = _canonical_effective_from(pt, today)  # type: ignore[arg-type]
        applicable = await _find_applicable_budget(db, ctx.household.id, pt, canon)
        if applicable is not None:
            current_by_period[pt] = _budget_to_out(applicable)

    return BudgetListResponse(items=items, current_by_period=current_by_period)


@router.get("/budgets/applicable", response_model=BudgetConfigOut | None)
async def get_applicable_budget(
    period: BudgetPeriodType = Query(...),
    date_str: str | None = Query(default=None, alias="date", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    year: int | None = Query(default=None, ge=2000, le=2100),
    month: int | None = Query(default=None, ge=1, le=12),
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> BudgetConfigOut | None:
    tz_name = getattr(ctx.household, "timezone", None) or "Asia/Kolkata"
    try:
        local_tz = ZoneInfo(tz_name)
    except Exception:
        local_tz = ZoneInfo("Asia/Kolkata")
    today = datetime.now(local_tz).date()

    if date_str:
        anchor = date.fromisoformat(date_str)
    elif period == "monthly" and (year is not None or month is not None):
        anchor = date(year or today.year, month or today.month, 1)
    elif period == "yearly" and year is not None:
        anchor = date(year, 1, 1)
    else:
        anchor = today

    canon = _canonical_effective_from(period, anchor)
    applicable = await _find_applicable_budget(db, ctx.household.id, period, canon)
    return _budget_to_out(applicable) if applicable is not None else None


@router.post("/budgets", response_model=BudgetConfigOut, status_code=status.HTTP_201_CREATED)
async def create_or_update_budget(
    payload: BudgetUpsertRequest,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> BudgetConfigOut:
    return await _upsert_budget_record(payload, ctx, db)


@router.put("/budgets/{period_type}", response_model=BudgetConfigOut)
async def upsert_budget_for_period(
    period_type: BudgetPeriodType,
    payload: BudgetUpsertRequest,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> BudgetConfigOut:
    if payload.period_type != period_type:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="period_type in URL must match payload.period_type.",
        )
    return await _upsert_budget_record(payload, ctx, db)


@router.delete("/budgets/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_budget(
    budget_id: UUID,
    ctx: HouseholdContext = Depends(get_household_context),
    db: AsyncSession = Depends(get_db),
) -> None:
    row = (
        await db.execute(
            select(HouseholdBudget).where(
                HouseholdBudget.id == budget_id,
                HouseholdBudget.household_id == ctx.household.id,
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Budget configuration not found.",
        )
    await db.delete(row)
    await db.commit()
