"""Spatial row clustering and Indian/universal receipt text parser."""

from __future__ import annotations

import re
from typing import Iterable

from app.infra.ocr.document_classifier import classify_document_type
from app.infra.ocr.privacy import redact_pii_text
from app.infra.ocr.schemas import (
    ExtractedReceipt,
    ExtractedReceiptLine,
    OcrTokenBox,
    ReceiptTaxBreakdown,
    SourceType,
    normalize_metric_unit,
    parse_flexible_date,
    parse_inr_to_paise,
)
from app.infra.ocr.table_extractor import extract_table_rows

_CURR_SYM = r"(?:₹|R\$|Rs\.?|INR|USD|AUD|CAD|SGD|EUR|GBP|AED|\$|€|£)"

_CURRENCY_AMOUNT_RE = re.compile(
    rf"{_CURR_SYM}\s*:?\s*([+-]?\s*[0-9][0-9.,]*)",
    re.IGNORECASE,
)
_NUMERIC_TOKEN_RE = re.compile(rf"^[+-]?{_CURR_SYM}?\s*:?\s*([0-9][0-9.,]*)$", re.IGNORECASE)
_TRAILING_3COL_RE = re.compile(
    r"^(?P<head>.+?)\s+"
    r"(?P<qty>\d+(?:\.\d+)?)\s+"
    rf"{_CURR_SYM}?\s*(?P<rate>[0-9][0-9,]*\.[0-9]{{2}}|[0-9][0-9,]*)\s+"
    rf"{_CURR_SYM}?\s*(?P<total>[0-9][0-9,]*\.[0-9]{{2}}|[0-9][0-9,]*)\s*$",
    re.IGNORECASE,
)
_TRAILING_AMOUNT_RE = re.compile(
    rf"(?:^|\s){_CURR_SYM}?\s*([0-9][0-9,]*(?:[.,][0-9]{{2}}))\s*{_CURR_SYM}?\s*$",
    re.IGNORECASE,
)
_WEIGHT_RATE_SUBLINE_RE = re.compile(
    r"^(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>kg|kgs|g|gm|gms|l|ltr|ml)\b.*?"
    rf"{_CURR_SYM}\s*(?P<rate>[0-9][0-9,]*\.[0-9]{{2}})\s*/\s*(?:kg|g|l|ml|ea|pc)",
    re.IGNORECASE,
)
_UNIT_QTY_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(kg|kgs|kilograms?|g|gm|gms|grams?|ml|l|ltr|ltrs|liters?|litres?|pcs|pc|pieces?|nos|no|units?|dozen|doz|packs?|pkts?|packets?|bottles?|box(?:es)?|plates?|oz|lb|lbs|fl\s*oz|gal)\b",
    re.IGNORECASE,
)
_MULT_QTY_RE = re.compile(
    r"(?:^|\s)(?:x|qty[:\s]*)\s*(\d+(?:\.\d+)?)\b|\b(\d+(?:\.\d+)?)\s*x\s+",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4})\b"
)
_TIME_RE = re.compile(r"\b(\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?)\b", re.IGNORECASE)
_BILL_NO_RE = re.compile(
    r"(?:bill\s*(?:no|num|#)|invoice\s*(?:no|num|#)|receipt\s*(?:no|num|#)|inv\s*(?:no|#))\s*[:.\-#\s]*([A-Za-z0-9/-]{2,24})",
    re.IGNORECASE,
)
_ORDER_ID_RE = re.compile(
    r"(?:order\s*(?:id|no|num|#)?\s*[:.\-#\s]*([A-Za-z0-9-]{5,32})|\b(ORD[A-Za-z0-9-]{4,28})\b)",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(r"(?:ph|tel|mob|phone|contact)\s*[:.\-]?\s*([+\d\-\s]{8,16})", re.IGNORECASE)
_GSTIN_RE = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z]{1}[A-Z\d]{1}Z[A-Z\d]{1}\b")
_PAYMENT_RE = re.compile(
    r"\b(UPI|GPAY|GOOGLE\s*PAY|PHONEPE|PAYTM|CASH|CARD|CREDIT\s*CARD|DEBIT\s*CARD|VISA|MASTERCARD)\b",
    re.IGNORECASE,
)
_SIGNED_ROUNDING_RE = re.compile(
    r"([+-]?\s*(?:₹|Rs\.?|INR|USD|EUR|GBP|AED|\$|€|£)?\s*[0-9]+(?:[.,][0-9]{1,2})?)\s*$",
    re.IGNORECASE,
)

_SUBTOTAL_KEYWORDS = ("subtotal", "sub total", "sub-total", "item total", "iten b1jl")
_TAXABLE_KEYWORDS = ("taxable value", "taxable amount", "taxable amt")
_MRP_TOTAL_KEYWORDS = ("mrp total", "total mrp", "total rsp")
_ROUNDING_KEYWORDS = ("round off", "rounding", "rounded", "round-off")
_GOODWILL_KEYWORDS = ("goodwill", "goodwill adjustment")
_LOYALTY_KEYWORDS = ("neucoins", "neu coins", "redeemed", "loyalty", "wallet", "supercoins")
_DISCOUNT_KEYWORDS = ("discount", "disc", "savings", "coupon", "promo", "less:")
_HANDLING_KEYWORDS = ("handling fee", "handling charge", "handling charges", "handling")
_DELIVERY_KEYWORDS = ("delivery fee", "delivery charge", "delivery charges", "delivery partner fee")
_FEE_KEYWORDS = ("packing fee", "platform fee", "service charge", "convenience fee", "surge fee")
_TENDER_SKIP_KEYWORDS = ("change $", "change ₹", "change rs", "amount tendered", "tendered", "you saved")
_PAYMENT_TXN_RE = re.compile(
    r"(?:\btransaction\s*id\b|\bpay\s+payment\s+on\b|\bpayment\s+on\s+\d{4}\b|\b[a-f0-9]{16,}\b.*\bpay\b)",
    re.IGNORECASE,
)
_TOTAL_KEYWORDS = (
    "grand total",
    "g. total",
    "g.total",
    "g total",
    "g. al",
    "g.al",
    "final total",
    "tirand total",
    "net payable",
    "amount payable",
    "bill total",
    "net amount",
    "total amount",
    "total.",
    "total",
)
_HEADER_SKIP_KEYWORDS = (
    "tax invoice",
    "retail invoice",
    "receipt",
    "cash memo",
    "gstin",
    "fssai",
    "ssai",
    "phone",
    "tel:",
    "mob:",
    "date:",
    "date ",
    "time:",
    "bill no",
    "invoice no",
    "invoice #",
    "thank you",
    "visit again",
    "item",
    "description",
    "particulars",
    "qty",
    "rate",
    "amount",
    "hsn",
    "sac code",
    "hsn/sac",
    "cashier",
    "table no",
    "token no",
)


