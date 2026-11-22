"""Regression tests for cross-column mapping, 3-row headers, vertically-centered
multi-line descriptions, and separate summary table isolation."""

from __future__ import annotations

import re

import pytest
from httpx import AsyncClient

from app.infra.ocr.schemas import OcrTokenBox
from app.infra.ocr.spatial_parser import parse_receipt_text_and_boxes
from app.infra.ocr.validation import validate_extracted_receipt
from tests.conftest import EphemeralJWKSProvider


def _b(
    text: str, x: float, y: float, w: float = 0.06, h: float = 0.014, page: int = 1
) -> OcrTokenBox:
    return OcrTokenBox(text=text, x=x, y=y, w=w, h=h, page=page, confidence=0.94)


def test_dense_14col_gst_invoice_3row_header_no_cross_column_contamination() -> None:
    """14-column GST invoice with 3-row header
    ('Unit Price*', 'Unit Taxable Value*', 'CGST Rate(%) Amount')
    and wrapped descriptions above/below anchors must map every column cleanly without bleeding."""
    boxes: list[OcrTokenBox] = [
        # 3-row multi-column header
        _b("CGST", 0.72, 0.08, w=0.05),
        _b("SGST/", 0.79, 0.08, w=0.05),
        _b("SI", 0.02, 0.10, w=0.03),
        _b("Item Description", 0.07, 0.10, w=0.12),
        _b("HSN", 0.21, 0.10, w=0.04),
        _b("Quantity", 0.27, 0.10, w=0.05),
        _b("Unit", 0.33, 0.10, w=0.04),
        _b("Unit", 0.39, 0.10, w=0.04),
        _b("Gross", 0.45, 0.10, w=0.05),
        _b("Discount/", 0.51, 0.10, w=0.06),
        _b("Other", 0.58, 0.10, w=0.05),
        _b("Taxable", 0.64, 0.10, w=0.06),
        _b("Rate(%)", 0.71, 0.10, w=0.05),
        _b("UTGST", 0.78, 0.10, w=0.05),
        _b("CESS", 0.85, 0.10, w=0.04),
        _b("TOTAL", 0.91, 0.10, w=0.06),
        _b("No.", 0.02, 0.12, w=0.03),
        _b("Code", 0.21, 0.12, w=0.04),
        _b("Price*", 0.33, 0.12, w=0.04),
        _b("Taxable", 0.39, 0.12, w=0.05),
        _b("Value*", 0.45, 0.12, w=0.05),
        _b("Margin*", 0.51, 0.12, w=0.05),
        _b("Charges**", 0.58, 0.12, w=0.06),
        _b("Value", 0.64, 0.12, w=0.05),
        _b("Amount", 0.71, 0.12, w=0.05),
        _b("Rate(%)", 0.78, 0.12, w=0.05),
        _b("Value", 0.85, 0.12, w=0.04),
        # Item 1: leading line above anchor, anchor, trailing line + 7 Days Return
        _b("Fortune Soya Health", 0.07, 0.16, w=0.13),
        _b("1", 0.02, 0.19, w=0.02),
        _b("Refined Soyabean Oil 1 L", 0.07, 0.19, w=0.13),
        _b("15079010", 0.21, 0.19, w=0.05),
        _b("2", 0.28, 0.19, w=0.02),
        _b("200.00", 0.33, 0.19, w=0.05),
        _b("144.53", 0.39, 0.19, w=0.05),
        _b("402.74", 0.45, 0.19, w=0.05),
        _b("99.22", 0.51, 0.19, w=0.05),
        _b("2.74", 0.58, 0.19, w=0.04),
        _b("289.06", 0.64, 0.19, w=0.05),
        _b("2.50%", 0.71, 0.19, w=0.04),
        _b("2.50%", 0.78, 0.19, w=0.04),
        _b("0.00", 0.85, 0.19, w=0.04),
        _b("303.52", 0.91, 0.19, w=0.05),
        _b("Pouch", 0.07, 0.22, w=0.06),
        _b("7.23", 0.71, 0.22, w=0.04),
        _b("7.23", 0.78, 0.22, w=0.04),
        _b("7 Days Return", 0.07, 0.24, w=0.09),
        # Item 2
        _b("bb Popular Tamarind/Imli -", 0.07, 0.28, w=0.13),
        _b("2", 0.02, 0.31, w=0.02),
        _b("Seedless 500 g", 0.07, 0.31, w=0.10),
        _b("08134010", 0.21, 0.31, w=0.05),
        _b("1", 0.28, 0.31, w=0.02),
        _b("220.00", 0.33, 0.31, w=0.05),
        _b("111.00", 0.39, 0.31, w=0.05),
        _b("221.00", 0.45, 0.31, w=0.05),
        _b("110.00", 0.51, 0.31, w=0.05),
        _b("1.00", 0.58, 0.31, w=0.04),
        _b("111.00", 0.64, 0.31, w=0.05),
        _b("0.00%", 0.71, 0.31, w=0.04),
        _b("0.00%", 0.78, 0.31, w=0.04),
        _b("0.00", 0.85, 0.31, w=0.04),
        _b("111.00", 0.91, 0.31, w=0.05),
        _b("7 Days Return", 0.07, 0.34, w=0.09),
        # Footer & separate GST Information summary
        _b("Sub Total", 0.72, 0.40, w=0.08),
        _b("Rs.414.52", 0.90, 0.40, w=0.07),
        _b("GST Information", 0.04, 0.45, w=0.12),
        _b("HSN", 0.04, 0.48, w=0.04),
        _b("Taxable", 0.20, 0.48, w=0.06),
        _b("CGST", 0.35, 0.48, w=0.05),
        _b("SGST", 0.48, 0.48, w=0.05),
        _b("15079010", 0.04, 0.51, w=0.05),
        _b("289.06", 0.20, 0.51, w=0.05),
        _b("7.23", 0.35, 0.51, w=0.04),
        _b("7.23", 0.48, 0.51, w=0.04),
    ]

    parsed = validate_extracted_receipt(
        parse_receipt_text_and_boxes(
            boxes=boxes,
            filename="dense_14col.jpeg",
            content_type="image/jpeg",
            source_type="image",
            adapter_used="apple_vision_ocrmac",
            base_confidence=0.94,
        )
    )

    assert len(parsed.lines) == 2
    assert parsed.merchant_name != "Fortune Soya Health"

    l1, l2 = parsed.lines
    assert l1.sr_no == 1
    assert l1.hsn_code == "15079010"
    assert l1.quantity == 2.0
    assert l1.unit_price_paise == 20000
    assert l1.unit_taxable_paise == 14453
    assert l1.gross_paise == 40274
    assert l1.discount_paise == 9922
    assert l1.other_charges_paise == 274
    assert l1.taxable_paise == 28906
    assert l1.cgst_paise == 723
    assert l1.sgst_paise == 723
    assert l1.total_paise == 30352
    assert "Fortune Soya Health" in l1.name
    assert "Pouch" in l1.name
    assert "15079010" not in l1.name
    assert "200.00" not in l1.name
    assert "7 Days Return" not in l1.name

    assert l2.sr_no == 2
    assert l2.hsn_code == "08134010"
    assert l2.unit_price_paise == 22000
    assert l2.total_paise == 11100
    assert "Tamarind" in l2.name
    assert "500 g" in l2.name


