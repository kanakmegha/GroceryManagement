from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field

MealType = Literal["breakfast", "lunch", "dinner", "snacks", "multiple", "other"]
DiningMode = Literal["restaurant", "takeaway", "delivery", "other"]


class FoodConsumptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    purchase_id: UUID
    consumed_on: date
    meal_type: str
    dining_mode: str
    merchant_name: str | None = None
    amount_paise: int
    currency_code: str
    items_summary: list[dict] | None = None
    people_count: int | None = None
    notes: str | None = None


class IngredientMappingConfirmationInput(BaseModel):
    canonical_item_id: UUID
    canonical_ingredient_id: UUID
    allow_override: bool = False


class ConfirmPurchaseRequest(BaseModel):
    meal_type: MealType | None = None
    dining_mode: DiningMode | None = None
    people_count: int | None = Field(default=None, ge=1, le=50)
    consumed_on: date | None = None
    notes: str | None = Field(default=None, max_length=500)
    ingredient_mappings: list[IngredientMappingConfirmationInput] = Field(default_factory=list)
    enforce_ingredient_review: bool = False


class PurchaseItemInput(BaseModel):
    raw_text: str = ""
    canonical_item_id: UUID | None = None
    display_name: str = Field(default="Item", min_length=1, max_length=200)
    normalized_name: str | None = Field(default=None, max_length=200)
    brand: str | None = Field(default=None, max_length=120)
    variant: str | None = Field(default=None, max_length=120)
    pack_size: str | None = Field(default=None, max_length=64)
    category: str = Field(default="general", min_length=1, max_length=64)
    secondary_category: str | None = Field(default=None, max_length=64)
    qty: Decimal | None = Field(default=None, gt=0)
    normalized_qty: Decimal | None = Field(default=None, gt=0)
    unit: str | None = Field(default=None, min_length=1, max_length=32)
    base_unit: str | None = Field(default=None, min_length=1, max_length=32)
    unit_price_paise: int = Field(default=0, ge=0)
    line_total_paise: int = Field(default=0, ge=0)
    discount_paise: int = Field(default=0, ge=0)
    tax_paise: int = Field(default=0, ge=0)
    tax_rate_bps: int | None = Field(default=None, ge=0)
    track_inventory: bool = True
    food_state: str | None = None
    budget_treatment: str | None = None
    classification_confidence: float | None = None
    matching_method: str | None = None
    classification_source: str | None = None
    expires_on: date | None = None
    canonical_ingredient_id: UUID | None = None
    ingredient_variant_id: str | None = None
    user_approved_ingredient: bool = False


class CreatePurchaseRequest(BaseModel):
    merchant_name: str | None = Field(default=None, max_length=200)
    receipt_number: str | None = Field(default=None, max_length=64)
    purchased_on: date | None = None
    purchased_time: str | None = Field(default=None, max_length=32)
    currency_code: str = Field(default="INR", min_length=3, max_length=3)
    receipt_category: str = Field(default="grocery", max_length=32)
    payment_method: str | None = Field(default=None, max_length=32)
    subtotal_paise: int | None = Field(default=None, ge=0)
    discount_paise: int = Field(default=0, ge=0)
    additional_charges_paise: int = Field(default=0, ge=0)
    cgst_paise: int = Field(default=0, ge=0)
    sgst_paise: int = Field(default=0, ge=0)
    igst_paise: int = Field(default=0, ge=0)
    other_tax_paise: int = Field(default=0, ge=0)
    tax_paise: int | None = Field(default=None, ge=0)
    rounding_paise: int = 0
    total_amount_paise: int | None = Field(default=None, ge=0)
    items: list[PurchaseItemInput] = Field(min_length=1)


class UpdatePurchaseItemInput(BaseModel):
    id: UUID | None = None
    raw_text: str | None = None
    canonical_item_id: UUID | None = None
    display_name: str = Field(default="Item", min_length=1, max_length=200)
    normalized_name: str | None = Field(default=None, max_length=200)
    brand: str | None = Field(default=None, max_length=120)
    variant: str | None = Field(default=None, max_length=120)
    pack_size: str | None = Field(default=None, max_length=64)
    category: str = Field(default="general", min_length=1, max_length=64)
    secondary_category: str | None = Field(default=None, max_length=64)
    qty: Decimal | None = Field(default=None, gt=0)
    normalized_qty: Decimal | None = Field(default=None, gt=0)
    unit: str | None = Field(default=None, min_length=1, max_length=32)
    base_unit: str | None = Field(default=None, min_length=1, max_length=32)
    unit_price_paise: int = Field(default=0, ge=0)
    line_total_paise: int = Field(default=0, ge=0)
    discount_paise: int = Field(default=0, ge=0)
    tax_paise: int = Field(default=0, ge=0)
    tax_rate_bps: int | None = Field(default=None, ge=0)
    track_inventory: bool | None = None
    expires_on: date | None = None
    canonical_ingredient_id: UUID | None = None
    ingredient_variant_id: str | None = None
    user_approved_ingredient: bool = False


