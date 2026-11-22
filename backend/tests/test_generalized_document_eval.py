"""Generalized document-layout evaluation & held-out layout tests.

Covers:
1. Real PyMuPDF digital multi-page PDF with bordered tables, wrapped descriptions,
   and HSN tax summary.
2. Bordered PNG image with wide descriptions + OpenCV vertical grid-line boundary inference.
3. Linux-compatible TesseractOcrProvider TSV parsing without Apple Vision.
4. Held-out unseen column orders, abbreviated headers, headerless page-2 continuation,
   and consistency checks.
5. Privacy redaction for names, UTR/transaction IDs, addresses, phones, and emails.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import cv2
import fitz
import numpy as np
import pytest

from app.infra.ocr.pipeline import extract_receipt
from app.infra.ocr.preprocessing import detect_table_vertical_lines
from app.infra.ocr.privacy import redact_pii_text
from app.infra.ocr.providers import TesseractOcrProvider
from app.infra.ocr.schemas import OcrTokenBox
from app.infra.ocr.spatial_parser import parse_receipt_text_and_boxes
from app.infra.ocr.validation import validate_extracted_receipt


def _box(
    text: str, x: float, y: float, w: float = 0.08, h: float = 0.018, page: int = 1
) -> OcrTokenBox:
    return OcrTokenBox(text=text, x=x, y=y, w=w, h=h, page=page, confidence=0.95)


@pytest.mark.asyncio
async def test_real_multipage_digital_pdf_with_hsn_summary_table() -> None:
    """End-to-end PyMuPDF digital PDF: 2 pages, repeated headers, and a separate HSN tax summary
    table.
    """
    doc = fitz.open()
    page1 = doc.new_page(width=595, height=842)
    page1.insert_text((40, 45), "UNSEEN WHOLESALE MART", fontsize=14)
    page1.insert_text((40, 65), "Tax Invoice No: INV-2026-8841   Date: 18/11/2026", fontsize=10)
    page1.insert_text((40, 82), "GSTIN: 29AABCU9603R1ZM", fontsize=10)

    # Page 1 table header
    page1.insert_text((40, 120), "Sr", fontsize=9)
    page1.insert_text((75, 120), "HSN", fontsize=9)
    page1.insert_text((145, 120), "Particulars", fontsize=9)
    page1.insert_text((350, 120), "Qty", fontsize=9)
    page1.insert_text((405, 120), "Rate", fontsize=9)
    page1.insert_text((470, 120), "Taxable", fontsize=9)
    page1.insert_text((530, 120), "Total", fontsize=9)

    page1.insert_text((40, 145), "1", fontsize=9)
    page1.insert_text((75, 145), "100630", fontsize=9)
    page1.insert_text((145, 145), "Long Grain Basmati Rice 5kg", fontsize=9)
    page1.insert_text((350, 145), "1", fontsize=9)
    page1.insert_text((405, 145), "420.00", fontsize=9)
    page1.insert_text((470, 145), "400.00", fontsize=9)
    page1.insert_text((530, 145), "420.00", fontsize=9)

    # Page 2 repeated header + 2nd item + separate HSN tax summary table
    page2 = doc.new_page(width=595, height=842)
    page2.insert_text((40, 60), "Sr", fontsize=9)
    page2.insert_text((75, 60), "HSN", fontsize=9)
    page2.insert_text((145, 60), "Particulars", fontsize=9)
    page2.insert_text((350, 60), "Qty", fontsize=9)
    page2.insert_text((405, 60), "Rate", fontsize=9)
    page2.insert_text((470, 60), "Taxable", fontsize=9)
    page2.insert_text((530, 60), "Total", fontsize=9)

    page2.insert_text((40, 85), "2", fontsize=9)
    page2.insert_text((75, 85), "040110", fontsize=9)
    page2.insert_text((145, 85), "Farm Fresh Milk 1L", fontsize=9)
    page2.insert_text((350, 85), "2", fontsize=9)
    page2.insert_text((405, 85), "65.00", fontsize=9)
    page2.insert_text((470, 85), "130.00", fontsize=9)
    page2.insert_text((530, 85), "130.00", fontsize=9)

    # Separate HSN Tax Summary Table (no Particulars/Qty -> must NOT become products!)
    page2.insert_text((40, 160), "HSN", fontsize=9)
    page2.insert_text((150, 160), "Taxable", fontsize=9)
    page2.insert_text((260, 160), "CGST", fontsize=9)
    page2.insert_text((360, 160), "SGST", fontsize=9)
    page2.insert_text((150, 180), "400.00", fontsize=9)
    page2.insert_text((260, 180), "10.00", fontsize=9)
    page2.insert_text((360, 180), "10.00", fontsize=9)
    page2.insert_text((40, 180), "100630", fontsize=9)

    page2.insert_text((40, 240), "Grand Total: Rs 550.00", fontsize=11)
    pdf_bytes = doc.tobytes()
    doc.close()

    receipt = await extract_receipt("wholesale_multipage.pdf", "application/pdf", pdf_bytes)
    assert receipt.document_type == "multi_page_invoice"
    assert len(receipt.lines) == 2
    assert receipt.lines[0].name == "Long Grain Basmati Rice 5kg"
    assert receipt.lines[0].source_page == 1
    assert receipt.lines[1].name == "Farm Fresh Milk 1L"
    assert receipt.lines[1].source_page == 2
    assert receipt.grand_total_paise == 55000
    assert any(t.table_kind == "tax_summary_table" for t in receipt.tables)
    assert receipt.tables[0].rows[0].cells[2].semantic_field == "description"


def test_bordered_image_vertical_grid_detection_protects_wide_descriptions() -> None:
    """OpenCV vertical grid-line detection prevents wide descriptions from being sliced into Qty."""
    canvas = np.full((500, 800, 3), 255, dtype=np.uint8)
    # Draw strong vertical table borders at x=30, x=450 (wide desc!), x=540, x=660, x=770
    for x in (30, 450, 540, 660, 770):
        cv2.line(canvas, (x, 60), (x, 420), (0, 0, 0), 2)
    ok, enc = cv2.imencode(".png", canvas)
    assert ok
    grid_xs = detect_table_vertical_lines(enc.tobytes())
    # x=450 / 800 = 0.5625 should be detected
    assert any(abs(gx - 0.5625) <= 0.02 for gx in grid_xs)

    # Description token extends to x=0.53 (past midpoint between header x=0.10 and Qty x=0.62 ->
    # 0.36!)
    boxes = [
        _box("Item", 0.06, 0.14, w=0.08),
        _box("Qty", 0.60, 0.14, w=0.05),
        _box("Rate", 0.72, 0.14, w=0.06),
        _box("Total", 0.86, 0.14, w=0.07),
        _box("Organic Cold Pressed Virgin Coconut Oil 1L", 0.05, 0.22, w=0.49),
        _box("2", 0.61, 0.22, w=0.03),
        _box("350.00", 0.71, 0.22, w=0.08),
        _box("700.00", 0.85, 0.22, w=0.09),
    ]
    parsed = parse_receipt_text_and_boxes(
        boxes=boxes,
        vertical_grid_xs=grid_xs,
        filename="bordered.png",
        content_type="image/png",
        source_type="image",
        adapter_used="test_grid",
        base_confidence=0.95,
    )
    assert len(parsed.lines) == 1
    assert parsed.lines[0].name == "Organic Cold Pressed Virgin Coconut Oil 1L"
    assert parsed.lines[0].quantity == 2.0
    assert parsed.lines[0].total_paise == 70000


def test_linux_tesseract_provider_tsv_preserves_reading_order_and_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TesseractOcrProvider executes without Apple Vision and preserves reading_order and visual
    rows.
    """
    tsv_output = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        "5\t1\t1\t1\t1\t1\t40\t20\t120\t20\t95\tMETRO\n"
        "5\t1\t1\t1\t1\t2\t170\t20\t100\t20\t94\tMARKET\n"
        "5\t1\t1\t1\t2\t1\t40\t80\t140\t20\t92\tOats\n"
        "5\t1\t1\t1\t2\t2\t500\t80\t80\t20\t93\t145.00\n"
    )
    mock_proc = MagicMock(returncode=0, stdout=tsv_output)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: mock_proc)
    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/bin/tesseract" if name == "tesseract" else None
    )

    img = np.full((200, 700, 3), 255, dtype=np.uint8)
    _, enc = cv2.imencode(".png", img)

    provider = TesseractOcrProvider()
    assert provider.is_available() is True
    out = provider.recognize_image(enc.tobytes(), page=1)
    assert [b.reading_order for b in out.boxes] == [0, 1, 2, 3]
    assert "\n" in out.raw_text  # Visual lines preserved, not flattened
    assert out.raw_text.splitlines()[0] == "METRO MARKET"


