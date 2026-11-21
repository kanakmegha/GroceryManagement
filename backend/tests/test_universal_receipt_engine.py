"""Comprehensive regression and unit tests for the universal receipt extraction engine."""

from __future__ import annotations

import io
import json

import cv2
import fitz
import httpx
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from app.infra.ocr.pipeline import extract_receipt
from app.infra.ocr.preprocessing import MAX_RECEIPT_BYTES, preprocess_receipt_image
from app.infra.ocr.providers import (
    AzureReceiptProvider,
    HuggingFaceVlmProvider,
    VeryfiReceiptProvider,
)
from app.infra.ocr.schemas import ExtractedReceipt, ExtractedReceiptLine, OcrTokenBox
from app.infra.ocr.spatial_parser import parse_receipt_text_and_boxes
from app.infra.ocr.validation import validate_extracted_receipt


@pytest.mark.asyncio
async def test_santushti_bhojanam_spatial_and_wrapped_regression() -> None:
    """Mandatory regression test for Santushti Bhojanam receipt with wrapped description & GST."""
    # Simulate bounding-boxed OCR output with wrapped "Patiala Kadai" / "Paneer"
    # and closely spaced Qty / Rate / Amount columns
    boxes = [
        OcrTokenBox(text="SANTUSHTI BHOJANAM", confidence=0.96, x=0.22, y=0.05, w=0.55, h=0.03),
        OcrTokenBox(text="Bill No: 2043", confidence=0.94, x=0.08, y=0.12, w=0.30, h=0.025),
        OcrTokenBox(text="Date: 26/04/26", confidence=0.93, x=0.55, y=0.12, w=0.35, h=0.025),
        OcrTokenBox(text="Item", confidence=0.95, x=0.08, y=0.18, w=0.15, h=0.02),
        OcrTokenBox(text="Qty", confidence=0.95, x=0.45, y=0.18, w=0.08, h=0.02),
        OcrTokenBox(text="Rate", confidence=0.95, x=0.60, y=0.18, w=0.10, h=0.02),
        OcrTokenBox(text="Amount", confidence=0.95, x=0.78, y=0.18, w=0.15, h=0.02),
        # Line 1: Phulka 30 x 15.00 = 450.00
        OcrTokenBox(text="Phulka", confidence=0.94, x=0.08, y=0.24, w=0.18, h=0.025),
        OcrTokenBox(text="30", confidence=0.92, x=0.46, y=0.24, w=0.06, h=0.025),
        OcrTokenBox(text="15.00", confidence=0.91, x=0.60, y=0.24, w=0.10, h=0.025),
        OcrTokenBox(text="450.00", confidence=0.93, x=0.78, y=0.24, w=0.14, h=0.025),
        # Line 2: Wrapped "Patiala Kadai" + "Paneer 5 220.00 1,100.00"
        OcrTokenBox(text="Patiala Kadai", confidence=0.90, x=0.08, y=0.30, w=0.30, h=0.025),
        OcrTokenBox(text="Paneer", confidence=0.91, x=0.08, y=0.335, w=0.16, h=0.025),
        OcrTokenBox(text="5", confidence=0.92, x=0.47, y=0.335, w=0.04, h=0.025),
        OcrTokenBox(text="220.00", confidence=0.90, x=0.60, y=0.335, w=0.11, h=0.025),
        OcrTokenBox(text="1,100.00", confidence=0.92, x=0.76, y=0.335, w=0.16, h=0.025),
        # Line 3: Chole Masala 3 180.00 540.00
        OcrTokenBox(text="Chole Masala", confidence=0.93, x=0.08, y=0.40, w=0.28, h=0.025),
        OcrTokenBox(text="3", confidence=0.94, x=0.47, y=0.40, w=0.04, h=0.025),
        OcrTokenBox(text="180.00", confidence=0.92, x=0.60, y=0.40, w=0.11, h=0.025),
        OcrTokenBox(text="540.00", confidence=0.93, x=0.78, y=0.40, w=0.14, h=0.025),
        # Totals & GST
        OcrTokenBox(text="Subtotal", confidence=0.95, x=0.08, y=0.50, w=0.20, h=0.025),
        OcrTokenBox(text="2,090.00", confidence=0.94, x=0.76, y=0.50, w=0.16, h=0.025),
        OcrTokenBox(text="CGST 2.5%", confidence=0.93, x=0.08, y=0.55, w=0.22, h=0.025),
        OcrTokenBox(text="52.25", confidence=0.92, x=0.80, y=0.55, w=0.12, h=0.025),
        OcrTokenBox(text="SGST 2.5%", confidence=0.93, x=0.08, y=0.60, w=0.22, h=0.025),
        OcrTokenBox(text="52.25", confidence=0.92, x=0.80, y=0.60, w=0.12, h=0.025),
        OcrTokenBox(text="Rounding", confidence=0.91, x=0.08, y=0.65, w=0.20, h=0.025),
        OcrTokenBox(text="+0.50", confidence=0.90, x=0.80, y=0.65, w=0.12, h=0.025),
        OcrTokenBox(text="Grand Total", confidence=0.96, x=0.08, y=0.72, w=0.25, h=0.03),
        OcrTokenBox(text="₹2,195.00", confidence=0.95, x=0.75, y=0.72, w=0.18, h=0.03),
    ]

    parsed = parse_receipt_text_and_boxes(
        boxes=boxes,
        filename="santushti.jpg",
        content_type="image/jpeg",
        source_type="image",
        adapter_used="apple_vision_ocrmac",
        base_confidence=0.92,
    )
    validated = validate_extracted_receipt(parsed)

    assert validated.merchant_name == "SANTUSHTI BHOJANAM"
    assert validated.receipt_number == "2043"
    assert validated.receipt_date == "2026-04-26"
    assert len(validated.lines) == 3

    assert validated.lines[0].name == "Phulka"
    assert validated.lines[0].quantity == 30.0
    assert validated.lines[0].unit_price_paise == 1500
    assert validated.lines[0].total_paise == 45000

    assert validated.lines[1].name == "Patiala Kadai Paneer"
    assert validated.lines[1].quantity == 5.0
    assert validated.lines[1].unit_price_paise == 22000
    assert validated.lines[1].total_paise == 110000

    assert validated.lines[2].name == "Chole Masala"
    assert validated.lines[2].quantity == 3.0
    assert validated.lines[2].unit_price_paise == 18000
    assert validated.lines[2].total_paise == 54000

    assert validated.subtotal_paise == 209000
    assert validated.tax_breakdown.cgst_paise == 5225
    assert validated.tax_breakdown.sgst_paise == 5225
    assert validated.tax_paise == 10450
    assert validated.rounding_paise == 50
    assert validated.grand_total_paise == 219500
    assert validated.computed_total_paise == 219500
    assert validated.math_reconciled is True
    assert validated.needs_review is False


