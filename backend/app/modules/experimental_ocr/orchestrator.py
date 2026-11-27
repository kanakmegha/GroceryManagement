"""Unified OCR Orchestrator: Experimental V10.6 Primary + Existing OCR Fallback."""

from __future__ import annotations

import inspect
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.config import Settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.experimental_ocr.provider import ExperimentalV10_6ExtractionProvider
from app.modules.experimental_ocr.schemas import (
    ExperimentalExtractionResult,
    OcrOrchestrationMetadata,
)

logger = get_logger(__name__)


def _to_paise(val: float | None) -> int:
    return int(round(float(val) * 100)) if val is not None else 0


def _convert_experimental_to_draft_dict(
    exp: ExperimentalExtractionResult,
    orch: OcrOrchestrationMetadata,
) -> dict[str, Any]:
    """Convert V10.8 ExperimentalExtractionResult into purchases/service.py draft dict without
    inventing values.
    """
    items_out: list[dict[str, Any]] = []
    for it in exp.items:
        name = (it.product_name.value or "").strip()
        if not name:
            continue
        qty = float(it.quantity.value) if it.quantity.value is not None else 1.0
        unit = (it.unit_of_measure.value or "pcs").strip().lower()
        lt_paise = _to_paise(it.line_total.value)
        # Preserve OBSERVED vs DERIVED provenance: never label a derived unit_price as observed
        up_paise = _to_paise(it.unit_price.value) if it.unit_price.provenance == "OBSERVED" else 0
        disc_paise = _to_paise(it.item_discount.value)
        tax_val = (
            it.gst.observed_tax_total
            if it.gst.observed_tax_total is not None
            else it.gst.derived_tax_total
        )
        tax_paise = _to_paise(tax_val)
        item_warnings: list[str] = []
        if it.unit_price.provenance == "DERIVED":
            item_warnings.append(
                "Unit price derived from line_total / qty (not independently printed)."
            )

        items_out.append(
            {
                "raw_text": name,
                "display_name": name,
                "pack_size": it.package_size.value,
                "qty": qty,
                "unit": unit,
                "unit_price_paise": up_paise,
                "unit_price_provenance": it.unit_price.provenance,
                "line_total_paise": lt_paise,
                "discount_paise": disc_paise,
                "tax_paise": tax_paise,
                "confidence": it.product_name.confidence,
                "warnings": item_warnings,
            }
        )

    cgst_p = sum(_to_paise(it.gst.cgst_amount) for it in exp.items)
    sgst_p = sum(_to_paise(it.gst.sgst_amount) for it in exp.items)
    igst_p = sum(_to_paise(it.gst.igst_amount) for it in exp.items)
    cess_p = sum(_to_paise(it.gst.cess_amount) for it in exp.items)
    charges_p = sum(
        _to_paise(f.actual_amount)
        for f in exp.fees
        if f.charge_status in {"CHARGED", "DISCOUNTED"} and f.actual_amount is not None
    )
    sub_val = (
        exp.subtotal_after_tax.value
        if exp.subtotal_after_tax.value is not None
        else exp.subtotal_before_tax.value
    )

    warnings = list(exp.warnings) + list(orch.validation_warnings)
    return {
        "adapter": exp.provider,
        "ocr_adapter": exp.provider,
        "merchant_name": exp.merchant_evidence.observed_merchant,
        "purchased_on": exp.date_evidence.parsed_date,
        "subtotal_paise": _to_paise(sub_val) if sub_val is not None else None,
        "discount_paise": 0,
        "additional_charges_paise": charges_p,
        "cgst_paise": cgst_p,
        "sgst_paise": sgst_p,
        "igst_paise": igst_p,
        "cess_paise": cess_p,
        "tax_paise": cgst_p + sgst_p + igst_p,
        "total_amount_paise": _to_paise(exp.grand_total.value)
        if exp.grand_total.value is not None
        else None,
        "items": items_out,
        "warnings": list(dict.fromkeys(warnings)),
        "confidence": 0.9 if orch.extraction_status == "EXPERIMENTAL_SUCCESS" else 0.75,
        "raw_payload": {
            "orchestration": orch.model_dump(),
            "v10_8_result": exp.model_dump(),
            "warnings": list(dict.fromkeys(warnings)),
            "confidence": 0.9 if orch.extraction_status == "EXPERIMENTAL_SUCCESS" else 0.75,
        },
    }