def test_vertically_centered_multiline_descriptions_and_mrp_vs_rate_vs_total() -> None:
    """3-row header with 'Unit MRP/RSP', 'Product Rate', 'Taxable Amt.', 'Total Amt.' and
    vertically-centered multi-line product cards must not bleed across rows or set
    merchant='Amt.'."""
    boxes: list[OcrTokenBox] = [
        _b("Seller Name: Fresh Supply Co", 0.04, 0.04, w=0.25),
        # 3-row header
        _b("SR", 0.02, 0.10, w=0.03),
        _b("Item &", 0.06, 0.10, w=0.10),
        _b("Unit", 0.22, 0.10, w=0.06),
        _b("HSN", 0.30, 0.10, w=0.05),
        _b("Qty", 0.37, 0.10, w=0.04),
        _b("Product", 0.42, 0.10, w=0.06),
        _b("Disc.", 0.49, 0.10, w=0.05),
        _b("Taxable", 0.55, 0.10, w=0.06),
        _b("CGST", 0.62, 0.10, w=0.05),
        _b("S/UT", 0.68, 0.10, w=0.05),
        _b("CGST", 0.74, 0.10, w=0.05),
        _b("S/UT", 0.80, 0.10, w=0.05),
        _b("Cess", 0.86, 0.10, w=0.04),
        _b("Total", 0.92, 0.10, w=0.05),
        _b("No", 0.02, 0.12, w=0.03),
        _b("Description", 0.06, 0.12, w=0.10),
        _b("MRP/RSP", 0.22, 0.12, w=0.06),
        _b("Rate", 0.42, 0.12, w=0.05),
        _b("Amt.", 0.55, 0.12, w=0.05),
        _b("GST", 0.68, 0.12, w=0.05),
        _b("Amt.", 0.74, 0.12, w=0.05),
        _b("GST", 0.80, 0.12, w=0.05),
        _b("Amt.", 0.92, 0.12, w=0.05),
        _b("Amt.", 0.80, 0.14, w=0.05),
        # Item 1
        _b("Dabur", 0.06, 0.18, w=0.06),
        _b("1", 0.02, 0.20, w=0.02),
        _b("Almond Hair", 0.06, 0.20, w=0.10),
        _b("250.00", 0.22, 0.20, w=0.05),
        _b("33059011", 0.30, 0.20, w=0.06),
        _b("1", 0.37, 0.20, w=0.02),
        _b("238.10", 0.42, 0.20, w=0.05),
        _b("38.00%", 0.49, 0.20, w=0.05),
        _b("147.62", 0.55, 0.20, w=0.05),
        _b("2.50%", 0.62, 0.20, w=0.04),
        _b("2.50%", 0.68, 0.20, w=0.04),
        _b("3.69", 0.74, 0.20, w=0.04),
        _b("3.69", 0.80, 0.20, w=0.04),
        _b("0.00", 0.86, 0.20, w=0.04),
        _b("155.00", 0.92, 0.20, w=0.05),
        _b("Oil 1 pc (290 ml)", 0.06, 0.22, w=0.12),
        # Clear vertical row gap before Item 2 (0.22 -> 0.28 = 0.06 vs 0.02 intra-row)
        _b("Get-A-Way", 0.06, 0.28, w=0.08),
        _b("Belgian", 0.06, 0.30, w=0.07),
        _b("Choco Chunk", 0.06, 0.32, w=0.10),
        _b("2", 0.02, 0.34, w=0.02),
        _b("High Protein", 0.06, 0.34, w=0.10),
        _b("100.00", 0.22, 0.34, w=0.05),
        _b("21050000", 0.30, 0.34, w=0.06),
        _b("1", 0.37, 0.34, w=0.02),
        _b("95.24", 0.42, 0.34, w=0.05),
        _b("50.00%", 0.49, 0.34, w=0.05),
        _b("47.62", 0.55, 0.34, w=0.05),
        _b("2.50%", 0.62, 0.34, w=0.04),
        _b("2.50%", 0.68, 0.34, w=0.04),
        _b("1.19", 0.74, 0.34, w=0.04),
        _b("1.19", 0.80, 0.34, w=0.04),
        _b("0.00", 0.86, 0.34, w=0.04),
        _b("50.00", 0.92, 0.34, w=0.05),
        _b("Ice Cream Cup", 0.06, 0.36, w=0.11),
        _b("1 pack (100 ml)", 0.06, 0.38, w=0.11),
        _b("Grand Total", 0.70, 0.44, w=0.10),
        _b("₹205.00", 0.90, 0.44, w=0.07),
    ]

    parsed = validate_extracted_receipt(
        parse_receipt_text_and_boxes(
            boxes=boxes,
            filename="zepto_gst.jpg",
            content_type="image/jpeg",
            source_type="image",
            adapter_used="apple_vision_ocrmac",
            base_confidence=0.94,
        )
    )

    assert parsed.merchant_name == "Fresh Supply Co"
    assert len(parsed.tables) == 1
    assert parsed.tables[0].table_kind == "item_table"
    assert len(parsed.lines) == 2

    l1, l2 = parsed.lines
    assert l1.name == "Dabur Almond Hair Oil 1 pc (290 ml)"
    assert l1.mrp_paise == 25000
    assert l1.unit_price_paise == 23810
    assert l1.taxable_paise == 14762
    assert l1.cgst_paise == 369
    assert l1.sgst_paise == 369
    assert l1.total_paise == 15500

    assert l2.name == "Get-A-Way Belgian Choco Chunk High Protein Ice Cream Cup 1 pack (100 ml)"
    assert l2.mrp_paise == 10000
    assert l2.unit_price_paise == 9524
    assert l2.taxable_paise == 4762
    assert l2.total_paise == 5000
    assert "Get-A-Way" not in l1.name
    assert "Dabur" not in l2.name


