from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

BudgetPeriodType = Literal["daily", "weekly", "monthly", "yearly"]
BudgetStatusLevel = Literal["within_budget", "approaching_limit", "fully_used", "exceeded"]


class CategorySpendOut(BaseModel):
    category: str
    total_paise: int


class MerchantSpendOut(BaseModel):
    merchant_name: str
    total_paise: int
    purchase_count: int


class ProductSpendOut(BaseModel):
    product_name: str
    category: str
    total_paise: int
    total_qty: float
    base_unit: str
    avg_unit_price_paise: int
    latest_unit_price_paise: int
    price_change_paise: int = 0


class SpendingTrendOut(BaseModel):
    date: str
    total_paise: int
    purchase_count: int


class MealSpendOut(BaseModel):
    meal_type: str
    total_paise: int
    count: int


class PeriodPurchaseOut(BaseModel):
    id: str
    merchant_name: str
    purchased_on: str
    receipt_category: str
    total_amount_paise: int
    grocery_paise: int
    outside_food_paise: int
    unclassified_paise: int = 0
    discount_paise: int = 0
    tax_paise: int = 0
    additional_charges_paise: int = 0
    item_count: int = 0


class BudgetStatusOut(BaseModel):
    limit_paise: int
    actual_spend_paise: int
    remaining_paise: int
    exceeded_paise: int
    percentage_used: float
    status: BudgetStatusLevel


class PeriodBudgetEvaluationOut(BaseModel):
    id: str
    period_type: BudgetPeriodType
    effective_from: date
    overall: BudgetStatusOut
    grocery: BudgetStatusOut | None = None
    outside_food: BudgetStatusOut | None = None


class BudgetUpsertRequest(BaseModel):
    period_type: BudgetPeriodType
    overall_limit_paise: int = Field(..., gt=0, description="Overall budget limit in paise (> 0)")
    grocery_limit_paise: int | None = Field(default=None, gt=0, description="Optional grocery limit in paise (> 0)")
    outside_food_limit_paise: int | None = Field(default=None, gt=0, description="Optional outside-food limit in paise (> 0)")
    effective_from: date | None = None


class BudgetConfigOut(BaseModel):
    id: str
    household_id: str
    period_type: BudgetPeriodType
    overall_limit_paise: int
    grocery_limit_paise: int | None = None
    outside_food_limit_paise: int | None = None
    effective_from: date
    created_at: datetime
    updated_at: datetime


class BudgetListResponse(BaseModel):
    items: list[BudgetConfigOut] = Field(default_factory=list)
    current_by_period: dict[str, BudgetConfigOut] = Field(default_factory=dict)


class InsightsSummaryResponse(BaseModel):
    period_days: int
    total_spend_paise: int
    total_spend_display: str
    total_waste_paise: int
    total_waste_display: str
    expiring_soon_count: int
    spend_by_category: list[CategorySpendOut]
    # Extended Budget & Accounting Breakdown (from confirmed purchases only)
    spend_today_paise: int = 0
    spend_this_week_paise: int = 0
    spend_this_month_paise: int = 0
    gross_subtotal_paise: int = 0
    total_discounts_paise: int = 0
    total_tax_paise: int = 0
    total_cgst_paise: int = 0
    total_sgst_paise: int = 0
    total_igst_paise: int = 0
    total_additional_charges_paise: int = 0
    total_rounding_paise: int = 0
    final_amount_paid_paise: int = 0
    purchase_count: int = 0
    average_purchase_paise: int = 0
    estimated_replenishment_paise: int = 0
    spend_by_merchant: list[MerchantSpendOut] = Field(default_factory=list)
    spend_by_product: list[ProductSpendOut] = Field(default_factory=list)
    spending_trends: list[SpendingTrendOut] = Field(default_factory=list)
    outside_food_spend_paise: int = 0
    grocery_spend_paise: int = 0
    outside_food_count: int = 0
    spend_by_meal: list[MealSpendOut] = Field(default_factory=list)
    previous_period_spend_paise: int | None = None
    previous_grocery_spend_paise: int | None = None
    previous_outside_food_spend_paise: int | None = None
    selected_year: int | None = None
    selected_month: int | None = None
    period_type: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    elapsed_days: int = 0
    average_daily_spend_paise: int = 0
    unclassified_spend_paise: int = 0
    accounting_discrepancy_paise: int = 0
    budget: PeriodBudgetEvaluationOut | None = None
    period_purchases: list[PeriodPurchaseOut] = Field(default_factory=list)
    all_time_spend_paise: int = 0
    all_time_grocery_spend_paise: int = 0
    all_time_outside_food_spend_paise: int = 0
