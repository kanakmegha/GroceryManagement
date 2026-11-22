"""V10.8 Extraction Data Contract schemas for Experimental V10.6 OCR."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")

ProvenanceType = Literal["OBSERVED", "DERIVED", "INFERRED", "UNAVAILABLE", "AMBIGUOUS"]
ReviewStatusType = Literal["ACCEPTED", "NEEDS_REVIEW", "REJECTED"]
FeeStatusType = Literal["CHARGED", "WAIVED", "DISCOUNTED", "UNKNOWN"]
ExtractModeType = Literal["experimental", "existing", "compare"]


class FieldEnvelope(BaseModel, Generic[T]):
    value: T | None = None
    provenance: ProvenanceType = "UNAVAILABLE"
    source_text_or_evidence_ref: str | None = None
    confidence: float = 0.0
    review_status: ReviewStatusType = "NEEDS_REVIEW"


class ExperimentalGstBreakdown(BaseModel):
    hsn: str | None = None
    taxable_value: float | None = None
    cgst_rate: float | None = None
    cgst_amount: float | None = None
    sgst_rate: float | None = None
    sgst_amount: float | None = None
    igst_rate: float | None = None
    igst_amount: float | None = None
    cess_rate: float | None = None
    cess_amount: float | None = None
    observed_tax_total: float | None = None
    derived_tax_total: float | None = None
    tax_provenance: ProvenanceType = "UNAVAILABLE"


class ExperimentalItem(BaseModel):
    line_index: int
    product_name: FieldEnvelope[str]
    quantity: FieldEnvelope[float]
    unit_of_measure: FieldEnvelope[str]
    package_size: FieldEnvelope[str]
    unit_price: FieldEnvelope[float]
    mrp: FieldEnvelope[float]
    selling_price: FieldEnvelope[float]
    line_total: FieldEnvelope[float]
    item_discount: FieldEnvelope[float]
    gst: ExperimentalGstBreakdown = Field(default_factory=ExperimentalGstBreakdown)
    price_presentation: str = "unknown"


class ExperimentalFee(BaseModel):
    fee_type: str
    original_amount: float | None = None
    actual_amount: float | None = None
    discount_or_waiver: float | None = None
    charge_status: FeeStatusType = "UNKNOWN"
    provenance: ProvenanceType = "OBSERVED"


class ExperimentalMerchantEvidence(BaseModel):
    observed_merchant: str | None = None
    inferred_merchant: str | None = None
    inference_evidence: str | None = None
    confidence: float = 0.0
    unavailable_merchant: bool = True
    provenance: ProvenanceType = "UNAVAILABLE"


class ExperimentalDateEvidence(BaseModel):
    printed_date: str | None = None
    parsed_date: str | None = None
    assumed_convention: str = "UNPARSEABLE"
    confidence: float = 0.0
    ambiguity_status: Literal["UNAMBIGUOUS", "AMBIGUOUS_DAY_MONTH_SWAP", "UNPARSEABLE"] = (
        "UNPARSEABLE"
    )
    provenance: ProvenanceType = "UNAVAILABLE"


class ExperimentalExtractionResult(BaseModel):
    status: Literal["completed", "failed"]
    provider: str
    model: str
    latency_ms: float = 0.0
    token_usage: dict[str, Any] = Field(default_factory=dict)
    raw_ocr_summary: dict[str, Any] = Field(default_factory=dict)
    merchant_evidence: ExperimentalMerchantEvidence = Field(
        default_factory=ExperimentalMerchantEvidence
    )
    date_evidence: ExperimentalDateEvidence = Field(default_factory=ExperimentalDateEvidence)
    items: list[ExperimentalItem] = Field(default_factory=list)
    fees: list[ExperimentalFee] = Field(default_factory=list)
    subtotal_before_tax: FieldEnvelope[float] = Field(default_factory=FieldEnvelope)
    subtotal_after_tax: FieldEnvelope[float] = Field(default_factory=FieldEnvelope)
    grand_total: FieldEnvelope[float] = Field(default_factory=FieldEnvelope)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class ExistingOcrSnapshot(BaseModel):
    status: Literal["completed", "failed"]
    adapter_used: str | None = None
    latency_ms: float = 0.0
    merchant_name: str | None = None
    receipt_date: str | None = None
    subtotal_rupees: float | None = None
    tax_rupees: float | None = None
    grand_total_rupees: float | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class ExperimentalHealthOut(BaseModel):
    enabled: bool
    provider: str
    provider_configured: bool
    model: str
    base_url_configured: bool
    api_key_configured: bool
    ppocrv6_available: bool
    connectivity_status: str = "not_checked"
    isolation_guaranteed: bool = True


class ExperimentalJobOut(BaseModel):
    job_id: str
    household_id: str
    mode: ExtractModeType
    filename: str
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    experimental_result: ExperimentalExtractionResult | None = None
    existing_result: ExistingOcrSnapshot | None = None
    ground_truth: dict[str, Any] | None = None
    inventory_mutated: bool = False


ExtractionStatusType = Literal[
    "EXPERIMENTAL_SUCCESS",
    "EXPERIMENTAL_PARTIAL",
    "FALLBACK_SUCCESS",
    "ALL_PROVIDERS_FAILED",
]


class OcrOrchestrationMetadata(BaseModel):
    extraction_job_id: str
    extraction_status: ExtractionStatusType
    primary_provider: str
    primary_model: str
    primary_status: str
    fallback_triggered: bool = False
    fallback_reason: str | None = None
    fallback_provider: str | None = None
    selected_provider: str
    primary_latency_ms: float = 0.0
    fallback_latency_ms: float = 0.0
    total_latency_ms: float = 0.0
    validation_warnings: list[str] = Field(default_factory=list)
