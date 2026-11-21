from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class InventorySummaryItemOut(BaseModel):
    canonical_item_id: UUID
    name: str
    slug: str
    category: str
    secondary_category: str | None = None
    total_remaining_qty: Decimal
    base_unit: str
    active_lot_count: int
    earliest_expires_on: date | None
    has_expired_lots: bool


class InventorySummaryResponse(BaseModel):
    items: list[InventorySummaryItemOut]


class CatalogItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str
    category: str
    secondary_category: str | None = None
    default_base_unit: str
    default_shelf_life_days: int | None = None
    low_stock_threshold_qty: Decimal = Decimal("0")
    is_custom: bool = True


class CatalogItemsResponse(BaseModel):
    items: list[CatalogItemOut]
    total: int
    limit: int
    offset: int


class CreateCatalogItemRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(default="general", min_length=1, max_length=64)
    default_base_unit: Literal["g", "ml", "pcs"] = "pcs"
    default_shelf_life_days: int | None = Field(default=None, gt=0)
    low_stock_threshold_qty: Decimal = Field(default=Decimal("0"), ge=0)


class CreateInventoryLotRequest(BaseModel):
    canonical_item_id: UUID
    remaining_qty: Decimal = Field(gt=0)
    base_unit: Literal["g", "ml", "pcs"]
    unit_cost_paise: int = Field(default=0, ge=0)
    expires_on: date | None = None


class InventoryLotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    household_id: UUID
    canonical_item_id: UUID
    canonical_item_name: str | None = None
    item_name: str | None = None
    purchase_item_id: UUID | None
    initial_qty: Decimal
    remaining_qty: Decimal
    base_unit: str
    unit_cost_paise: int
    purchased_at: datetime
    received_at: datetime | None = None
    expires_on: date | None
    lot_status: str
    status: str | None = None


class InventoryLotsResponse(BaseModel):
    items: list[InventoryLotOut]


class AdjustLotRequest(BaseModel):
    event_type: Literal["ADJUSTMENT_IN", "ADJUSTMENT_OUT", "WASTE_OUT"]
    quantity_delta: Decimal
    notes: str | None = Field(default=None, max_length=500)


class StockLedgerEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    household_id: UUID
    lot_id: UUID
    canonical_item_id: UUID
    event_type: str
    quantity_delta: Decimal
    base_unit: str
    balance_after_lot: Decimal
    unit_cost_paise: int
    reference_type: str | None
    reference_id: UUID | None
    actor_user_id: UUID
    notes: str | None
    created_at: datetime


class AdjustLotResponse(BaseModel):
    lot: InventoryLotOut
    ledger_entry: StockLedgerEntryOut


class StockLedgerResponse(BaseModel):
    items: list[StockLedgerEntryOut]


class IngredientUnitGroupOut(BaseModel):
    unit: str
    total_qty: Decimal
    lot_count: int


class IngredientLotDetailOut(BaseModel):
    lot_id: UUID
    canonical_item_id: UUID
    purchase_item_id: UUID | None
    initial_qty: Decimal
    remaining_qty: Decimal
    base_unit: str
    converted_qty: Decimal | None = None
    converted_unit: str | None = None
    unit_cost_paise: int
    purchased_at: datetime
    expires_on: date | None
    lot_status: str


class IngredientProductBreakdownOut(BaseModel):
    canonical_item_id: UUID
    name: str
    category: str
    base_unit: str
    total_remaining_qty: Decimal
    active_lot_count: int
    has_expired_lots: bool
    earliest_expires_on: date | None
    mapping_source: str
    matching_confidence: Decimal
    lots: list[IngredientLotDetailOut]


class IngredientSummaryOut(BaseModel):
    canonical_ingredient_id: UUID
    name: str
    normalized_name: str
    category: str
    subcategory: str | None = None
    standard_unit: str
    total_remaining_qty: Decimal | None
    unit_completeness_status: Literal["complete", "incomplete", "empty"]
    unit_groups: list[IngredientUnitGroupOut]
    contributing_product_count: int
    lot_count: int
    has_expired_lots: bool
    earliest_expires_on: date | None
    calculation_note: str
    products: list[IngredientProductBreakdownOut]


class UnmappedPantryProductOut(BaseModel):
    canonical_item_id: UUID
    name: str
    category: str
    base_unit: str
    total_remaining_qty: Decimal
    active_lot_count: int
    suggestion_status: Literal["unmapped", "unconfirmed_suggestion"]
    suggested_ingredient_id: UUID | None = None
    suggested_ingredient_name: str | None = None
    suggestion_reason: str | None = None


class IngredientInventorySummaryResponse(BaseModel):
    ingredients: list[IngredientSummaryOut]
    unmapped_products_count: int
    unmapped_lots_count: int
    unconfirmed_suggestions_count: int
    mapping_review_count: int = 0
    mapping_review_items: list[UnmappedPantryProductOut] = Field(default_factory=list)
    unmapped_products: list[UnmappedPantryProductOut]


class ConfirmIngredientMappingRequest(BaseModel):
    canonical_item_id: UUID
    canonical_ingredient_id: UUID
    allow_override: bool = False


class ConfirmIngredientMappingResponse(BaseModel):
    mapping_id: UUID
    canonical_item_id: UUID
    canonical_ingredient_id: UUID
    canonical_ingredient_name: str
    user_confirmed: bool
    mapping_source: str
    matching_confidence: Decimal


class RejectIngredientMappingRequest(BaseModel):
    canonical_item_id: UUID


class RejectIngredientMappingResponse(BaseModel):
    canonical_item_id: UUID
    rejected: bool


class UpdateCanonicalItemNameRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class UpdateCanonicalItemNameResponse(BaseModel):
    canonical_item_id: UUID
    name: str


class UpdateIngredientPantryItemRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    target_quantity: Decimal | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, min_length=1, max_length=16)


class UpdateIngredientPantryItemResponse(BaseModel):
    canonical_ingredient_id: UUID
    name: str
    standard_unit: str
    total_remaining_qty: Decimal
    adjusted_lots_count: int = 0


class CanonicalIngredientOptionOut(BaseModel):
    id: UUID
    name: str
    normalized_name: str
    category: str
    standard_unit: str


class CanonicalIngredientsListResponse(BaseModel):
    items: list[CanonicalIngredientOptionOut]
