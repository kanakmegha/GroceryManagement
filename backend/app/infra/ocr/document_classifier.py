"""Document-type classification based on structure, table headers, and content."""

from __future__ import annotations

import re
from typing import Literal

DocumentType = Literal[
    "physical_receipt",
    "gst_invoice",
    "digital_invoice",
    "order_item_list",
    "bill_summary",
    "payment_confirmation",
    "multi_page_invoice",
    "unknown",
]


def classify_document_type(
    *,
    raw_text: str,
    source_type: str,
    page_count: int = 1,
    has_table_headers: bool = False,
    parsed_line_count: int = 0,
) -> DocumentType:
    """Classify uploaded document structure before/alongside item extraction."""
    lower = (raw_text or "").lower()
    if not lower.strip():
        return "unknown"

    if page_count > 1 and source_type in ("pdf_digital", "pdf_scanned"):
        return "multi_page_invoice"

    has_gst_table = ("hsn" in lower or "gstin" in lower) and (
        "cgst" in lower or "sgst" in lower or "igst" in lower or "taxable" in lower
    )
    if has_gst_table and has_table_headers:
        return "gst_invoice"

    has_summary_signals = sum(
        1
        for k in (
            "bill summary",
            "order summary",
            "mrp",
            "product discount",
            "item total",
            "handling charge",
            "handling fee",
            "delivery charge",
            "delivery fee",
            "delivery partner fee",
            "bill total",
            "redeemed",
            "neuccoins",
        )
        if k in lower
    )
    has_order_id = bool(re.search(r"\b(?:order\s*id|ord\d{5,})\b", lower))

    # Bill summary screenshot: summary financials present, no itemized product table
    if (
        (has_summary_signals >= 2 or "bill summary" in lower or "order summary" in lower)
        and parsed_line_count == 0
        and not has_table_headers
    ):
        return "bill_summary"

    # Payment confirmation screen without itemized products
    if (
        parsed_line_count == 0
        and any(
            k in lower
            for k in ("payment successful", "paid via", "transaction id", "utr", "upi ref")
        )
        and not has_table_headers
    ):
        return "payment_confirmation"

    if has_table_headers and (has_order_id or source_type == "pdf_digital" or "invoice" in lower):
        return "digital_invoice"

    if has_order_id and parsed_line_count > 0:
        return "order_item_list"

    if parsed_line_count > 0 or any(
        k in lower for k in ("cash memo", "bill no", "grand total", "subtotal", "receipt")
    ):
        return "physical_receipt"

    return "unknown"