def test_missing_cells_do_not_shift_subsequent_values() -> None:
    """Missing HSN or missing Discount cell in a row must not shift subsequent column values
    left.
    """
    boxes = [
        _b("Item", 0.05, 0.10, w=0.10),
        _b("HSN", 0.28, 0.10, w=0.06),
        _b("Qty", 0.40, 0.10, w=0.05),
        _b("Rate", 0.52, 0.10, w=0.06),
        _b("Discount", 0.64, 0.10, w=0.08),
        _b("Total", 0.80, 0.10, w=0.08),
        # Row 1: missing Discount
        _b("Organic Oats 500g", 0.05, 0.16, w=0.16),
        _b("11041200", 0.28, 0.16, w=0.07),
        _b("2", 0.41, 0.16, w=0.03),
        _b("120.00", 0.52, 0.16, w=0.06),
        _b("240.00", 0.80, 0.16, w=0.07),
        # Row 2: missing HSN
        _b("Green Tea 100g", 0.05, 0.22, w=0.14),
        _b("1", 0.41, 0.22, w=0.03),
        _b("150.00", 0.52, 0.22, w=0.06),
        _b("15.00", 0.65, 0.22, w=0.06),
        _b("135.00", 0.80, 0.22, w=0.07),
    ]
    parsed = parse_receipt_text_and_boxes(
        boxes=boxes,
        filename="missing_cells.pdf",
        content_type="application/pdf",
        source_type="pdf_digital",
        adapter_used="pymupdf_spatial_parser",
        base_confidence=0.95,
    )
    assert len(parsed.lines) == 2
    assert parsed.lines[0].hsn_code == "11041200"
    assert parsed.lines[0].discount_paise is None
    assert parsed.lines[0].total_paise == 24000
    assert parsed.lines[1].hsn_code is None
    assert parsed.lines[1].quantity == 1.0
    assert parsed.lines[1].unit_price_paise == 15000
    assert parsed.lines[1].discount_paise == 1500
    assert parsed.lines[1].total_paise == 13500


