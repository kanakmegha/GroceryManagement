"""Universal hybrid receipt extraction pipeline.

Stages:
  A. Input validation, digital PDF text+box extraction, or scanned PDF rendering.
  B. Image quality assessment (IQA) & adaptive OpenCV preprocessing.
  C. Primary local OCR (Apple Vision via ``ocrmac`` or Tesseract TSV) + selective VLM fallback.
  D. Spatial row-clustering and Indian/universal receipt structured parsing.
  E. Deterministic arithmetic, duplicate, and anomaly validation.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path
from typing import Any

from app.infra.ocr.preprocessing import (
    MAX_RECEIPT_BYTES,
    detect_table_vertical_lines,
    extract_pdf_inputs,
    preprocess_receipt_image,
)
from app.infra.ocr.providers import (
    HuggingFaceVlmProvider,
    LocalVisionOcrProvider,
    RawOcrOutput,
    TesseractOcrProvider,
    TextOcrProvider,
)
from app.infra.ocr.schemas import (
    BaseUnit,
    ExtractedReceipt,
    ExtractedReceiptLine,
    OcrTokenBox,
    ReceiptQualityReport,
    ReceiptTaxBreakdown,
    SourceType,
    normalize_metric_unit,
    parse_flexible_date,
    parse_inr_to_paise,
)
from app.infra.ocr.spatial_parser import parse_receipt_text_and_boxes
from app.infra.ocr.validation import (
    MATH_RECONCILIATION_TOLERANCE_PAISE,
    validate_extracted_receipt,
)

__all__ = [
    "BaseUnit",
    "ExtractedReceipt",
    "ExtractedReceiptLine",
    "MATH_RECONCILIATION_TOLERANCE_PAISE",
    "SourceType",
    "extract_receipt",
    "normalize_metric_unit",
    "parse_inr_to_paise",
]


def _select_local_ocr_provider() -> TextOcrProvider | None:
    for candidate in (LocalVisionOcrProvider(), TesseractOcrProvider()):
        if candidate.is_available():
            return candidate
    return None


def _vlm_dict_to_receipt(
    vlm_data: dict[str, Any],
    *,
    filename: str,
    content_type: str,
    source_type: SourceType,
    quality_report: ReceiptQualityReport | None,
    initial_warnings: list[str],
) -> ExtractedReceipt:
    """Convert structured VLM JSON output into ExtractedReceipt without mutating values."""
    def _to_paise(val: Any, *, signed: bool = False) -> int | None:
        if val is None or val == "":
            return None
        if isinstance(val, (int, float)):
            return int(round(float(val) * 100))
        return parse_inr_to_paise(str(val), allow_signed=signed)

    lines: list[ExtractedReceiptLine] = []
    for raw_item in vlm_data.get("items") or []:
        if not isinstance(raw_item, dict):
            continue
        name = str(raw_item.get("name") or raw_item.get("raw_text") or "").strip()
        if not name:
            continue
        qty_raw = raw_item.get("quantity")
        qty = float(qty_raw) if qty_raw is not None else 1.0
        unit = str(raw_item.get("unit") or "pcs").strip().lower()
        norm_qty, base_unit = normalize_metric_unit(qty, unit)
        total_paise = _to_paise(raw_item.get("total_price"))
        unit_price_paise = _to_paise(raw_item.get("unit_price"))
        if unit_price_paise is None and total_paise is not None and qty > 0:
            unit_price_paise = round(total_paise / qty)
        lines.append(
            ExtractedReceiptLine(
                raw_text=str(raw_item.get("raw_text") or name),
                name=name,
                normalized_name=name.lower(),
                quantity=qty,
                unit=unit,
                normalized_qty=norm_qty,
                base_unit=base_unit,
                unit_price_paise=unit_price_paise,
                total_paise=total_paise,
                confidence=0.84,
                source_evidence=str(raw_item.get("raw_text") or name),
            )
        )

    cgst = _to_paise(vlm_data.get("cgst"))
    sgst = _to_paise(vlm_data.get("sgst"))
    igst = _to_paise(vlm_data.get("igst"))
    tax_total = _to_paise(vlm_data.get("tax_total"))
    parts = [v for v in (cgst, sgst, igst) if v is not None]
    eff_tax = sum(parts) if parts else tax_total

    raw_date = vlm_data.get("receipt_date")
    parsed_date = parse_flexible_date(str(raw_date)) if raw_date else None

    return ExtractedReceipt(
        filename=filename,
        content_type=content_type,
        source_type=source_type,
        adapter_used="hf_qwen2_5_vl",
        merchant_name=vlm_data.get("merchant_name"),
        receipt_number=vlm_data.get("receipt_number"),
        receipt_date=parsed_date.isoformat() if parsed_date else (str(raw_date) if raw_date else None),
        currency=str(vlm_data.get("currency") or "INR"),
        lines=lines,
        subtotal_paise=_to_paise(vlm_data.get("subtotal")),
        discount_paise=_to_paise(vlm_data.get("discount")),
        tax_paise=eff_tax,
        tax_breakdown=ReceiptTaxBreakdown(cgst_paise=cgst, sgst_paise=sgst, igst_paise=igst),
        rounding_paise=_to_paise(vlm_data.get("rounding"), signed=True),
        grand_total_paise=_to_paise(vlm_data.get("grand_total")),
        payment_method=vlm_data.get("payment_method"),
        quality_report=quality_report,
        confidence=0.84,
        warnings=list(initial_warnings),
        raw_text="",
    )


def _run_local_ocr_on_images_sync(
    image_bytes_list: list[bytes],
    *,
    is_pdf_pages: bool = False,
) -> tuple[list[OcrTokenBox], str, str, ReceiptQualityReport | None, list[str]]:
    """Run Stage B preprocessing + Stage C local OCR across 1..N images/pages."""
    warnings: list[str] = []
    all_boxes: list[OcrTokenBox] = []
    raw_texts: list[str] = []
    first_quality: ReceiptQualityReport | None = None
    provider = _select_local_ocr_provider()
    adapter_name = provider.name if provider else "ocr_provider_unavailable"

    if provider is None:
        warnings.append(
            "OCR_PROVIDER_UNAVAILABLE: No local OCR engine (ocrmac or tesseract) is available; returning draft for manual review."
        )

    for idx, img_bytes in enumerate(image_bytes_list):
        try:
            prep = preprocess_receipt_image(img_bytes)
        except Exception as exc:
            warnings.append(f"Image preprocessing failed ({type(exc).__name__}); manual review needed.")
            continue

        if idx == 0:
            first_quality = prep.quality_report
        warnings.extend(prep.warnings)

        if not prep.quality_report.is_readable:
            continue

        if provider is None:
            continue

        ocr_out: RawOcrOutput = provider.recognize_image(prep.enhanced_png_bytes, page=idx + 1)
        # If perspective-warped/enhanced image yielded nothing, retry once on original PNG
        if not ocr_out.boxes and prep.enhanced_png_bytes != prep.original_png_bytes:
            ocr_out = provider.recognize_image(prep.original_png_bytes, page=idx + 1)

        warnings.extend(ocr_out.warnings)
        all_boxes.extend(ocr_out.boxes)
        if ocr_out.raw_text:
            raw_texts.append(ocr_out.raw_text)

    if is_pdf_pages and not all_boxes and not any("tesseract" in w or "ocrmac" in w or "blurry" in w.lower() for w in warnings):
        warnings.append("PDF contained no extractable text; manual review required.")

    return (
        all_boxes,
        "\n".join(raw_texts),
        adapter_name,
        first_quality,
        list(dict.fromkeys(warnings)),
    )


async def extract_receipt(
    filename: str,
    content_type: str,
    data: bytes,
) -> ExtractedReceipt:
    """Extract structured receipt data from text, PDF, or image bytes."""
    started = time.perf_counter()
    ct = (content_type or "").lower().split(";")[0].strip()
    ext = Path(filename or "").suffix.lower()

    if not data:
        return ExtractedReceipt(
            filename=filename,
            content_type=content_type,
            source_type="unknown",
            adapter_used="validation",
            latency_ms=int((time.perf_counter() - started) * 1000),
            confidence=0.0,
            needs_review=True,
            warnings=["Uploaded file is empty."],
        )

    if len(data) > MAX_RECEIPT_BYTES:
        return ExtractedReceipt(
            filename=filename,
            content_type=content_type,
            source_type="unknown",
            adapter_used="validation",
            latency_ms=int((time.perf_counter() - started) * 1000),
            confidence=0.0,
            needs_review=True,
            warnings=[f"Uploaded file exceeds maximum size of {MAX_RECEIPT_BYTES // (1024 * 1024)} MB."],
        )

    # 1. Plain text transcripts
    if ct.startswith("text/") or ext in {".txt", ".csv", ".log"}:
        text = data.decode("utf-8", errors="replace")
        receipt = parse_receipt_text_and_boxes(
            raw_text=text,
            filename=filename,
            content_type=content_type,
            source_type="text",
            adapter_used="deterministic_text_parser",
            base_confidence=0.95,
        )
        receipt = validate_extracted_receipt(receipt)
        receipt.latency_ms = int((time.perf_counter() - started) * 1000)
        return receipt

    # 2. PDFs (digital or scanned, single or multi-page)
    if ct == "application/pdf" or ext == ".pdf":
        pdf_res = await asyncio.to_thread(extract_pdf_inputs, data)
        if pdf_res.is_digital:
            receipt = parse_receipt_text_and_boxes(
                raw_text=pdf_res.digital_text,
                boxes=pdf_res.digital_boxes or None,
                filename=filename,
                content_type=content_type,
                source_type="pdf_digital",
                adapter_used="pymupdf_spatial_parser",
                base_confidence=0.92,
                initial_warnings=pdf_res.warnings,
            )
            receipt = validate_extracted_receipt(receipt)
            receipt.latency_ms = int((time.perf_counter() - started) * 1000)
            return receipt

        if pdf_res.page_images:
            boxes, raw_text, adapter_name, quality, ocr_warnings = await asyncio.to_thread(
                _run_local_ocr_on_images_sync, pdf_res.page_images, is_pdf_pages=True
            )
            all_warnings = list(dict.fromkeys(pdf_res.warnings + ocr_warnings))
            receipt = parse_receipt_text_and_boxes(
                raw_text=raw_text,
                boxes=boxes or None,
                filename=filename,
                content_type=content_type,
                source_type="pdf_scanned",
                adapter_used=adapter_name,
                base_confidence=0.78 if boxes else 0.0,
                initial_warnings=all_warnings,
            )
            receipt.quality_report = quality
            receipt = validate_extracted_receipt(receipt)
            receipt.latency_ms = int((time.perf_counter() - started) * 1000)
            return receipt

        receipt = ExtractedReceipt(
            filename=filename,
            content_type=content_type,
            source_type="pdf_scanned",
            adapter_used="pymupdf_error",
            confidence=0.0,
            needs_review=True,
            warnings=pdf_res.warnings or ["PDF contained no extractable content; manual review required."],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return receipt

    # 3. Camera / Gallery images
    if ct.startswith("image/") or ext in {".png", ".jpg", ".jpeg", ".webp", ".heic", ".bmp"}:
        boxes, raw_text, adapter_name, quality, ocr_warnings = await asyncio.to_thread(
            _run_local_ocr_on_images_sync, [data], is_pdf_pages=False
        )
        grid_xs = await asyncio.to_thread(detect_table_vertical_lines, data)
        receipt = parse_receipt_text_and_boxes(
            raw_text=raw_text,
            boxes=boxes or None,
            vertical_grid_xs=grid_xs or None,
            filename=filename,
            content_type=content_type,
            source_type="image",
            adapter_used=adapter_name,
            base_confidence=0.80 if boxes else 0.0,
            initial_warnings=ocr_warnings,
        )
        receipt.quality_report = quality
        receipt = validate_extracted_receipt(receipt)

        # Selective VLM escalation ONLY when enabled and local result is unreadable/unreconciled
        vlm = HuggingFaceVlmProvider()
        if (
            vlm.is_available()
            and (quality is None or quality.is_readable)
            and (not receipt.lines or not receipt.math_reconciled)
        ):
            prep = await asyncio.to_thread(preprocess_receipt_image, data)
            vlm_json = await vlm.extract_structured(prep.enhanced_png_bytes)
            if vlm_json:
                vlm_receipt = _vlm_dict_to_receipt(
                    vlm_json,
                    filename=filename,
                    content_type=content_type,
                    source_type="image",
                    quality_report=quality,
                    initial_warnings=ocr_warnings,
                )
                vlm_receipt.raw_text = raw_text
                vlm_receipt = validate_extracted_receipt(vlm_receipt)
                if vlm_receipt.math_reconciled or len(vlm_receipt.lines) > len(receipt.lines):
                    receipt = vlm_receipt

        receipt.latency_ms = int((time.perf_counter() - started) * 1000)
        return receipt

    return ExtractedReceipt(
        filename=filename,
        content_type=content_type,
        source_type="unknown",
        adapter_used="validation",
        latency_ms=int((time.perf_counter() - started) * 1000),
        confidence=0.0,
        needs_review=True,
        warnings=[f"Unsupported receipt content type: {content_type or ext or 'unknown'}."],
    )
