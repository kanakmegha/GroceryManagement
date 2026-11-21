from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

ConfidenceLevel = Literal["insufficient_data", "low", "medium", "high"]
StockStatus = Literal["depleted", "low_stock", "running_out_soon", "adequate", "unknown"]
NormalizedStockStatus = Literal["SUFFICIENT", "LOW", "OUT_OF_STOCK", "UNKNOWN"]
EligibilityStatus = Literal["ELIGIBLE", "SUPPRESSED"]
RecommendationGroup = Literal["buy_now", "buy_soon", "regular_purchases", "dismissed"]
FeedbackAction = Literal[
    "purchased",
    "already_have_enough",
    "dismissed",
    "not_relevant",
    "remind_later",
]
ShoppingListSourceType = Literal[
    "depleted",
    "low_stock",
    "predicted_replenishment",
    "regular_purchase",
    "recipe_missing",
    "manual",
]
ShoppingListStatus = Literal["pending", "purchased", "dismissed"]


class ReplenishmentItemOut(BaseModel):
    canonical_item_id: str
    item_name: str
    category: str
    base_unit: str
    group: RecommendationGroup
    stock_status: StockStatus
    available_qty: float
    reserved_qty: float = 0.0
    usable_qty: float
    ingredient_usable_qty: float | None = None
    expired_qty: float = 0.0
    normalized_stock_status: NormalizedStockStatus = "UNKNOWN"
    eligibility_status: EligibilityStatus = "SUPPRESSED"
    suppression_reason: str | None = None
    low_stock_threshold_qty: float = 0.0
    earliest_expires_on: date | None = None

    purchase_count: int = 0
    last_purchase_date: date | None = None
    last_merchant: str | None = None
    avg_interval_days: float | None = None
    median_interval_days: float | None = None
    interval_variability_days: float | None = None
    estimated_next_purchase_date: date | None = None

    avg_daily_consumption: float | None = None
    recent_consumption_trend: str | None = None
    estimated_days_remaining: float | None = None
    prediction_horizon_days: int = 7
    predicted_requirement_qty: float | None = None
    net_requirement_qty: float | None = None

    recommended_qty: float | None = None
    recommended_unit: str | None = None
    quantity_basis: Literal[
        "consumption_prediction", "historical_pattern", "user_target", "insufficient_data"
    ] = "insufficient_data"
    evidence_basis: Literal[
        "consumption_based", "pattern_based", "user_target_based", "insufficient_data"
    ] = "insufficient_data"
    unit_mismatch: bool = False
    unit_clarification_note: str | None = None

    confidence: ConfidenceLevel
    confidence_reason: str
    reason: str

    active_feedback_action: FeedbackAction | None = None
    remind_after: date | None = None
    dismissal_count: int = 0


class DataQualityIssueOut(BaseModel):
    canonical_item_id: str
    item_name: str
    issue_type: Literal["excluded_non_food", "uncertain_identity", "unit_incompatibility"]
    detail: str


class ReplenishmentRecommendationsResponse(BaseModel):
    generated_at: datetime
    buy_now: list[ReplenishmentItemOut] = Field(default_factory=list)
    buy_soon: list[ReplenishmentItemOut] = Field(default_factory=list)
    regular_purchases: list[ReplenishmentItemOut] = Field(default_factory=list)
    dismissed: list[ReplenishmentItemOut] = Field(default_factory=list)
    needs_more_data: list[ReplenishmentItemOut] = Field(default_factory=list)
    data_quality_issues: list[DataQualityIssueOut] = Field(default_factory=list)
    total_tracked_items: int = 0


class ReplenishmentFeedbackRequest(BaseModel):
    action: FeedbackAction
    remind_days: int | None = Field(default=3, ge=1, le=90)
    reason_snapshot: str | None = None


class ReplenishmentFeedbackOut(BaseModel):
    id: str
    canonical_item_id: str
    action: FeedbackAction
    remind_after: date | None = None
    is_active: bool
    created_at: datetime


class ShoppingListSourceEntry(BaseModel):
    source: ShoppingListSourceType
    ref_id: str | None = None
    added_qty: float | None = None
    added_unit: str | None = None
    added_at: str


class ShoppingListItemCreate(BaseModel):
    canonical_item_id: str | None = None
    item_name: str = Field(..., min_length=1, max_length=255)
    quantity: float | None = Field(default=None, gt=0)
    unit: str | None = Field(default=None, max_length=32)
    source: ShoppingListSourceType = "manual"
    source_ref_id: str | None = None
    notes: str | None = None


class ShoppingListItemUpdate(BaseModel):
    status: ShoppingListStatus | None = None
    quantity: float | None = Field(default=None, gt=0)
    unit: str | None = Field(default=None, max_length=32)
    notes: str | None = None


class ShoppingListItemOut(BaseModel):
    id: str
    household_id: str
    canonical_item_id: str | None = None
    item_name: str
    quantity: float | None = None
    unit: str | None = None
    primary_source: ShoppingListSourceType
    sources: list[ShoppingListSourceEntry] = Field(default_factory=list)
    notes: str | None = None
    status: ShoppingListStatus
    unit_conflict_note: str | None = None
    created_at: datetime
    updated_at: datetime


class ShoppingListResponse(BaseModel):
    pending: list[ShoppingListItemOut] = Field(default_factory=list)
    purchased: list[ShoppingListItemOut] = Field(default_factory=list)
    dismissed: list[ShoppingListItemOut] = Field(default_factory=list)