def cluster_boxes_into_lines(boxes: Iterable[OcrTokenBox]) -> list[tuple[str, float]]:
    """Group bounding-boxed OCR tokens into visual top-to-bottom rows with mean confidence."""
    sorted_boxes = sorted(boxes, key=lambda b: (b.page, b.y_center, b.x))
    if not sorted_boxes:
        return []

    rows: list[list[OcrTokenBox]] = []
    for box in sorted_boxes:
        placed = False
        for row in reversed(rows):
            if row[0].page != box.page:
                continue
            avg_y = sum(b.y_center for b in row) / len(row)
            max_h = max(max(b.h for b in row), box.h)
            tol = min(0.025, max(0.008, 0.55 * max_h))
            # Never merge two boxes that overlap horizontally (stacked lines in same column)
            has_x_overlap = any(
                (min(b.x_right, box.x_right) - max(b.x, box.x)) > 0.02 for b in row
            )
            if not has_x_overlap and abs(box.y_center - avg_y) <= tol:
                row.append(box)
                placed = True
                break
        if not placed:
            rows.append([box])

    lines: list[tuple[str, float]] = []
    for row in rows:
        row.sort(key=lambda b: b.x)
        text = " ".join(b.text.strip() for b in row if b.text.strip())
        if text:
            mean_conf = sum(b.confidence for b in row) / len(row)
            lines.append((text, mean_conf))
    return lines


def _clean_ocr_numeric_noise(text: str) -> str:
    """Fix common OCR glyph confusions inside numeric/currency tokens only."""
    # Split currency symbols stuck between digits like '110$110.00' -> '110 $110.00'
    cleaned = re.sub(rf"(\d)\s*({_CURR_SYM})\s*(\d)", r"\1 \2\3", text, flags=re.IGNORECASE)
    # Split fused decimal amounts like '220.001100.00' -> '220.00 1100.00'
    cleaned = re.sub(r"(\d+\.\d{2})(\d+(?:,\d{3})*\.\d{2})", r"\1 \2", cleaned)
    # Fix trailing C/O/o misread for '0' in 2-decimal tails like '+0.5C' -> '+0.50'
    cleaned = re.sub(r"(\d+\.\d)[COo](?=\s|$)", r"\g<1>0", cleaned)
    # Fix comma-dot confusion in decimal tails e.g. '180,00' -> '180.00'
    cleaned = re.sub(r"(\d+),(\d{2})(?=\s*$|\s)", r"\1.\2", cleaned)
    # Fix colon-dot confusion after currency e.g. 'Rs 18:00' -> 'Rs 18.00'
    cleaned = re.sub(rf"({_CURR_SYM}\s*:?\s*\d+):(\d{{2}})\b", r"\1.\2", cleaned, flags=re.IGNORECASE)
    # Strip stray trailing colon/semicolon after numbers e.g. '729.00:' -> '729.00', '110:' -> '110'
    cleaned = re.sub(r"(\d)[:;]+(?=\s|$)", r"\1", cleaned)
    # Normalize bullet/dot separator between qty and unit e.g. '1 • KG' -> '1 KG'
    cleaned = re.sub(r"(\d)\s*[•·]\s*([A-Za-z])", r"\1 \2", cleaned)
    return cleaned


def _extract_amount_paise_from_line(line: str) -> tuple[int | None, str]:
    """Extract rightmost monetary amount in paise and return (paise, remaining_text)."""
    norm_line = _clean_ocr_numeric_noise(line)
    matches = list(_CURRENCY_AMOUNT_RE.finditer(norm_line))
    if matches:
        last = matches[-1]
        paise = parse_inr_to_paise(last.group(1))
        if paise is not None:
            remaining = (norm_line[: last.start()] + norm_line[last.end() :]).strip()
            return paise, remaining

    m = _TRAILING_AMOUNT_RE.search(norm_line)
    if m:
        paise = parse_inr_to_paise(m.group(1))
        if paise is not None:
            remaining = norm_line[: m.start()].strip()
            return paise, remaining

    # Fallback: if a stray integer/margin note trails a 2-decimal monetary amount (e.g. 'G.TOTAL : - 3459.00 3500')
    dec_matches = list(re.finditer(r"(?:^|\s)([0-9][0-9,]*[.,][0-9]{2})(?=\s|$)", norm_line))
    if dec_matches:
        last_dec = dec_matches[-1]
        paise = parse_inr_to_paise(last_dec.group(1))
        if paise is not None:
            remaining = (norm_line[: last_dec.start()] + norm_line[last_dec.end() :]).strip()
            return paise, remaining

    return None, norm_line.strip()


def _extract_3col_line(line: str) -> tuple[str, float, int, int] | None:
    """Try parsing '<name> <qty> <unit_price> <line_total>' where qty * unit_price ≈ line_total."""
    norm_line = _clean_ocr_numeric_noise(line)
    m = _TRAILING_3COL_RE.match(norm_line)
    if not m:
        return None
    name = m.group("head").strip(" -:\t")
    if not name or not any(ch.isalpha() for ch in name):
        return None
    try:
        qty = float(m.group("qty"))
    except ValueError:
        return None
    if qty <= 0 or qty > 10000:
        return None
    unit_paise = parse_inr_to_paise(m.group("rate"))
    total_paise = parse_inr_to_paise(m.group("total"))
    if unit_paise is None or total_paise is None:
        return None
    expected = round(qty * unit_paise)
    # Accept 3-column parse if arithmetic is close (within 5% or ₹2)
    if abs(expected - total_paise) <= max(200, int(0.05 * max(total_paise, 1))):
        return name, qty, unit_paise, total_paise
    return None