def test_opencv_preprocessing_handles_dark_background_and_shadows() -> None:
    """Verify IQA + perspective/illumination preprocessing on a tilted shadowed receipt."""
    # Create a dark wooden-like textured canvas (900x1100) with a bright white receipt card inside
    canvas = np.full((1100, 900, 3), (45, 35, 28), dtype=np.uint8)
    receipt = np.full((820, 480, 3), 235, dtype=np.uint8)
    pil_r = Image.fromarray(receipt)
    draw = ImageDraw.Draw(pil_r)
    font = ImageFont.load_default(size=22)
    draw.text((30, 30), "SANTUSHTI BHOJANAM", fill=(30, 30, 30), font=font)
    draw.text((30, 90), "Phulka        30  15.00   450.00", fill=(30, 30, 30), font=font)
    draw.text((30, 140), "Chole Masala   3 180.00   540.00", fill=(30, 30, 30), font=font)
    draw.text((30, 200), "Grand Total             990.00", fill=(30, 30, 30), font=font)

    # Paste receipt onto canvas with uneven shadow gradient
    r_np = np.array(pil_r)
    canvas[140:960, 210:690] = r_np
    grad = np.linspace(0.55, 1.0, 900, dtype=np.float32)[None, :, None]
    shadowed = np.clip(canvas.astype(np.float32) * grad, 0, 255).astype(np.uint8)

    ok, buf = cv2.imencode(".jpg", shadowed)
    assert ok
    res = preprocess_receipt_image(buf.tobytes())

    assert res.quality_report.is_readable is True
    assert res.quality_report.perspective_corrected is True
    assert res.quality_report.illumination_normalized is True
    assert "perspective_warp" in res.quality_report.applied_steps


