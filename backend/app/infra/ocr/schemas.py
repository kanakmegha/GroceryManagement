"""Structured schemas and deterministic value parsers for receipt extraction."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, Field

from app.infra.ocr.document_classifier import DocumentType
from app.infra.ocr.table_ir import TableIR

BaseUnit = Literal["g", "ml", "pcs"]
SourceType = Literal["text", "pdf_digital", "pdf_scanned", "image", "unknown"]


class OcrTokenBox(BaseModel):
    """Single recognized text span with normalized [0..1] bounding box."""

    text: str
    confidence: float = 1.0
    x: float = 0.0  # left edge [0..1]
    y: float = 0.0  # top edge [0..1]
    w: float = 1.0  # width [0..1]
    h: float = 0.02  # height [0..1]
    page: int = 1
    reading_order: int = 0
    language: str | None = None

    @property
    def y_center(self) -> float:
        return self.y + (self.h / 2.0)

    @property
    def x_right(self) -> float:
        return self.x + self.w


class ReceiptQualityReport(BaseModel):
    """Image quality assessment (IQA) metrics and preprocessing actions applied."""

    width: int = 0
    height: int = 0
    blur_score: float = 0.0
    brightness_mean: float = 0.0
    contrast_std: float = 0.0
    illumination_variance: float = 0.0
    glare_ratio: float = 0.0
    estimated_skew_deg: float = 0.0
    perspective_corrected: bool = False
    illumination_normalized: bool = False
    contrast_enhanced: bool = False
    deskewed: bool = False
    is_readable: bool = True
    retake_reasons: list[str] = Field(default_factory=list)
    applied_steps: list[str] = Field(default_factory=list)


class ReceiptTaxBreakdown(BaseModel):
    """Indian GST & generic tax components in paise."""

    cgst_paise: int | None = None
    sgst_paise: int | None = None
    igst_paise: int | None = None
    other_tax_paise: int | None = None


class ValidationIssue(BaseModel):
    """Deterministic arithmetic or structural validation finding."""

    code: str
    severity: Literal["info", "warning", "error"] = "warning"
    message: str
    field: str | None = None
    line_index: int | None = None


class ExtractedReceiptLine(BaseModel):
    """Single parsed line item from a receipt."""

    sr_no: int | None = None
    raw_text: str
    name: str
    normalized_name: str | None = None
    brand: str | None = None
    variant: str | None = None
    quantity: float | None = None
    unit: str | None = None
    normalized_qty: float | None = None
    base_unit: BaseUnit | None = None
    pack_size: str | None = None
    unit_price_paise: int | None = None
    suggested_unit_price_paise: int | None = None
    has_explicit_unit_price: bool = False
    mrp_paise: int | None = None
    unit_taxable_paise: int | None = None
    gross_paise: int | None = None
    other_charges_paise: int | None = None
    taxable_paise: int | None = None
    cgst_paise: int | None = None
    sgst_paise: int | None = None
    igst_paise: int | None = None
    cess_paise: int | None = None
    hsn_code: str | None = None
    total_paise: int | None = None
    discount_paise: int | None = None
    discount_pct: float | None = None
    confidence: float = 1.0
    source_evidence: str | None = None
    source_page: int = 1
    source_bbox: list[float] | None = None
    needs_review: bool = False
    uncertain_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ExtractedReceipt(BaseModel):
    """Structured receipt extraction result with reconciliation metadata."""

    filename: str
    content_type: str
    source_type: SourceType
    document_type: DocumentType = "unknown"
    adapter_used: str = "local_pipeline"
    merchant_name: str | None = None
    merchant_address: str | None = None
    merchant_contact: str | None = None
    receipt_number: str | None = None
    order_id: str | None = None
    receipt_date: str | None = None
    receipt_time: str | None = None
    currency: str = "INR"
    lines: list[ExtractedReceiptLine] = Field(default_factory=list)
    mrp_total_paise: int | None = None
    subtotal_paise: int | None = None
    taxable_paise: int | None = None
    discount_paise: int | None = None
    loyalty_discount_paise: int | None = None
    handling_charges_paise: int | None = None
    delivery_charges_paise: int | None = None
    additional_charges_paise: int | None = None
    tax_paise: int | None = None
    cess_paise: int | None = None
    tax_breakdown: ReceiptTaxBreakdown = Field(default_factory=ReceiptTaxBreakdown)
    rounding_paise: int | None = None  # signed: +50 for +₹0.50, -50 for -₹0.50
    goodwill_paise: int | None = None  # signed: -100 for -₹1.00
    grand_total_paise: int | None = None
    computed_total_paise: int | None = None
    payment_method: str | None = None
    math_reconciled: bool = False
    missing_fields: list[str] = Field(default_factory=list)
    redacted_pii_types: list[str] = Field(default_factory=list)
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    candidate_row_count: int | None = None
    printed_item_qty: float | None = None
    tables: list[TableIR] = Field(default_factory=list)
    quality_report: ReceiptQualityReport | None = None
    latency_ms: int = 0
    confidence: float = 0.0
    needs_review: bool = True
    warnings: list[str] = Field(default_factory=list)
    raw_text: str = ""


def parse_inr_to_paise(value: str, *, allow_signed: bool = False) -> int | None:
    """Parse a currency amount string (supports INR/USD/EUR/GBP/AED & European decimals) to minor
    units.
    """
    if not value:
        return None
    # Never concatenate two distinct numbers separated by a currency symbol (e.g. '110$110.00')
    if re.search(
        r"\d\s*(?:₹|R\$|Rs\.?|INR|USD|AUD|CAD|SGD|EUR|GBP|AED|\$|€|£)\s*\d",
        value,
        flags=re.IGNORECASE,
    ):
        return None
    cleaned = re.sub(
        r"(?:₹|R\$|Rs\.?|INR|USD|AUD|CAD|SGD|EUR|GBP|AED|\$|€|£|\s|:)",
        "",
        value.strip(),
        flags=re.IGNORECASE,
    )
    if not cleaned:
        return None
    # Handle European decimal formats like '1.234,56' or '12,50'
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        parts = cleaned.split(",")
        if len(parts[-1]) in (1, 2):
            cleaned = "".join(parts[:-1]) + "." + parts[-1]
        else:
            cleaned = cleaned.replace(",", "")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not allow_signed and amount < 0:
        return None
    paise = (amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(paise)


def normalize_metric_unit(qty: float, unit: str) -> tuple[float, BaseUnit] | tuple[None, None]:
    """Normalize a quantity and unit into base units ('g', 'ml', 'pcs')."""
    if qty <= 0 or not unit:
        return (None, None)
    u = unit.strip().lower()
    if u in {"g", "gm", "gms", "gram", "grams"}:
        return (round(qty, 4), "g")
    if u in {"kg", "kgs", "kc", "kilogram", "kilograms"}:
        return (round(qty * 1000.0, 4), "g")
    if u in {"oz", "ounce", "ounces"}:
        return (round(qty * 28.3495, 4), "g")
    if u in {"lb", "lbs", "pound", "pounds"}:
        return (round(qty * 453.592, 4), "g")
    if u == "ml":
        return (round(qty, 4), "ml")
    if u in {"l", "ltr", "ltrs", "liter", "liters", "litre", "litres"}:
        return (round(qty * 1000.0, 4), "ml")
    if u in {"fl oz", "fl.oz", "floz"}:
        return (round(qty * 29.5735, 4), "ml")
    if u in {"gal", "gallon", "gallons"}:
        return (round(qty * 3785.41, 4), "ml")
    if u in {"pcs", "pc", "piece", "pieces", "nos", "no", "unit", "units", "plate", "plates"}:
        return (round(qty, 4), "pcs")
    if u in {"dozen", "doz"}:
        return (round(qty * 12.0, 4), "pcs")
    if u in {
        "pack",
        "packs",
        "pkt",
        "pkts",
        "packet",
        "packets",
        "box",
        "boxes",
        "bottle",
        "bottles",
    }:
        return (round(qty, 4), "pcs")
    return (None, None)


def parse_flexible_date(raw: str | None) -> date | None:
    """Parse ISO, DD/MM/YYYY, DD/MM/YY, DD-MM-YYYY, or 'DD Mon YYYY' dates safely."""
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None
    # Normalize common OCR separators
    s_norm = re.sub(r"[.\s]+", "-", s) if re.match(r"^\d{1,2}\.\d{1,2}\.\d{2,4}$", s) else s
    for fmt in (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d/%m/%y",
        "%d-%m-%Y",
        "%d-%m-%y",
        "%d %b %Y",
        "%d %B %Y",
        "%d %b %y",
        "%d-%b-%Y",
        "%d-%b-%y",
        "%m/%d/%Y",
    ):
        try:
            return datetime.strptime(s_norm, fmt).date()
        except ValueError:
            continue
    return None