async def _invoke_existing_ocr(
    extract_fn: Callable[..., Any],
    *,
    file_bytes: bytes | None,
    filename: str | None,
    content_type: str | None,
    raw_text: str | None,
) -> dict[str, Any]:
    data = file_bytes if file_bytes is not None else (raw_text or "").encode("utf-8")
    eff_filename = filename or ("receipt.txt" if raw_text is not None else "receipt.bin")
    eff_content_type = content_type or (
        "text/plain" if raw_text is not None else "application/octet-stream"
    )

    sig = inspect.signature(extract_fn)
    if {"filename", "content_type", "data"}.issubset(sig.parameters):
        result = extract_fn(filename=eff_filename, content_type=eff_content_type, data=data)
    else:
        result = extract_fn(
            file_bytes=data, filename=eff_filename, content_type=eff_content_type, raw_text=raw_text
        )
    if inspect.isawaitable(result):
        result = await result

    if isinstance(result, dict):
        return result
    if hasattr(result, "model_dump"):
        return dict(result.model_dump())
    return {
        "adapter": getattr(
            result, "adapter", getattr(result, "ocr_adapter", getattr(result, "source", "ocr"))
        ),
        "merchant_name": getattr(result, "merchant_name", None),
        "purchased_on": getattr(result, "purchased_on", None),
        "total_amount_paise": getattr(result, "total_amount_paise", None),
        "items": getattr(result, "items", []),
        "raw_payload": getattr(result, "raw_payload", None),
    }


def _evaluate_experimental_usability(
    exp: ExperimentalExtractionResult,
) -> tuple[bool, bool, str | None, list[str]]:
    """Return (usable, is_partial, fallback_reason, warnings)."""
    if exp.status != "completed":
        return False, False, exp.error or "EXPERIMENTAL_FAILED", []
    usable_items = [it for it in exp.items if (it.product_name.value or "").strip()]
    if not usable_items:
        return False, False, "EXPERIMENTAL_ZERO_USABLE_ITEMS", []
    if exp.grand_total.value is not None and exp.grand_total.value < 0:
        return False, False, "EXPERIMENTAL_INVALID_NEGATIVE_TOTAL", []

    partial_warnings: list[str] = []
    if exp.merchant_evidence.unavailable_merchant:
        partial_warnings.append("Merchant name not printed on receipt; review if needed.")
    if exp.date_evidence.ambiguity_status != "UNAMBIGUOUS":
        partial_warnings.append(
            f"Date ambiguity ({exp.date_evidence.ambiguity_status}); verify purchase date."
        )
    if exp.grand_total.value is None:
        partial_warnings.append("Grand total not detected; verify before confirming.")
    return True, bool(partial_warnings), None, partial_warnings