@pytest.mark.asyncio
async def test_unreadable_blank_and_oversized_images_rejected_gracefully() -> None:
    """Blank or tiny images must be flagged unreadable rather than fabricating data."""
    blank = Image.new("RGB", (600, 800), (245, 245, 245))
    buf = io.BytesIO()
    blank.save(buf, format="PNG")
    res = await extract_receipt("blank.png", "image/png", buf.getvalue())
    assert res.needs_review is True
    assert res.lines == []
    assert res.quality_report is not None
    assert res.quality_report.is_readable is False

    tiny = Image.new("RGB", (80, 80), (255, 255, 255))
    tbuf = io.BytesIO()
    tiny.save(tbuf, format="PNG")
    res_tiny = await extract_receipt("tiny.png", "image/png", tbuf.getvalue())
    assert res_tiny.needs_review is True
    assert res_tiny.quality_report is not None
    assert res_tiny.quality_report.is_readable is False

    oversized = b"0" * (MAX_RECEIPT_BYTES + 1)
    res_big = await extract_receipt("huge.jpg", "image/jpeg", oversized)
    assert res_big.needs_review is True
    assert any("exceeds maximum size" in w for w in res_big.warnings)


@pytest.mark.asyncio
async def test_digital_multipage_pdf_and_malformed_pdf() -> None:
    """Verify multi-page digital PDF extraction and malformed PDF protection."""
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((50, 72), "BIGBASKET GROCERY\nDate: 2026-11-15\nTata Salt 1 kg   Rs. 28.00\n")
    p2 = doc.new_page()
    p2.insert_text((50, 72), "Amul Butter 500 g   Rs. 275.00\nGrand Total   Rs. 303.00\n")
    pdf_bytes = doc.tobytes()
    doc.close()

    res = await extract_receipt("bigbasket.pdf", "application/pdf", pdf_bytes)
    assert res.source_type == "pdf_digital"
    assert res.merchant_name == "BIGBASKET GROCERY"
    assert len(res.lines) == 2
    assert res.grand_total_paise == 30300
    assert res.math_reconciled is True

    bad_pdf = await extract_receipt("corrupt.pdf", "application/pdf", b"%PDF-1.4-corrupted-bytes")
    assert bad_pdf.needs_review is True
    assert bad_pdf.lines == []