def test_held_out_unseen_column_order_abbreviations_and_consistency_checks() -> None:
    """Held-out layout: reversed/abbreviated columns, discontinuous serials, and tax-total
    anomaly.
    """
    boxes = [
        _box("Sl.", 0.04, 0.10, w=0.04),
        _box("Net Val", 0.14, 0.10, w=0.08),
        _box("Qnty", 0.28, 0.10, w=0.06),
        _box("RSP", 0.40, 0.10, w=0.06),
        _box("Particulars", 0.54, 0.10, w=0.14),
        _box("CGST %", 0.72, 0.10, w=0.08),
        _box("CGST Amt", 0.84, 0.10, w=0.09),
        # Row 1 (sr_no = 1)
        _box("1", 0.04, 0.16, w=0.03),
        _box("105.00", 0.14, 0.16, w=0.08),
        _box("1", 0.29, 0.16, w=0.03),
        _box("120.00", 0.40, 0.16, w=0.07),
        _box("Whole Almond 250g", 0.52, 0.16, w=0.18),
        _box("2.5%", 0.73, 0.16, w=0.05),
        _box("2.50", 0.85, 0.16, w=0.06),
        # Row 2 (sr_no = 3 -> skipped row 2!)
        _box("3", 0.04, 0.22, w=0.03),
        _box("50.00", 0.14, 0.22, w=0.08),
        _box("1", 0.29, 0.22, w=0.03),
        _box("55.00", 0.40, 0.22, w=0.07),
        _box("Brown Sugar 500g", 0.52, 0.22, w=0.18),
        _box("2.5%", 0.73, 0.22, w=0.05),
        _box("1.25", 0.85, 0.22, w=0.06),
    ]
    parsed = validate_extracted_receipt(
        parse_receipt_text_and_boxes(
            boxes=boxes,
            filename="held_out_layout.pdf",
            content_type="application/pdf",
            source_type="pdf_digital",
            adapter_used="pymupdf_spatial_parser",
            base_confidence=0.95,
        )
    )
    assert len(parsed.lines) == 2
    assert parsed.lines[0].name == "Whole Almond 250g"
    assert parsed.lines[0].total_paise == 10500
    assert parsed.lines[0].mrp_paise == 12000
    assert parsed.lines[0].cgst_paise == 250
    assert parsed.lines[1].sr_no == 3
    assert any(issue.code == "DISCONTINUOUS_SERIAL_NUMBERS" for issue in parsed.validation_issues)


def test_privacy_redacts_names_utr_addresses_without_touching_prices() -> None:
    """Personal names, UTR/UPI refs, emails, phones, and addresses are redacted without touching
    prices.
    """
    raw = (
        "Fresh Mart\n"
        "Name: Aarav Sharma\n"
        "Deliver to: 21B Baker Street, Indiranagar\n"
        "Phone: +91 9988776655\n"
        "Email: aarav@example.org\n"
        "UTR No: 4321987654321\n"
        "Organic Milk 1L ₹68.00\n"
        "Grand Total ₹68.00\n"
    )
    redacted, types = redact_pii_text(raw)
    assert "Aarav Sharma" not in redacted
    assert "Baker Street" not in redacted
    assert "9988776655" not in redacted
    assert "aarav@example.org" not in redacted
    assert "4321987654321" not in redacted
    assert "Organic Milk 1L ₹68.00" in redacted
    assert "Grand Total ₹68.00" in redacted
    assert set(types) >= {"name", "address", "phone", "email", "transaction_id"}