async def orchestrate_receipt_extraction(
    *,
    settings: Settings,
    existing_extract_fn: Callable[..., Any],
    file_bytes: bytes | None = None,
    filename: str | None = None,
    content_type: str | None = None,
    raw_text: str | None = None,
    experimental_provider: ExperimentalV10_6ExtractionProvider | None = None,
) -> dict[str, Any]:
    """Primary: Experimental V10.6. Fallback: Existing OCR. Never merges results or mutates
    inventory.
    """
    t0 = time.perf_counter()
    job_id = f"orch_{uuid4().hex[:12]}"
    ct = (content_type or "").lower().split(";")[0].strip()
    ext = Path(filename or "").suffix.lower()
    is_image = bool(file_bytes) and (
        ct.startswith("image/") or ext in {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
    )

    primary_status = "SKIPPED"
    primary_ms = 0.0
    fallback_reason: str | None = None

    if (
        raw_text is None
        and is_image
        and settings.EXPERIMENTAL_OCR_ENABLED
        and settings.EXPERIMENTAL_OCR_PRIMARY
    ):
        prov = experimental_provider or ExperimentalV10_6ExtractionProvider(settings)
        exp_res = await prov.extract(image_bytes=file_bytes or b"", content_type=ct or "image/jpeg")
        primary_ms = exp_res.latency_ms
        usable, is_partial, reason, p_warnings = _evaluate_experimental_usability(exp_res)
        if usable:
            status_str = "EXPERIMENTAL_PARTIAL" if is_partial else "EXPERIMENTAL_SUCCESS"
            orch = OcrOrchestrationMetadata(
                extraction_job_id=job_id,
                extraction_status=status_str,  # type: ignore[arg-type]
                primary_provider=settings.EXPERIMENTAL_OCR_PROVIDER,
                primary_model=settings.EXPERIMENTAL_OCR_MODEL,
                primary_status=exp_res.status,
                fallback_triggered=False,
                selected_provider=settings.EXPERIMENTAL_OCR_PROVIDER,
                primary_latency_ms=primary_ms,
                total_latency_ms=round((time.perf_counter() - t0) * 1000.0, 1),
                validation_warnings=p_warnings,
            )
            return _convert_experimental_to_draft_dict(exp_res, orch)
        primary_status = exp_res.status
        fallback_reason = reason
    elif not settings.EXPERIMENTAL_OCR_ENABLED or not settings.EXPERIMENTAL_OCR_PRIMARY:
        fallback_reason = "EXPERIMENTAL_OCR_DISABLED_OR_NOT_PRIMARY"
    elif not is_image:
        fallback_reason = "NON_IMAGE_INPUT_ROUTED_TO_EXISTING_OCR"

    if not settings.EXISTING_OCR_FALLBACK_ENABLED and primary_status != "SKIPPED":
        raise AppError(
            status_code=502,
            code="ALL_PROVIDERS_FAILED",
            message=f"Experimental OCR failed ({fallback_reason}) and fallback is disabled.",
        )

    fb_t0 = time.perf_counter()
    try:
        fb_dict = await _invoke_existing_ocr(
            existing_extract_fn,
            file_bytes=file_bytes,
            filename=filename,
            content_type=content_type,
            raw_text=raw_text,
        )
    except Exception as exc:
        logger.exception(
            "Fallback OCR failed (primary=%s, fallback_reason=%s): %s",
            primary_status,
            fallback_reason,
            type(exc).__name__,
        )
        raise AppError(
            status_code=502,
            code="ALL_PROVIDERS_FAILED",
            message=(
                f"Both experimental ({fallback_reason}) and fallback OCR failed "
                f"({type(exc).__name__})."
            ),
        ) from exc

    fb_ms = round((time.perf_counter() - fb_t0) * 1000.0, 1)
    fb_adapter = str(
        fb_dict.get("adapter_used")
        or fb_dict.get("adapter")
        or fb_dict.get("ocr_adapter")
        or "apple_vision_ocrmac"
    )
    fb_items = fb_dict.get("items") or fb_dict.get("lines") or []
    fb_total = fb_dict.get("grand_total_paise") or fb_dict.get("total_amount_paise")
    if (
        primary_status != "SKIPPED"
        and not fb_items
        and fb_total is None
        and fb_adapter in {"validation", "ocr_provider_unavailable", "pymupdf_error"}
    ):
        raise AppError(
            status_code=502,
            code="ALL_PROVIDERS_FAILED",
            message=(
                f"Both experimental ({fallback_reason}) and fallback OCR failed to extract "
                "receipt data."
            ),
        )

    orch = OcrOrchestrationMetadata(
        extraction_job_id=job_id,
        extraction_status="FALLBACK_SUCCESS",
        primary_provider=settings.EXPERIMENTAL_OCR_PROVIDER,
        primary_model=settings.EXPERIMENTAL_OCR_MODEL,
        primary_status=primary_status,
        fallback_triggered=primary_status != "SKIPPED",
        fallback_reason=fallback_reason,
        fallback_provider=fb_adapter,
        selected_provider=fb_adapter,
        primary_latency_ms=primary_ms,
        fallback_latency_ms=fb_ms,
        total_latency_ms=round((time.perf_counter() - t0) * 1000.0, 1),
    )
    raw_p = dict(fb_dict.get("raw_payload") or fb_dict)
    raw_p["orchestration"] = orch.model_dump()
    fb_dict["raw_payload"] = raw_p
    return fb_dict
