"""Isolated Experimental OCR Service (zero writes to purchases, inventory, or learning)."""
from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from app.core.config import Settings
from app.infra.ocr.pipeline import extract_receipt
from app.modules.experimental_ocr.provider import ExperimentalV10_6ExtractionProvider
from app.modules.experimental_ocr.schemas import (
    ExistingOcrSnapshot,
    ExtractModeType,
    ExperimentalExtractionResult,
    ExperimentalHealthOut,
    ExperimentalJobOut,
)

# Isolated household-scoped job store (never touches production DB tables)
_EXPERIMENTAL_JOBS: dict[str, dict[str, ExperimentalJobOut]] = {}


def get_experimental_health(settings: Settings) -> ExperimentalHealthOut:
    api_key = (settings.EXPERIMENTAL_OCR_API_KEY or settings.HF_TOKEN).strip()
    base_ok = bool(settings.EXPERIMENTAL_OCR_BASE_URL.strip())
    key_ok = bool(api_key)
    return ExperimentalHealthOut(
        enabled=settings.EXPERIMENTAL_OCR_ENABLED,
        provider=settings.EXPERIMENTAL_OCR_PROVIDER,
        provider_configured=base_ok and key_ok,
        model=settings.EXPERIMENTAL_OCR_MODEL,
        base_url_configured=base_ok,
        api_key_configured=key_ok,
        ppocrv6_available=ExperimentalV10_6ExtractionProvider.is_ppocr_available(),
        connectivity_status="not_checked",
        isolation_guaranteed=True,
    )


async def _run_existing_readonly(filename: str, content_type: str, data: bytes) -> ExistingOcrSnapshot:
    t0 = time.perf_counter()
    try:
        ext = await extract_receipt(filename=filename, content_type=content_type, data=data)
        return ExistingOcrSnapshot(
            status="completed",
            adapter_used=ext.adapter_used,
            latency_ms=round((time.perf_counter() - t0) * 1000.0, 1),
            merchant_name=ext.merchant_name,
            receipt_date=ext.receipt_date,
            subtotal_rupees=round(ext.subtotal_paise / 100.0, 2) if ext.subtotal_paise is not None else None,
            tax_rupees=round(ext.tax_paise / 100.0, 2) if ext.tax_paise is not None else None,
            grand_total_rupees=round(ext.grand_total_paise / 100.0, 2) if ext.grand_total_paise is not None else None,
            items=[
                {
                    "name": ln.name,
                    "qty": ln.quantity,
                    "unit": ln.unit,
                    "unit_price_rupees": round(ln.unit_price_paise / 100.0, 2) if ln.unit_price_paise is not None else None,
                    "line_total_rupees": round(ln.total_paise / 100.0, 2) if ln.total_paise is not None else None,
                }
                for ln in ext.lines
            ],
            warnings=list(ext.warnings),
        )
    except Exception as exc:
        return ExistingOcrSnapshot(
            status="failed",
            latency_ms=round((time.perf_counter() - t0) * 1000.0, 1),
            error=f"{type(exc).__name__}: {exc}",
        )


async def run_experimental_extract(
    *,
    settings: Settings,
    household_id: str,
    filename: str,
    content_type: str,
    image_bytes: bytes,
    mode: ExtractModeType,
    provider_override: ExperimentalV10_6ExtractionProvider | None = None,
) -> ExperimentalJobOut:
    job_id = f"exp_{uuid4().hex[:12]}"
    exp_res: ExperimentalExtractionResult | None = None
    existing_res: ExistingOcrSnapshot | None = None

    if mode in ("existing", "compare"):
        existing_res = await _run_existing_readonly(filename, content_type, image_bytes)

    if mode in ("experimental", "compare"):
        provider = provider_override or ExperimentalV10_6ExtractionProvider(settings)
        exp_res = await provider.extract(image_bytes=image_bytes, content_type=content_type)

    job = ExperimentalJobOut(
        job_id=job_id,
        household_id=household_id,
        mode=mode,
        filename=filename,
        experimental_result=exp_res,
        existing_result=existing_res,
        inventory_mutated=False,
    )
    _EXPERIMENTAL_JOBS.setdefault(household_id, {})[job_id] = job
    return job


def get_experimental_job(household_id: str, job_id: str) -> ExperimentalJobOut | None:
    return _EXPERIMENTAL_JOBS.get(household_id, {}).get(job_id)


def attach_ground_truth(household_id: str, job_id: str, gt: dict[str, Any]) -> ExperimentalJobOut | None:
    job = get_experimental_job(household_id, job_id)
    if job is None:
        return None
    job.ground_truth = gt
    return job