class UpdatePurchaseRequest(BaseModel):
    merchant_name: str | None = Field(default=None, max_length=200)
    receipt_number: str | None = Field(default=None, max_length=64)
    purchased_on: date | None = None
    purchased_time: str | None = Field(default=None, max_length=32)
    receipt_category: str | None = Field(default=None, max_length=32)
    payment_method: str | None = Field(default=None, max_length=32)
    subtotal_paise: int | None = Field(default=None, ge=0)
    discount_paise: int | None = Field(default=None, ge=0)
    additional_charges_paise: int | None = Field(default=None, ge=0)
    cgst_paise: int | None = Field(default=None, ge=0)
    sgst_paise: int | None = Field(default=None, ge=0)
    igst_paise: int | None = Field(default=None, ge=0)
    other_tax_paise: int | None = Field(default=None, ge=0)
    tax_paise: int | None = Field(default=None, ge=0)
    rounding_paise: int | None = None
    total_amount_paise: int | None = Field(default=None, ge=0)
    items: list[UpdatePurchaseItemInput] | None = None


class OcrJsonRequest(BaseModel):
    raw_text: str = Field(min_length=1)
    merchant_name: str | None = Field(default=None, max_length=200)
    purchased_on: date | None = None


class PurchaseItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    line_index: int
    raw_text: str
    canonical_item_id: UUID | None
    display_name: str
    normalized_name: str | None = None
    brand: str | None = None
    variant: str | None = None
    pack_size: str | None = None
    category: str
    secondary_category: str | None = None
    match_confidence: str
    matching_confidence: float | None = None
    eligibility_reason: str | None = None
    unit_price_provenance: str | None = None
    normalized_qty: Decimal
    base_unit: str
    unit_price_paise: int
    line_total_paise: int
    discount_paise: int = 0
    tax_paise: int = 0
    tax_rate_bps: int | None = None
    confidence: float | None = None
    warnings: list[str] = Field(default_factory=list)
    track_inventory: bool = True
    food_state: str | None = None
    budget_treatment: str | None = None
    classification_confidence: float | None = None
    matching_method: str | None = None
    classification_source: str | None = None
    expires_on: date | None
    standardized_name: str | None = None
    ingredient_family_id: str | None = None
    ingredient_family_name: str | None = None
    ingredient_variant_id: str | None = None
    canonical_ingredient_id: UUID | None = None
    ingredient_category: str | None = None
    is_unspecified_variant: bool = False
    ingredient_confidence_tier: str | None = None
    ingredient_confidence_score: int | None = None
    ingredient_match_source: str | None = None
    ingredient_review_reason: str | None = None
    user_approved_ingredient: bool = False
    ingredient_suggestions: list[dict] = Field(default_factory=list)
    quantity_confidence_tier: str | None = None
    quantity_review_reason: str | None = None
    financial_mismatch: bool = False
    financial_review_reason: str | None = None
    supported_units: list[str] = Field(default_factory=list)

    @computed_field
    @property
    def classification_status(self) -> str:
        if self.budget_treatment == "household_non_food" or self.category == "non_food":
            return "non_food"
        if self.budget_treatment == "outside_food_expenditure" or self.food_state == "cooked":
            return "prepared_meal"
        if self.budget_treatment == "requires_user_review" and not self.track_inventory:
            return "ambiguous_review"
        return "pantry_eligible" if self.track_inventory else "prepared_meal"

    @computed_field
    @property
    def review_reason(self) -> str | None:
        if self.warnings:
            for w in self.warnings:
                if "review" in w.lower() or "non-food" in w.lower() or "unrecognized" in w.lower():
                    return w
        if self.classification_status == "ambiguous_review":
            return "Unrecognized or low-confidence item; review before adding to pantry."
        if self.classification_status == "non_food":
            return "Non-food product; excluded from pantry inventory."
        return None


class PurchaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    household_id: UUID
    status: str
    source_type: str
    document_type: str = "unknown"
    ocr_adapter: str | None
    merchant_name: str | None
    receipt_number: str | None = None
    order_id: str | None = None
    purchased_on: date
    purchased_time: str | None = None
    currency_code: str
    receipt_category: str = "grocery"
    payment_method: str | None = None
    subtotal_paise: int | None = None
    discount_paise: int = 0
    additional_charges_paise: int = 0
    cgst_paise: int = 0
    sgst_paise: int = 0
    igst_paise: int = 0
    other_tax_paise: int = 0
    tax_paise: int = 0
    rounding_paise: int = 0
    total_amount_paise: int
    confirmed_at: datetime | None
    created_at: datetime
    items: list[PurchaseItemOut]
    ocr_warnings: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    attachments: list[dict] = Field(default_factory=list)
    math_reconciled: bool | None = None
    ocr_confidence: float | None = None
    ocr_orchestration: dict | None = None
    food_consumption: FoodConsumptionOut | None = None


class PurchaseListResponse(BaseModel):
    items: list[PurchaseOut]
    total: int


class CreatedLotSummary(BaseModel):
    lot_id: UUID
    canonical_item_id: UUID
    remaining_qty: Decimal
    base_unit: str
    expires_on: date | None


class PurchaseIngredientMappingOut(BaseModel):
    canonical_item_id: UUID
    status: str
    tier: str
    canonical_ingredient_id: UUID | None = None
    canonical_ingredient_name: str | None = None
    matching_confidence: Decimal = Decimal("0.000")
    mapping_source: str | None = None
    user_confirmed: bool = False
    detected_variant: str | None = None


class ConfirmPurchaseResponse(BaseModel):
    purchase_id: UUID
    draft_id: UUID
    status: str
    total_amount_paise: int
    created_lots: list[CreatedLotSummary]
    food_consumption: FoodConsumptionOut | None = None
    ingredient_mappings: list[PurchaseIngredientMappingOut] = Field(default_factory=list)
