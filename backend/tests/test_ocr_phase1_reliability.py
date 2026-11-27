"""Phase 1 receipt upload reliability tests: preprocessing reuse & timing observability."""

from __future__ import annotations

import logging
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.core.errors import AppError
from app.infra.ocr.pipeline import extract_receipt
from app.infra.ocr.preprocessing import PreprocessedImageResult
from app.infra.ocr.providers import RawOcrOutput
from app.infra.ocr.schemas import ReceiptQualityReport
from app.modules.experimental_ocr.orchestrator import orchestrate_receipt_extraction


@pytest.mark.asyncio
async def test_vlm_escalation_reuses_preprocessed_image_without_reprocessing() -> None:
    """When local OCR yields no reconciled lines and VLM is enabled, preprocess_receipt_image
    must only be called ONCE (reusing enhanced_png_bytes for VLM escalation).
    """
    fake_prep = PreprocessedImageResult(
        enhanced_png_bytes=b"ENHANCED_PNG_BYTES",
        original_png_bytes=b"ORIGINAL_PNG_BYTES",
        quality_report=ReceiptQualityReport(is_readable=True),
        warnings=[],
    )
    fake_local_provider = MagicMock()
    fake_local_provider.name = "tesseract_tsv"
    fake_local_provider.is_available.return_value = True
    fake_local_provider.recognize_image.return_value = RawOcrOutput(
        adapter_name="tesseract_tsv",
        boxes=[],
        raw_text="",
        warnings=[],
    )

    fake_vlm = MagicMock()
    fake_vlm.is_available.return_value = True
    fake_vlm.extract_structured = AsyncMock(
        return_value={
            "merchant_name": "Fresh Mart",
            "currency": "INR",
            "grand_total": 100.0,
            "items": [
                {"name": "Milk", "quantity": 1, "unit": "l", "unit_price": 100.0, "total_price": 100.0}
            ],
        }
    )

    with (
        patch("app.infra.ocr.pipeline.preprocess_receipt_image", return_value=fake_prep) as mock_prep,
        patch("app.infra.ocr.pipeline._select_local_ocr_provider", return_value=fake_local_provider),
        patch("app.infra.ocr.pipeline.detect_table_vertical_lines", return_value=[]),
        patch("app.infra.ocr.pipeline.HuggingFaceVlmProvider", return_value=fake_vlm),
    ):
        result = await extract_receipt("receipt.jpg", "image/jpeg", b"FAKE_JPEG_DATA")

    assert mock_prep.call_count == 1
    fake_vlm.extract_structured.assert_awaited_once_with(b"ENHANCED_PNG_BYTES")
    assert result.merchant_name == "Fresh Mart"
    assert len(result.lines) == 1


@pytest.mark.asyncio
async def test_orchestrator_logs_fallback_timeout_exception_and_preserves_502(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def _failing_ocr(**_kwargs: object) -> dict[str, object]:
        raise subprocess.TimeoutExpired(cmd=["tesseract"], timeout=30)

    settings = Settings(EXPERIMENTAL_OCR_ENABLED=False, EXPERIMENTAL_OCR_PRIMARY=False)

    with caplog.at_level(logging.ERROR, logger="app.modules.experimental_ocr.orchestrator"):
        with pytest.raises(AppError) as exc_info:
            await orchestrate_receipt_extraction(
                settings=settings,
                existing_extract_fn=_failing_ocr,
                file_bytes=b"FAKE_IMAGE",
                filename="receipt.jpg",
                content_type="image/jpeg",
            )

    assert exc_info.value.status_code == 502
    assert exc_info.value.code == "ALL_PROVIDERS_FAILED"
    assert "TimeoutExpired" in exc_info.value.message
    assert any(
        "Fallback OCR failed" in rec.message and rec.exc_info is not None
        for rec in caplog.records
    )