def test_validation_detects_decimal_anomaly_and_duplicates() -> None:
    """Deterministic validation flags 100x decimal errors and duplicates without mutating."""
    receipt = ExtractedReceipt(
        filename="test.jpg",
        content_type="image/jpeg",
        source_type="image",
        lines=[
            ExtractedReceiptLine(
                raw_text="Phulka 30 15.00 45000.00",
                name="Phulka",
                quantity=30.0,
                unit="pcs",
                unit_price_paise=1500,
                has_explicit_unit_price=True,
                total_paise=4500000,
                confidence=0.9,
            ),
            ExtractedReceiptLine(
                raw_text="Salt 1kg 28.00",
                name="Salt",
                normalized_name="salt",
                quantity=1.0,
                unit="pcs",
                total_paise=2800,
                confidence=0.9,
            ),
            ExtractedReceiptLine(
                raw_text="Salt 1kg 28.00",
                name="Salt",
                normalized_name="salt",
                quantity=1.0,
                unit="pcs",
                total_paise=2800,
                confidence=0.9,
            ),
        ],
        grand_total_paise=50000,
        confidence=0.9,
    )
    out = validate_extracted_receipt(receipt)
    codes = {i.code for i in out.validation_issues}
    assert "DECIMAL_ANOMALY" in codes
    assert "DUPLICATE_LINE_ITEM" in codes
    assert out.needs_review is True
    # Original value preserved, never silently mutated
    assert out.lines[0].total_paise == 4500000


@pytest.mark.asyncio
async def test_cloud_provider_adapters_contract_with_mock_transport() -> None:
    """Verify HF VLM, Azure, and Veryfi adapters via mock HTTP transport (zero paid calls)."""
    vlm_payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "merchant_name": "Santushti Bhojanam",
                            "receipt_number": "2043",
                            "receipt_date": "2026-04-26",
                            "currency": "INR",
                            "subtotal": 2090.0,
                            "cgst": 52.25,
                            "sgst": 52.25,
                            "rounding": 0.5,
                            "grand_total": 2195.0,
                            "items": [
                                {
                                    "raw_text": "Phulka 30 15.00 450.00",
                                    "name": "Phulka",
                                    "quantity": 30,
                                    "unit": "pcs",
                                    "unit_price": 15.0,
                                    "total_price": 450.0,
                                }
                            ],
                        }
                    )
                }
            }
        ]
    }
    vlm_transport = httpx.MockTransport(
        lambda _req: httpx.Response(200, json=vlm_payload)
    )
    vlm = HuggingFaceVlmProvider(token="hf_mock_token")
    res = await vlm.extract_structured(b"fake-png", transport=vlm_transport)
    assert res is not None
    assert res["merchant_name"] == "Santushti Bhojanam"
    assert res["grand_total"] == 2195.0

    # Azure adapter contract
    azure = AzureReceiptProvider()
    azure.endpoint = "https://mock.cognitiveservices.azure.com"
    azure.api_key = "mock-key"
    az_transport = httpx.MockTransport(
        lambda _req: httpx.Response(200, json={"analyzeResult": {"documents": []}})
    )
    az_out = await azure.analyze_receipt(b"png", transport=az_transport)
    assert az_out == {"analyzeResult": {"documents": []}}

    # Veryfi adapter contract
    veryfi = VeryfiReceiptProvider()
    veryfi.client_id = "cid"
    veryfi.username = "user"
    veryfi.api_key = "key"
    vf_transport = httpx.MockTransport(
        lambda _req: httpx.Response(201, json={"id": 123, "total": 2195.0})
    )
    vf_out = await veryfi.process_document(b"png", transport=vf_transport)
    assert vf_out == {"id": 123, "total": 2195.0}


@pytest.mark.asyncio
async def test_missing_ocr_provider_emits_explicit_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no local OCR engine is available, pipeline emits explicit OCR_PROVIDER_UNAVAILABLE."""
    monkeypatch.setattr("app.infra.ocr.pipeline._select_local_ocr_provider", lambda: None)
    img = Image.new("RGB", (500, 700), (245, 245, 245))
    draw = ImageDraw.Draw(img)
    draw.text((30, 30), "TEST RECEIPT 100.00", fill=(20, 20, 20))
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    res = await extract_receipt("test.png", "image/png", buf.getvalue())
    assert res.adapter_used == "ocr_provider_unavailable"
    assert res.needs_review is True
    assert any(i.code == "OCR_PROVIDER_UNAVAILABLE" for i in res.validation_issues)
    assert any("OCR_PROVIDER_UNAVAILABLE" in w for w in res.warnings)