_TRAILING_4COL_UNIT_RE = re.compile(
    r"^(?P<head>.+?)\s+"
    r"(?P<qty>\d+(?:\.\d+)?)\s+"
    r"(?P<unit>kg|kgs|kc|g|gm|gms|gram|grams|ml|l|ltr|litre|litres|pcs|pc|nos|no|pkt|pkts|pack|packs)\s+"
    rf"{_CURR_SYM}?\s*(?P<rate>[0-9][0-9,]*\.[0-9]{{2}}|[0-9][0-9,]*)\s+"
    rf"{_CURR_SYM}?\s*(?P<total>[0-9][0-9,]*\.[0-9]{{2}}|[0-9][0-9,]*)\s*$",
    re.IGNORECASE,
)


def _extract_4col_unit_line(line: str) -> tuple[str, float, str, int, int, bool] | None:
    """Parse '<name> <qty> <unit> <rate> <total>' -> (name, qty, unit, rate_paise, total_paise, math_ok)."""
    norm_line = _clean_ocr_numeric_noise(line)
    m = _TRAILING_4COL_UNIT_RE.match(norm_line)
    if not m:
        return None
    name = m.group("head").strip(" -:\t")
    if not name or not any(ch.isalpha() for ch in name):
        return None
    try:
        qty = float(m.group("qty"))
    except ValueError:
        return None
    if qty <= 0 or qty > 10000:
        return None
    raw_unit = m.group("unit").strip().lower()
    unit = "kg" if raw_unit == "kc" else raw_unit
    rate_paise = parse_inr_to_paise(m.group("rate"))
    total_paise = parse_inr_to_paise(m.group("total"))
    if rate_paise is None or total_paise is None:
        return None
    expected = round(qty * rate_paise)
    math_ok = abs(expected - total_paise) <= max(200, int(0.05 * max(total_paise, 1)))
    return name, qty, unit, rate_paise, total_paise, math_ok


def _classify_crossout_line(line: str) -> tuple[str, str]:
    """Classify whether a line is explicitly cancelled, has an uncertain cross-out, or is clean."""
    stripped = line.strip()
    if re.match(r"^(?:\[?\s*(?:cancelled|void|strike)\s*\]?\b|~~.+~~\s*$)", stripped, re.IGNORECASE):
        return "cancelled", stripped
    if re.match(r"^~{1,2}\s*[^~]", stripped):
        cleaned = re.sub(r"^[~\s]+", "", stripped).strip()
        return "uncertain", cleaned
    return "none", line


def _is_separator_or_noise(line: str) -> bool:
    stripped = line.strip()
    if len(stripped) < 2:
        return True
    if all(ch in "-=_*#~.|/\\ " for ch in stripped):
        return True
    if re.match(r"^[^0-9]*(?:={2,}|[-=]{2,}>?|-{3,})\s*$", stripped):
        return True
    if re.match(r"^(?:item\s*qty|atem\s*qty|total\s*qty|total\s*items)\b", stripped, re.IGNORECASE):
        return True
    return False


def _clean_item_name(raw_name: str) -> str:
    cleaned = re.sub(r"^(?:\d+[.)]?|[*•\-–]+|[A-Za-z](?=\s+(?:\d+|[A-Z][a-zA-Z]+)))\s+", "", raw_name.strip())
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -:\t|*")
    return cleaned


def _looks_like_wrapped_name(line: str) -> bool:
    s = line.strip()
    if _is_separator_or_noise(s):
        return False
    lower = s.lower()
    if any(k in lower for k in _HEADER_SKIP_KEYWORDS + _TOTAL_KEYWORDS + _SUBTOTAL_KEYWORDS + _DISCOUNT_KEYWORDS + _ROUNDING_KEYWORDS):
        return False
    if _GSTIN_RE.search(s) or _DATE_RE.search(s) or _BILL_NO_RE.search(s) or _PHONE_RE.search(s):
        return False
    alpha_count = sum(1 for ch in s if ch.isalpha())
    digit_count = sum(1 for ch in s if ch.isdigit())
    # Wrapped product/dish name should be predominantly alphabetic and short-to-medium
    return alpha_count >= 3 and alpha_count >= digit_count and len(s) <= 60