@pytest.mark.asyncio
async def test_api_purchase_draft_contains_clean_names_and_correct_financials(
    client: AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    """API PurchaseOut draft response preserves clean display_name without raw OCR numeric dumps."""
    token = jwks_provider.make_token(
        sub="user_clean_draft", email="clean@example.com", name="Clean"
    )
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 200

    # Upload a clean digital PDF generated via PyMuPDF
    import fitz

    doc = fitz.open()
    p = doc.new_page(width=595, height=420)
    p.insert_text((40, 40), "METRO GROCERY", fontsize=13)
    p.insert_text((40, 80), "Sr", fontsize=9)
    p.insert_text((80, 80), "HSN", fontsize=9)
    p.insert_text((150, 80), "Item Description", fontsize=9)
    p.insert_text((360, 80), "Qty", fontsize=9)
    p.insert_text((420, 80), "Unit Price", fontsize=9)
    p.insert_text((500, 80), "Total", fontsize=9)

    p.insert_text((40, 105), "1", fontsize=9)
    p.insert_text((80, 105), "10063020", fontsize=9)
    p.insert_text((150, 105), "Basmati Rice 1kg", fontsize=9)
    p.insert_text((360, 105), "2", fontsize=9)
    p.insert_text((420, 105), "110.00", fontsize=9)
    p.insert_text((500, 105), "220.00", fontsize=9)
    p.insert_text((420, 150), "Grand Total: Rs 220.00", fontsize=10)
    pdf_bytes = doc.tobytes()
    doc.close()

    resp = await client.post(
        "/api/v1/purchases/ocr",
        headers=headers,
        files={"file": ("clean_invoice.pdf", pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 201
    draft = resp.json()
    assert draft["merchant_name"] == "METRO GROCERY"
    assert draft["total_amount_paise"] == 22000
    assert len(draft["items"]) == 1
    item = draft["items"][0]
    assert item["display_name"] == "Basmati Rice 1kg"
    assert not re.search(r"10063020|110\.00|220\.00", item["display_name"])
    assert item["unit_price_paise"] == 11000
    assert item["line_total_paise"] == 22000


def test_cropped_table_merchant_uncertainty_payment_isolation_and_decimal_anomaly() -> None:
    """Cropped invoice table without seller header leaves merchant_name=None,
    isolates PAY lines from SGST, strips OCR return-policy typos ('7 Days Retum'),
    and surfaces DECIMAL_ANOMALY in receipt.warnings."""
    boxes = [
        _b("SI", 0.02, 0.04, w=0.03),
        _b("Item Description", 0.07, 0.04, w=0.12),
        _b("HSN", 0.22, 0.04, w=0.05),
        _b("Quantity", 0.29, 0.04, w=0.06),
        _b("Unit Price*", 0.37, 0.04, w=0.06),
        _b("Taxable Value", 0.55, 0.04, w=0.07),
        _b("CGST", 0.65, 0.04, w=0.05),
        _b("SGST/", 0.72, 0.04, w=0.05),
        _b("TOTAL", 0.85, 0.04, w=0.06),
        # Product 1 with '7 Days Retum' OCR typo
        _b("Brand A", 0.07, 0.09, w=0.10),
        _b("1", 0.02, 0.11, w=0.02),
        _b("Refined Oil 1 L", 0.07, 0.11, w=0.11),
        _b("15079010", 0.22, 0.11, w=0.05),
        _b("2", 0.30, 0.11, w=0.02),
        _b("200.00", 0.37, 0.11, w=0.05),
        _b("289.06", 0.55, 0.11, w=0.05),
        _b("7.23", 0.65, 0.11, w=0.04),
        _b("7.23", 0.72, 0.11, w=0.04),
        _b("303.52", 0.85, 0.11, w=0.05),
        _b("7 Days Retum", 0.07, 0.13, w=0.09),
        # Product 2 with 100x decimal-drop OCR error ('18164' vs taxable 173.00)
        _b("2", 0.02, 0.18, w=0.02),
        _b("Dark Chocolate 150 g", 0.07, 0.18, w=0.12),
        _b("18069010", 0.22, 0.18, w=0.05),
        _b("1", 0.30, 0.18, w=0.02),
        _b("180.00", 0.37, 0.18, w=0.05),
        _b("173.00", 0.55, 0.18, w=0.05),
        _b("4.32", 0.65, 0.18, w=0.04),
        _b("4.32", 0.72, 0.18, w=0.04),
        _b("18164", 0.85, 0.18, w=0.05),
        # GST Information + Transaction PAY line that previously polluted SGST
        _b("Sub Total", 0.55, 0.25, w=0.08),
        _b("Rs.485.16", 0.85, 0.25, w=0.07),
        _b("GST Information", 0.04, 0.30, w=0.12),
        _b("CGST% Sale Taxable Value Tax Value", 0.04, 0.33, w=0.35),
        _b("106a3988d3600a60c31b PAY Payment on 2026-11-16 Rs.467.18", 0.45, 0.36, w=0.45),
        _b("SGST% Sale Taxable Value Tax Value", 0.04, 0.39, w=0.35),
        _b("Final Total Rs.467.18", 0.45, 0.42, w=0.25),
    ]

    parsed = validate_extracted_receipt(
        parse_receipt_text_and_boxes(
            boxes=boxes,
            filename="cropped_gst.jpeg",
            content_type="image/jpeg",
            source_type="image",
            adapter_used="apple_vision_ocrmac",
            base_confidence=0.92,
        )
    )

    assert parsed.merchant_name is None
    assert len(parsed.lines) == 2
    assert "7 Days Retum" not in parsed.lines[0].name
    assert parsed.tax_breakdown.sgst_paise == 1155  # 723 + 432, NEVER 46718!
    assert parsed.grand_total_paise == 46718
    assert any(i.code == "DECIMAL_ANOMALY" for i in parsed.validation_issues)
    assert any("decimal-drop" in w.lower() for w in parsed.warnings)


def test_ocr_corrupted_item_header_still_classified_as_item_table() -> None:
    """Header with OCR typos ('tem Descripton', 'Guant') must still be classified as item_table."""
    boxes = [
        _b("tem Descripton", 0.05, 0.10, w=0.16),
        _b("HSN Code", 0.26, 0.10, w=0.07),
        _b("Guant", 0.36, 0.10, w=0.05),
        _b("Unit Price*", 0.44, 0.10, w=0.07),
        _b("Taxable Value", 0.55, 0.10, w=0.08),
        _b("CGST", 0.67, 0.10, w=0.05),
        _b("SGST", 0.74, 0.10, w=0.05),
        _b("TOTAL", 0.85, 0.10, w=0.06),
        _b("Iodized Salt 1 kg", 0.05, 0.16, w=0.15),
        _b("25010090", 0.26, 0.16, w=0.06),
        _b("1", 0.37, 0.16, w=0.02),
        _b("28.00", 0.44, 0.16, w=0.05),
        _b("26.30", 0.55, 0.16, w=0.05),
        _b("0.00", 0.67, 0.16, w=0.04),
        _b("0.00", 0.74, 0.16, w=0.04),
        _b("26.30", 0.85, 0.16, w=0.05),
    ]
    parsed = parse_receipt_text_and_boxes(
        boxes=boxes,
        filename="ocr_typo_header.jpg",
        content_type="image/jpeg",
        source_type="image",
        adapter_used="apple_vision_ocrmac",
        base_confidence=0.90,
    )
    assert len(parsed.tables) == 1
    assert parsed.tables[0].table_kind == "item_table"
    assert len(parsed.lines) == 1
    assert parsed.lines[0].name == "Iodized Salt 1 kg"
    assert parsed.lines[0].total_paise == 2630