def parse_receipt_text_and_boxes(
    *,
    raw_text: str = "",
    boxes: list[OcrTokenBox] | None = None,
    vertical_grid_xs: list[float] | None = None,
    filename: str,
    content_type: str,
    source_type: SourceType,
    adapter_used: str,
    base_confidence: float,
    initial_warnings: list[str] | None = None,
) -> ExtractedReceipt:
    """Parse OCR bounding boxes (preferred) or plain text into a structured ExtractedReceipt."""
    warnings = list(initial_warnings or [])

    if boxes:
        row_tuples = cluster_boxes_into_lines(boxes)
        lines_with_conf = [(txt.strip(), conf) for txt, conf in row_tuples if txt.strip()]
        effective_raw_text = "\n".join(txt for txt, _ in lines_with_conf)
    else:
        lines_with_conf = [
            (ln.strip(), base_confidence)
            for ln in (raw_text or "").splitlines()
            if ln.strip()
        ]
        effective_raw_text = raw_text or ""

    merchant_name: str | None = None
    merchant_contact: str | None = None
    receipt_number: str | None = None
    order_id: str | None = None
    receipt_date: str | None = None
    receipt_time: str | None = None
    payment_method: str | None = None

    mrp_total_paise: int | None = None
    subtotal_paise: int | None = None
    taxable_paise: int | None = None
    discount_paise: int | None = None
    loyalty_discount_paise: int | None = None
    handling_charges_paise: int | None = None
    delivery_charges_paise: int | None = None
    additional_charges_paise: int | None = None
    cess_paise: int | None = None
    rounding_paise: int | None = None
    goodwill_paise: int | None = None
    grand_total_paise: int | None = None
    tax_breakdown = ReceiptTaxBreakdown()
    explicit_tax_total: int | None = None

    has_inr_signal = bool(
        re.search(r"(?:₹|\bRs\.?\b|\bINR\b|\bCGST\b|\bSGST\b|\bIGST\b|\bGSTIN\b)", effective_raw_text, re.IGNORECASE)
    )
    has_eur_signal = bool(re.search(r"(?:€|\bEUR\b)", effective_raw_text, re.IGNORECASE))
    has_gbp_signal = bool(re.search(r"(?:£|\bGBP\b)", effective_raw_text, re.IGNORECASE))
    has_aed_signal = bool(re.search(r"\bAED\b", effective_raw_text, re.IGNORECASE))
    has_usd_signal = bool(re.search(r"(?:^|\s)\$\s*\d|\bUSD\b", effective_raw_text, re.IGNORECASE))
    if has_inr_signal:
        detected_currency = "INR"
    elif has_eur_signal:
        detected_currency = "EUR"
    elif has_gbp_signal:
        detected_currency = "GBP"
    elif has_aed_signal:
        detected_currency = "AED"
    elif has_usd_signal:
        detected_currency = "USD"
    else:
        detected_currency = "INR"

    table_result = (
        extract_table_rows(
            boxes,
            document_id=filename,
            vertical_grid_xs=vertical_grid_xs,
        )
        if boxes
        else None
    )
    use_table_lines = bool(table_result and table_result.has_table_headers and table_result.lines)
    parsed_lines: list[ExtractedReceiptLine] = list(table_result.lines) if use_table_lines and table_result else []
    fallback_lines: list[ExtractedReceiptLine] = []
    pending_wrapped_parts: list[str] = []
    pending_summary_key: str | None = None
    seen_items_section = False
    has_row_level_cgst = use_table_lines and any(l.cgst_paise is not None for l in parsed_lines)
    has_row_level_sgst = use_table_lines and any(l.sgst_paise is not None for l in parsed_lines)
    in_gst_summary_section = False
    printed_item_qty: float | None = None
    candidate_row_count = 0
    cancelled_row_count = 0
    after_grand_total = False
    annotated_total_paise: int | None = None
    uncertain_crossout_lines: set[str] = set()

    for line_idx, (line, line_conf) in enumerate(lines_with_conf):
        qty_summary_m = re.match(
            r"^(?:item\s*qty|atem\s*qty|total\s*qty|total\s*items)\s*[:\-]?\s*(\d+(?:\.\d+)?)\b",
            line.strip(),
            re.IGNORECASE,
        )
        if qty_summary_m:
            try:
                printed_item_qty = float(qty_summary_m.group(1))
            except ValueError:
                pass
            pending_wrapped_parts.clear()
            continue
        if _is_separator_or_noise(line):
            pending_wrapped_parts.clear()
            continue
        crossout_status, line = _classify_crossout_line(line)
        if crossout_status == "cancelled":
            cancelled_row_count += 1
            pending_wrapped_parts.clear()
            continue
        if crossout_status == "uncertain":
            uncertain_crossout_lines.add(line)
        lower = line.lower()

        if after_grand_total and _NUMERIC_TOKEN_RE.match(line.strip()):
            cand_paise = parse_inr_to_paise(line.strip())
            if cand_paise is not None and grand_total_paise is not None and cand_paise != grand_total_paise:
                annotated_total_paise = cand_paise
            after_grand_total = False
            continue

        # If previous summary label had no amount on its line and this line is a standalone amount
        if pending_summary_key is not None and _NUMERIC_TOKEN_RE.match(line.strip()):
            standalone_paise = parse_inr_to_paise(
                line.strip(), allow_signed=(pending_summary_key == "rounding")
            )
            if standalone_paise is not None:
                if pending_summary_key == "subtotal":
                    subtotal_paise = standalone_paise
                elif pending_summary_key == "cgst":
                    tax_breakdown.cgst_paise = (tax_breakdown.cgst_paise or 0) + standalone_paise
                elif pending_summary_key == "sgst":
                    tax_breakdown.sgst_paise = (tax_breakdown.sgst_paise or 0) + standalone_paise
                elif pending_summary_key == "igst":
                    tax_breakdown.igst_paise = (tax_breakdown.igst_paise or 0) + standalone_paise
                elif pending_summary_key == "rounding":
                    rounding_paise = standalone_paise
                elif pending_summary_key == "discount":
                    discount_paise = (discount_paise or 0) + standalone_paise
                elif pending_summary_key == "grand_total":
                    grand_total_paise = standalone_paise
                pending_summary_key = None
                continue
        if not any(
            k in lower
            for k in (
                _SUBTOTAL_KEYWORDS
                + _ROUNDING_KEYWORDS
                + _DISCOUNT_KEYWORDS
                + _TOTAL_KEYWORDS
                + ("cgst", "sgst", "igst", "gst", "vat", "tax")
            )
        ):
            pending_summary_key = None

        # Contact / Bill No / Date / Time / Payment extraction
        if merchant_contact is None:
            pm = _PHONE_RE.search(line)
            if pm:
                merchant_contact = pm.group(1).strip()

        if receipt_number is None:
            bm = _BILL_NO_RE.search(line)
            if bm:
                receipt_number = bm.group(1).strip()

        if order_id is None:
            om = _ORDER_ID_RE.search(line)
            if om:
                order_id = (om.group(1) or om.group(2)).strip()

        if receipt_date is None:
            dm = _DATE_RE.search(line)
            if dm:
                parsed_d = parse_flexible_date(dm.group(1))
                receipt_date = parsed_d.isoformat() if parsed_d else dm.group(1)

        if receipt_time is None:
            tm = _TIME_RE.search(line)
            if tm and ("time" in lower or ":" in tm.group(1)):
                receipt_time = tm.group(1).strip()

        if payment_method is None:
            pay_m = _PAYMENT_RE.search(line)
            if pay_m:
                payment_method = pay_m.group(1).upper()

        # Summary / Tax / Total lines
        if any(k in lower for k in _MRP_TOTAL_KEYWORDS):
            amt, _ = _extract_amount_paise_from_line(line)
            if amt is not None:
                mrp_total_paise = amt
            pending_wrapped_parts.clear()
            continue

        if any(k in lower for k in _TAXABLE_KEYWORDS) and not use_table_lines:
            amt, _ = _extract_amount_paise_from_line(line)
            if amt is not None:
                taxable_paise = amt
            pending_wrapped_parts.clear()
            continue

        if (
            any(k in lower for k in _SUBTOTAL_KEYWORDS)
            or ("sub" in lower and "total" in lower)
            or lower == "sub"
            or lower.endswith(" sub")
        ):
            amt, _ = _extract_amount_paise_from_line(line)
            if amt is not None:
                subtotal_paise = amt
            else:
                pending_summary_key = "subtotal"
            pending_wrapped_parts.clear()
            continue

        if any(k in lower for k in _GOODWILL_KEYWORDS):
            m_good = _SIGNED_ROUNDING_RE.search(_clean_ocr_numeric_noise(line))
            if m_good:
                g_val = parse_inr_to_paise(m_good.group(1), allow_signed=True)
                if g_val is not None:
                    # Goodwill adjustment on invoices is typically a deduction unless explicitly '+'
                    if "-" in line and g_val > 0:
                        g_val = -g_val
                    goodwill_paise = (goodwill_paise or 0) + g_val
            pending_wrapped_parts.clear()
            continue

        if any(k in lower for k in _ROUNDING_KEYWORDS):
            m_round = _SIGNED_ROUNDING_RE.search(_clean_ocr_numeric_noise(line))
            if m_round:
                r_val = parse_inr_to_paise(m_round.group(1), allow_signed=True)
                if r_val is not None:
                    if pending_summary_key == "sgst" and tax_breakdown.sgst_paise is None:
                        tax_breakdown.sgst_paise = r_val
                        pending_summary_key = "rounding"
                    else:
                        rounding_paise = r_val
                    pending_wrapped_parts.clear()
                    continue
            pending_summary_key = "rounding"
            pending_wrapped_parts.clear()
            continue

        if any(k in lower for k in _HANDLING_KEYWORDS):
            amt, _ = _extract_amount_paise_from_line(line)
            if amt is not None:
                handling_charges_paise = (handling_charges_paise or 0) + amt
            pending_wrapped_parts.clear()
            continue

        if any(k in lower for k in _DELIVERY_KEYWORDS):
            amt, _ = _extract_amount_paise_from_line(line)
            if amt is not None:
                delivery_charges_paise = (delivery_charges_paise or 0) + amt
            pending_wrapped_parts.clear()
            continue

        if any(k in lower for k in _FEE_KEYWORDS):
            amt, _ = _extract_amount_paise_from_line(line)
            if amt is not None:
                additional_charges_paise = (additional_charges_paise or 0) + amt
            pending_wrapped_parts.clear()
            continue

        if _PAYMENT_TXN_RE.search(line) or any(k in lower for k in _TENDER_SKIP_KEYWORDS) or re.match(
            rf"^(?:cash|card|change|paid)\s+{_CURR_SYM}?\s*\d", lower, re.IGNORECASE
        ):
            pending_summary_key = None
            pending_wrapped_parts.clear()
            continue

        if any(k in lower for k in _LOYALTY_KEYWORDS):
            m_loy = _CURRENCY_AMOUNT_RE.search(_clean_ocr_numeric_noise(line)) or re.search(
                r"([+-]?\s*[0-9][0-9.,]*)\s*$", _clean_ocr_numeric_noise(line)
            )
            amt = parse_inr_to_paise(m_loy.group(1), allow_signed=True) if m_loy else None
            if amt is not None:
                loyalty_discount_paise = (loyalty_discount_paise or 0) + abs(amt)
            pending_wrapped_parts.clear()
            continue

        if any(k in lower for k in _DISCOUNT_KEYWORDS):
            m_disc = _CURRENCY_AMOUNT_RE.search(_clean_ocr_numeric_noise(line)) or re.search(
                r"([+-]?\s*[0-9][0-9.,]*)\s*$", _clean_ocr_numeric_noise(line)
            )
            amt = parse_inr_to_paise(m_disc.group(1), allow_signed=True) if m_disc else None
            if amt is not None:
                discount_paise = (discount_paise or 0) + abs(amt)
            else:
                pending_summary_key = "discount"
            pending_wrapped_parts.clear()
            continue

        if "gst information" in lower or "hsn summary" in lower or "tax summary" in lower:
            seen_items_section = True
            in_gst_summary_section = True
            pending_summary_key = None
            pending_wrapped_parts.clear()
            continue

        if "cgst" in lower:
            seen_items_section = True
            if "sale" in lower or "taxable value" in lower or "tax value" in lower:
                pending_summary_key = None
                continue
            amt, _ = _extract_amount_paise_from_line(line)
            if amt is not None:
                lines_sum = sum(l.total_paise or 0 for l in parsed_lines)
                if pending_summary_key == "subtotal" and lines_sum > 0 and abs(amt - lines_sum) <= 100:
                    subtotal_paise = amt
                    pending_summary_key = "cgst"
                elif not (has_row_level_cgst and in_gst_summary_section):
                    tax_breakdown.cgst_paise = (tax_breakdown.cgst_paise or 0) + amt
            elif not (has_row_level_cgst and in_gst_summary_section):
                pending_summary_key = "cgst"
            pending_wrapped_parts.clear()
            continue

        if "sgst" in lower:
            seen_items_section = True
            if "sale" in lower or "taxable value" in lower or "tax value" in lower:
                pending_summary_key = None
                continue
            amt, _ = _extract_amount_paise_from_line(line)
            if amt is not None:
                if pending_summary_key == "cgst" and tax_breakdown.cgst_paise is None and not (has_row_level_cgst and in_gst_summary_section):
                    tax_breakdown.cgst_paise = amt
                    pending_summary_key = "sgst"
                elif not (has_row_level_sgst and in_gst_summary_section):
                    tax_breakdown.sgst_paise = (tax_breakdown.sgst_paise or 0) + amt
            elif not (has_row_level_sgst and in_gst_summary_section):
                pending_summary_key = "sgst"
            pending_wrapped_parts.clear()
            continue

        if "igst" in lower:
            amt, _ = _extract_amount_paise_from_line(line)
            if amt is not None:
                tax_breakdown.igst_paise = (tax_breakdown.igst_paise or 0) + amt
            else:
                pending_summary_key = "igst"
            pending_wrapped_parts.clear()
            continue

        if "cess" in lower and not use_table_lines:
            amt, _ = _extract_amount_paise_from_line(line)
            if amt is not None:
                cess_paise = (cess_paise or 0) + amt
            pending_wrapped_parts.clear()
            continue

        if any(k in lower for k in ("gst", "vat", "tax")) and not any(
            k in lower for k in ("tax invoice", "gstin")
        ):
            amt, _ = _extract_amount_paise_from_line(line)
            if amt is not None:
                tax_breakdown.other_tax_paise = (tax_breakdown.other_tax_paise or 0) + amt
                explicit_tax_total = (explicit_tax_total or 0) + amt
                pending_wrapped_parts.clear()
                continue

        if any(k in lower for k in _TOTAL_KEYWORDS) and "subtotal" not in lower and not (
            "item description" in lower or "hsn" in lower or "quantity" in lower
        ):
            amt, _ = _extract_amount_paise_from_line(line)
            if amt is not None:
                if (
                    pending_summary_key == "subtotal"
                    and subtotal_paise is None
                    and "grand" not in lower
                    and "net" not in lower
                ):
                    subtotal_paise = amt
                    pending_summary_key = None
                elif pending_summary_key == "rounding" and rounding_paise is None and amt <= 500:
                    rounding_paise = amt
                    pending_summary_key = "grand_total"
                elif not ("payable amount" in lower and amt == 0):
                    grand_total_paise = amt
                    after_grand_total = True
            else:
                pending_summary_key = "grand_total"
            pending_wrapped_parts.clear()
            continue

        if re.match(r"^rs\.?\s+[a-z\s]+(?:only|ony)\s*$", lower):
            pending_wrapped_parts.clear()
            continue

        if any(
            k in lower
            for k in ("item description", "item &", "particulars", "hsn", "taxable", "cgst", "sgst", "amt.", "unit price")
        ):
            seen_items_section = True

        # Merchant name heuristic: first clean alphabetic header line before items/tables
        if (
            merchant_name is None
            and not seen_items_section
            and not any(k in lower for k in _HEADER_SKIP_KEYWORDS)
            and not any(k in lower for k in ("amt.", "sr", "no.", "mrp/rsp", "disc.", "cess", "taxable", "value"))
            and not _GSTIN_RE.search(line)
            and not _DATE_RE.search(line)
            and sum(1 for c in line if c.isalpha()) >= 3
            and not (len(line.strip()) < 8 and line_conf < 0.65)
            and _extract_amount_paise_from_line(line)[0] is None
        ):
            cleaned_merchant = re.sub(
                r"^(?:seller\s*name|sold\s*by|merchant)\s*[:\-]\s*",
                "",
                line.strip(),
                flags=re.IGNORECASE,
            ).strip()
            if cleaned_merchant and len(cleaned_merchant) >= 3:
                merchant_name = cleaned_merchant
            continue

        if any(k in lower for k in _HEADER_SKIP_KEYWORDS):
            if any(k in lower for k in ("item", "particulars", "description", "qty", "rate")):
                seen_items_section = True
            pending_wrapped_parts.clear()
            continue

        target_lines = fallback_lines if use_table_lines else parsed_lines

        # Try 4-column item line with explicit unit: <name> <qty> <unit> <rate> <total>
        four_col = _extract_4col_unit_line(line)
        if four_col is not None:
            seen_items_section = True
            c_name, c_qty, c_unit, c_rate, c_total, c_math_ok = four_col
            if pending_wrapped_parts:
                c_name = f"{' '.join(pending_wrapped_parts)} {c_name}".strip()
                pending_wrapped_parts.clear()
            clean_name = _clean_item_name(c_name)
            norm_qty, base_unit = normalize_metric_unit(c_qty, c_unit)
            implied_rate = round(c_total / c_qty) if (not c_math_ok and c_qty > 0) else None
            target_lines.append(
                ExtractedReceiptLine(
                    raw_text=line,
                    name=clean_name,
                    normalized_name=clean_name.lower(),
                    quantity=c_qty,
                    unit=c_unit,
                    normalized_qty=norm_qty,
                    base_unit=base_unit,
                    unit_price_paise=c_rate,
                    suggested_unit_price_paise=implied_rate,
                    has_explicit_unit_price=True,
                    total_paise=c_total,
                    confidence=round(min(base_confidence, line_conf), 2),
                    source_evidence=line,
                    needs_review=(line_conf < 0.7 or not c_math_ok),
                    uncertain_fields=([] if c_math_ok else ["unit_price"]),
                    warnings=(
                        []
                        if c_math_ok
                        else [
                            f"Rate uncertain on '{clean_name}': {c_qty:g} x ₹{c_rate / 100:.2f} != ₹{c_total / 100:.2f} "
                            f"(implied rate: ₹{implied_rate / 100:.2f}/{c_unit})."
                        ]
                    ),
                )
            )
            continue

        # Try 3-column item line: <name> <qty> <unit_price> <line_total>
        three_col = _extract_3col_line(line)
        if three_col is not None:
            seen_items_section = True
            col_name, col_qty, col_unit_paise, col_total_paise = three_col
            if pending_wrapped_parts:
                col_name = f"{' '.join(pending_wrapped_parts)} {col_name}".strip()
                pending_wrapped_parts.clear()
            clean_name = _clean_item_name(col_name)
            norm_qty, base_unit = normalize_metric_unit(col_qty, "pcs")
            target_lines.append(
                ExtractedReceiptLine(
                    raw_text=line,
                    name=clean_name,
                    normalized_name=clean_name.lower(),
                    quantity=col_qty,
                    unit="pcs",
                    normalized_qty=norm_qty,
                    base_unit=base_unit,
                    unit_price_paise=col_unit_paise,
                    has_explicit_unit_price=True,
                    total_paise=col_total_paise,
                    confidence=round(min(base_confidence, line_conf), 2),
                    source_evidence=line,
                    needs_review=line_conf < 0.7,
                )
            )
            continue

        # Weighted produce sub-line: e.g. '0.778kg NET # $5.99/kg' following 'ZUCHINNI GREEN $4.66'
        wr_match = _WEIGHT_RATE_SUBLINE_RE.match(_clean_ocr_numeric_noise(line))
        if wr_match and target_lines:
            prev_line = target_lines[-1]
            try:
                w_qty = float(wr_match.group("qty"))
                w_unit = wr_match.group("unit").lower()
                w_rate_paise = parse_inr_to_paise(wr_match.group("rate"))
                n_qty, b_unit = normalize_metric_unit(w_qty, w_unit)
                if n_qty is not None and b_unit is not None:
                    prev_line.quantity = w_qty
                    prev_line.unit = w_unit
                    prev_line.normalized_qty = n_qty
                    prev_line.base_unit = b_unit
                    if w_rate_paise is not None:
                        prev_line.unit_price_paise = w_rate_paise
                    prev_line.raw_text = f"{prev_line.raw_text} | {line.strip()}"
            except (TypeError, ValueError):
                pass
            pending_wrapped_parts.clear()
            continue

        # Standard single trailing amount line (or standalone price following wrapped item name)
        amount_paise, remaining = _extract_amount_paise_from_line(line)
        if amount_paise is not None and (remaining or pending_wrapped_parts):
            seen_items_section = True
            if pending_wrapped_parts:
                remaining = f"{' '.join(pending_wrapped_parts)} {remaining}".strip()
                pending_wrapped_parts.clear()

            qty: float | None = None
            unit: str | None = None
            norm_qty: float | None = None
            base_unit = None

            unit_match = _UNIT_QTY_RE.search(remaining)
            mult_match = _MULT_QTY_RE.search(remaining)
            multiplier = 1.0
            if mult_match:
                raw_mult = mult_match.group(1) or mult_match.group(2)
                try:
                    multiplier = float(raw_mult)
                except (TypeError, ValueError):
                    multiplier = 1.0

            if unit_match:
                base_qty = float(unit_match.group(1))
                unit = unit_match.group(2).lower()
                qty = round(base_qty * multiplier, 4)
                norm_qty, base_unit = normalize_metric_unit(qty, unit)
            elif mult_match:
                qty = round(multiplier, 4)
                unit = "pcs"
                norm_qty, base_unit = normalize_metric_unit(qty, unit)
            else:
                qty = 1.0
                unit = "pcs"
                norm_qty, base_unit = (1.0, "pcs")

            clean_name = remaining
            if unit_match:
                clean_name = clean_name.replace(unit_match.group(0), " ")
            if mult_match:
                clean_name = clean_name.replace(mult_match.group(0), " ")
            clean_name = _clean_item_name(clean_name) or remaining

            unit_price_paise = (
                round(amount_paise / qty) if qty and qty > 0 else amount_paise
            )
            target_lines.append(
                ExtractedReceiptLine(
                    raw_text=line,
                    name=clean_name,
                    normalized_name=clean_name.lower(),
                    quantity=qty,
                    unit=unit,
                    normalized_qty=norm_qty,
                    base_unit=base_unit,
                    unit_price_paise=unit_price_paise,
                    total_paise=amount_paise,
                    confidence=round(min(base_confidence, line_conf), 2),
                    source_evidence=line,
                    needs_review=line_conf < 0.7,
                )
            )
            continue

        # Buffer potential wrapped item name, or attach as suffix to previous priced item
        if _looks_like_wrapped_name(line):
            next_line_has_own_item = False
            if line_idx + 1 < len(lines_with_conf):
                nxt = lines_with_conf[line_idx + 1][0]
                nxt_amt, nxt_rem = _extract_amount_paise_from_line(nxt)
                if nxt_amt is not None and any(ch.isalpha() for ch in nxt_rem):
                    next_line_has_own_item = True
            if (
                seen_items_section
                and target_lines
                and not pending_wrapped_parts
                and len(line.strip().split()) == 1
                and next_line_has_own_item
            ):
                prev = target_lines[-1]
                prev.name = _clean_item_name(f"{prev.name} {line.strip()}")
                prev.normalized_name = prev.name.lower()
                prev.raw_text = f"{prev.raw_text} {line.strip()}"
            else:
                pending_wrapped_parts.append(line.strip())
                if len(pending_wrapped_parts) > 2:
                    pending_wrapped_parts.pop(0)
        else:
            pending_wrapped_parts.clear()

    candidate_row_count = max(len(fallback_lines), len(parsed_lines)) if use_table_lines else len(parsed_lines)

    # Automatic recovery: if spatial TableIR rows fail reconciliation against grand_total_paise
    # OR if TableIR severely under-extracted (<=2 rows when >=4 candidate rows exist), recover with fallback_lines.
    if use_table_lines and parsed_lines and fallback_lines:
        should_recover = False
        table_sum = sum(l.total_paise or 0 for l in parsed_lines)
        fallback_sum = sum(l.total_paise or 0 for l in fallback_lines)
        table_reconciled = (
            (
                grand_total_paise is not None
                and abs(table_sum - grand_total_paise) <= max(200, int(0.02 * max(grand_total_paise, 1)))
            )
            or (
                subtotal_paise is not None
                and abs(table_sum - subtotal_paise) <= max(200, int(0.02 * max(subtotal_paise, 1)))
            )
        )
        if not table_reconciled and len(parsed_lines) <= 2 and len(fallback_lines) >= max(4, len(parsed_lines) * 2):
            should_recover = True
        elif grand_total_paise is not None and not table_reconciled:
            table_diff = abs(table_sum - grand_total_paise)
            fallback_diff = abs(fallback_sum - grand_total_paise)
            if (
                table_diff > 200
                and fallback_diff <= max(200, int(0.02 * max(grand_total_paise, 1)))
                and fallback_diff < table_diff
                and len(fallback_lines) >= len(parsed_lines)
            ):
                should_recover = True
        if should_recover:
            parsed_lines = fallback_lines
            use_table_lines = False

    if uncertain_crossout_lines:
        for pl in parsed_lines:
            if pl.raw_text in uncertain_crossout_lines:
                pl.needs_review = True
                if "crossout" not in pl.uncertain_fields:
                    pl.uncertain_fields.append("crossout")
                msg = (
                    f"Possible crossed-out or handwritten mark on '{pl.name}'; "
                    "verify whether this item was purchased."
                )
                if msg not in pl.warnings:
                    pl.warnings.append(msg)

    if annotated_total_paise is not None and grand_total_paise is not None:
        warnings.append(
            f"Printed grand total (₹{grand_total_paise / 100:.2f}) has nearby handwritten/ambiguous "
            f"annotation (₹{annotated_total_paise / 100:.2f}); verify the final paid amount."
        )
    if cancelled_row_count > 0:
        warnings.append(f"Excluded {cancelled_row_count} explicitly cancelled row(s) from bill.")

    # Aggregate table-level tax & totals when table rows provide per-line breakdown
    if use_table_lines and parsed_lines:
        if tax_breakdown.cgst_paise is None:
            line_cgst = [l.cgst_paise for l in parsed_lines if l.cgst_paise is not None]
            if line_cgst:
                tax_breakdown.cgst_paise = sum(line_cgst)
        if tax_breakdown.sgst_paise is None:
            line_sgst = [l.sgst_paise for l in parsed_lines if l.sgst_paise is not None]
            if line_sgst:
                tax_breakdown.sgst_paise = sum(line_sgst)
        if tax_breakdown.igst_paise is None:
            line_igst = [l.igst_paise for l in parsed_lines if l.igst_paise is not None]
            if line_igst:
                tax_breakdown.igst_paise = sum(line_igst)
        if cess_paise is None:
            line_cess = [l.cess_paise for l in parsed_lines if l.cess_paise is not None]
            if line_cess:
                cess_paise = sum(line_cess)
        if taxable_paise is None:
            line_taxable = [l.taxable_paise for l in parsed_lines if l.taxable_paise is not None]
            if line_taxable:
                taxable_paise = sum(line_taxable)

    # Aggregate tax_paise from breakdown
    breakdown_parts = [
        v
        for v in (
            tax_breakdown.cgst_paise,
            tax_breakdown.sgst_paise,
            tax_breakdown.igst_paise,
            tax_breakdown.other_tax_paise,
        )
        if v is not None
    ]
    tax_paise = sum(breakdown_parts) if breakdown_parts else explicit_tax_total

    if handling_charges_paise is not None or delivery_charges_paise is not None:
        additional_charges_paise = (
            (additional_charges_paise or 0)
            + (handling_charges_paise or 0)
            + (delivery_charges_paise or 0)
        )
    if loyalty_discount_paise is not None:
        discount_paise = (discount_paise or 0) + loyalty_discount_paise

    page_count = max((b.page for b in boxes), default=1) if boxes else 1
    has_table_headers = bool(table_result and table_result.has_table_headers)
    document_type = classify_document_type(
        raw_text=effective_raw_text,
        source_type=source_type,
        page_count=page_count,
        has_table_headers=has_table_headers,
        parsed_line_count=len(parsed_lines),
    )

    missing_fields: list[str] = []
    if document_type == "bill_summary":
        parsed_lines = []
        missing_fields.append("line_items")
        warnings.append("Bill summary only — attach itemized invoice or add items")
    elif not parsed_lines:
        missing_fields.append("line_items")

    redacted_raw_text, redacted_pii_types = redact_pii_text(effective_raw_text)
    if merchant_contact:
        redacted_contact, contact_pii = redact_pii_text(merchant_contact)
        if contact_pii:
            for item in contact_pii:
                if item not in redacted_pii_types:
                    redacted_pii_types.append(item)
            merchant_contact = redacted_contact

    return ExtractedReceipt(
        filename=filename,
        content_type=content_type,
        source_type=source_type,
        document_type=document_type,
        adapter_used=adapter_used,
        merchant_name=merchant_name,
        merchant_contact=merchant_contact,
        receipt_number=receipt_number,
        order_id=order_id,
        receipt_date=receipt_date,
        receipt_time=receipt_time,
        currency=detected_currency,
        lines=parsed_lines,
        mrp_total_paise=mrp_total_paise,
        subtotal_paise=subtotal_paise,
        taxable_paise=taxable_paise,
        discount_paise=discount_paise,
        loyalty_discount_paise=loyalty_discount_paise,
        handling_charges_paise=handling_charges_paise,
        delivery_charges_paise=delivery_charges_paise,
        additional_charges_paise=additional_charges_paise,
        tax_paise=tax_paise,
        cess_paise=cess_paise,
        tax_breakdown=tax_breakdown,
        rounding_paise=rounding_paise,
        goodwill_paise=goodwill_paise,
        grand_total_paise=grand_total_paise,
        payment_method=payment_method,
        missing_fields=missing_fields,
        redacted_pii_types=redacted_pii_types,
        candidate_row_count=candidate_row_count or None,
        printed_item_qty=printed_item_qty,
        tables=table_result.tables if table_result else [],
        confidence=base_confidence,
        warnings=warnings,
        raw_text=redacted_raw_text,
    )
