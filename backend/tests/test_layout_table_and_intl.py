"""Regression tests for layout-aware table extraction, document classification,
international formats, PII redaction, and multi-document draft merging."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.infra.ocr.schemas import OcrTokenBox, normalize_metric_unit, parse_inr_to_paise
from app.infra.ocr.spatial_parser import parse_receipt_text_and_boxes
from app.infra.ocr.validation import validate_extracted_receipt
from tests.conftest import EphemeralJWKSProvider


def _make_row_boxes(
    y: float,
    cols: list[tuple[float, str]],
    *,
    page: int = 1,
    h: float = 0.018,
) -> list[OcrTokenBox]:
    return [
        OcrTokenBox(text=txt, x=x, y=y, w=0.08, h=h, page=page, confidence=0.95)
        for x, txt in cols
    ]


def test_fixture_a_bill_summary_no_fabricated_items() -> None:
    """Fixture A (synthetic transcript): digital order bill summary must not invent items."""
    raw = """
    Swiggy Instamart
    Order ID: ORD31485518163
    Bill Summary
    Item Total ₹285.00
    Handling Fee ₹10.00
    Delivery Partner Fee ₹25.00
    Discount -₹20.00
    Grand Total ₹300.00
    Paid via UPI
    """
    parsed = validate_extracted_receipt(
        parse_receipt_text_and_boxes(
            raw_text=raw,
            filename="fixture_a_summary.png",
            content_type="image/png",
            source_type="image",
            adapter_used="deterministic_test",
            base_confidence=0.95,
        )
    )
    assert parsed.document_type == "bill_summary"
    assert parsed.order_id == "ORD31485518163"
    assert parsed.grand_total_paise == 30000
    assert parsed.lines == []
    assert "line_items" in parsed.missing_fields


def test_fixture_b_dense_gst_table_with_goodwill_and_pii() -> None:
    """Fixture B (synthetic box layout): 8-product dense GST invoice + goodwill + PII redaction."""
    boxes: list[OcrTokenBox] = []
    # Header + PII
    boxes.extend(_make_row_boxes(0.03, [(0.05, "Tax Invoice"), (0.55, "GSTIN: 29ABCDE1234F1Z5")]))
    boxes.extend(_make_row_boxes(0.06, [(0.05, "Customer: customer.test@example.com"), (0.55, "Phone: +91 9876543210")]))
    boxes.extend(_make_row_boxes(0.09, [(0.05, "Delivery Address: 42 MG Road, Indiranagar, Bengaluru 560038")]))

    # Table header (10 semantic columns)
    boxes.extend(
        _make_row_boxes(
            0.14,
            [
                (0.02, "Sr"),
                (0.07, "HSN"),
                (0.16, "Item Description"),
                (0.38, "Qty"),
                (0.44, "MRP"),
                (0.51, "Rate"),
                (0.58, "Taxable"),
                (0.67, "CGST"),
                (0.76, "SGST"),
                (0.86, "Total"),
            ],
        )
    )

    items = [
        ("1", "04011000", "Amul Taaza Toned Milk 1L", "1", "72.00", "68.00", "68.00", "0.00", "0.00", "68.00"),
        ("2", "10063020", "India Gate Basmati Rice 1kg", "1", "110.00", "90.00", "85.71", "2.14", "2.15", "90.00"),
        ("3", "07134000", "Tata Sampann Toor Dal 500g", "1", "85.00", "70.00", "66.67", "1.66", "1.67", "70.00"),
        ("4", "15091000", "Fortune Sunflower Oil 500ml", "1", "75.00", "65.00", "61.90", "1.55", "1.55", "65.00"),
        ("5", "09024020", "Red Label Tea 100g", "1", "55.00", "45.00", "42.86", "1.07", "1.07", "45.00"),
        ("6", "25010010", "Tata Salt Iodized 1kg", "1", "28.00", "22.00", "22.00", "0.00", "0.00", "22.00"),
        ("7", "19053100", "Britannia Good Day 100g", "1", "15.00", "11.00", "9.32", "0.84", "0.84", "11.00"),
        ("8", "21069099", "Maggi Masala Noodles 70g", "1", "12.00", "10.00", "8.48", "0.76", "0.76", "10.00"),
    ]
    y = 0.18
    for row in items:
        boxes.extend(
            _make_row_boxes(
                y,
                [
                    (0.02, row[0]),
                    (0.07, row[1]),
                    (0.16, row[2]),
                    (0.38, row[3]),
                    (0.44, row[4]),
                    (0.51, row[5]),
                    (0.58, row[6]),
                    (0.67, row[7]),
                    (0.76, row[8]),
                    (0.86, row[9]),
                ],
            )
        )
        y += 0.04

    boxes.extend(_make_row_boxes(0.54, [(0.55, "Goodwill adjustment"), (0.86, "-1.00")]))
    boxes.extend(_make_row_boxes(0.58, [(0.55, "Grand Total"), (0.86, "₹380.00")]))

    parsed = validate_extracted_receipt(
        parse_receipt_text_and_boxes(
            raw_text="",
            boxes=boxes,
            filename="fixture_b_gst.pdf",
            content_type="application/pdf",
            source_type="pdf_digital",
            adapter_used="pymupdf_spatial_parser",
            base_confidence=0.95,
        )
    )

    assert parsed.document_type == "gst_invoice"
    assert len(parsed.lines) == 8
    assert parsed.lines[0].hsn_code == "04011000"
    assert parsed.lines[0].mrp_paise == 7200
    assert parsed.lines[0].source_page == 1
    assert parsed.lines[0].source_bbox is not None
    assert parsed.lines[0].source_evidence is not None
    assert parsed.goodwill_paise == -100
    assert parsed.grand_total_paise == 38000
    assert parsed.math_reconciled is True

    # PII redaction checks
    assert "customer.test@example.com" not in parsed.raw_text
    assert "9876543210" not in parsed.raw_text
    assert "Indiranagar" not in parsed.raw_text
    assert set(parsed.redacted_pii_types) >= {"email", "phone", "address"}


def test_fixture_c_bigbasket_loyalty_and_handling() -> None:
    """Fixture C (synthetic box layout): 6-product BigBasket table + NeucCoins + handling."""
    boxes: list[OcrTokenBox] = []
    boxes.extend(_make_row_boxes(0.04, [(0.05, "BigBasket"), (0.55, "Invoice No: BB-99881")]))
    boxes.extend(
        _make_row_boxes(
            0.10,
            [
                (0.03, "Item Description"),
                (0.45, "Qty"),
                (0.58, "Rate"),
                (0.75, "Amount"),
            ],
        )
    )
    rows = [
        ("Fresho Onion 1kg", "2", "40.00", "80.00"),
        ("Fresho Tomato Hybrid 1kg", "1", "45.00", "45.00"),
        ("Nandini GoodLife Milk 1L", "3", "62.00", "186.00"),
        ("Aashirvaad Shudh Chakki Atta 5kg", "1", "265.00", "265.00"),
        ("Amul Butter Pasteurised 500g", "1", "285.00", "285.00"),
        ("bb Royal Curry Leaves 100g", "1", "20.00", "20.00"),
    ]
    y = 0.15
    for name, qty, rate, amt in rows:
        boxes.extend(_make_row_boxes(y, [(0.03, name), (0.45, qty), (0.58, rate), (0.75, amt)]))
        y += 0.04

    boxes.extend(_make_row_boxes(0.44, [(0.45, "Handling Charges"), (0.75, "15.00")]))
    boxes.extend(_make_row_boxes(0.48, [(0.45, "Redeemed NeucCoins"), (0.75, "-28.20")]))
    boxes.extend(_make_row_boxes(0.52, [(0.45, "Grand Total"), (0.75, "₹867.80")]))

    parsed = validate_extracted_receipt(
        parse_receipt_text_and_boxes(
            raw_text="",
            boxes=boxes,
            filename="fixture_c_bb.pdf",
            content_type="application/pdf",
            source_type="pdf_digital",
            adapter_used="pymupdf_spatial_parser",
            base_confidence=0.95,
        )
    )

    assert len(parsed.lines) == 6
    assert parsed.handling_charges_paise == 1500
    assert parsed.loyalty_discount_paise == 2820
    assert parsed.grand_total_paise == 86780
    assert parsed.math_reconciled is True


def test_wrapped_description_multipage_and_multiline_tax_cells() -> None:
    """Multi-page tables, wrapped descriptions, and rate%/amt tax cells parse cleanly."""
    boxes: list[OcrTokenBox] = []
    # Page 1
    boxes.extend(
        _make_row_boxes(
            0.10,
            [(0.05, "Item"), (0.45, "Qty"), (0.60, "CGST"), (0.78, "Total")],
            page=1,
        )
    )
    boxes.extend(
        _make_row_boxes(
            0.15,
            [(0.05, "Amul Gold Full Cream Milk"), (0.45, "2"), (0.60, "2.5%"), (0.78, "132.00")],
            page=1,
        )
    )
    # Wrapped description + multi-line CGST amount on next row
    boxes.extend(
        _make_row_boxes(
            0.18,
            [(0.05, "(Tetrapack) 1 L"), (0.60, "3.14")],
            page=1,
        )
    )
    # Page 2
    boxes.extend(
        _make_row_boxes(
            0.10,
            [(0.05, "Item"), (0.45, "Qty"), (0.60, "CGST"), (0.78, "Total")],
            page=2,
        )
    )
    boxes.extend(
        _make_row_boxes(
            0.15,
            [(0.05, "Tata Salt 1 kg"), (0.45, "1"), (0.60, "0.00"), (0.78, "28.00")],
            page=2,
        )
    )
    boxes.extend(_make_row_boxes(0.25, [(0.55, "Grand Total"), (0.78, "₹160.00")], page=2))

    parsed = validate_extracted_receipt(
        parse_receipt_text_and_boxes(
            raw_text="",
            boxes=boxes,
            filename="multipage.pdf",
            content_type="application/pdf",
            source_type="pdf_digital",
            adapter_used="pymupdf_spatial_parser",
            base_confidence=0.95,
        )
    )
    assert parsed.document_type == "multi_page_invoice"
    assert len(parsed.lines) == 2
    assert "Tetrapack" in parsed.lines[0].name
    assert parsed.lines[0].cgst_paise == 314
    assert parsed.lines[0].pack_size == "1 L"
    assert parsed.lines[1].source_page == 2
    assert parsed.grand_total_paise == 16000


def test_international_currency_european_decimals_and_imperial_units() -> None:
    """European comma decimals, EUR currency, and lb/oz imperial units convert deterministically."""
    assert parse_inr_to_paise("1.234,56 €") == 123456
    assert parse_inr_to_paise("12,50 EUR") == 1250
    assert parse_inr_to_paise("AED 45.75") == 4575

    lb_qty, lb_unit = normalize_metric_unit(2.0, "lb")
    assert lb_unit == "g"
    assert lb_qty == 907.184

    oz_qty, oz_unit = normalize_metric_unit(8.0, "oz")
    assert oz_unit == "g"
    assert oz_qty == 226.796

    raw = """
    Carrefour Market
    Invoice No: EU-2026-11
    Olive Oil 500ml 12,50 €
    Organic Apples 2 lb 5,40 €
    Total 17,90 €
    """
    parsed = parse_receipt_text_and_boxes(
        raw_text=raw,
        filename="carrefour.txt",
        content_type="text/plain",
        source_type="text",
        adapter_used="deterministic_text_parser",
        base_confidence=0.95,
    )
    assert parsed.currency == "EUR"
    assert parsed.grand_total_paise == 1790
    assert len(parsed.lines) == 2
    assert parsed.lines[0].total_paise == 1250
    assert parsed.lines[1].total_paise == 540


@pytest.mark.asyncio
async def test_multi_document_attach_and_no_inventory_mutation_before_confirm(
    client: AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    """Attaching itemized invoice to a bill_summary draft merges items cleanly without mutating inventory."""
    token = jwks_provider.make_token(sub="user_multi_doc", email="multi@example.com", name="Multi")
    auth_headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/api/v1/auth/me", headers=auth_headers)
    assert me.status_code == 200
    summary_text = (
        "Swiggy Instamart\n"
        "Order ID: ORD31485518163\n"
        "Bill Summary\n"
        "Grand Total ₹300.00\n"
    ).encode("utf-8")

    resp1 = await client.post(
        "/api/v1/purchases/ocr",
        headers=auth_headers,
        files={"file": ("summary.txt", summary_text, "text/plain")},
    )
    assert resp1.status_code == 201
    draft1 = resp1.json()
    assert draft1["document_type"] == "bill_summary"
    assert draft1["order_id"] == "ORD31485518163"
    assert draft1["items"] == []
    assert "line_items" in draft1["missing_fields"]

    itemized_text = (
        "Swiggy Instamart\n"
        "Order ID: ORD31485518163\n"
        "Amul Milk 1L ₹65.00\n"
        "Whole Wheat Bread 400g ₹55.00\n"
        "Fresh Paneer 200g ₹180.00\n"
        "Grand Total ₹300.00\n"
    ).encode("utf-8")

    resp2 = await client.post(
        "/api/v1/purchases/ocr",
        headers=auth_headers,
        data={"purchase_id": draft1["id"]},
        files={"files": ("invoice.txt", itemized_text, "text/plain")},
    )
    assert resp2.status_code == 201
    merged = resp2.json()
    assert merged["id"] == draft1["id"]
    assert len(merged["items"]) == 3
    assert merged["total_amount_paise"] == 30000
    assert len(merged["attachments"]) == 2
    assert "line_items" not in merged["missing_fields"]

    # Re-attaching the exact same file is idempotent (no duplicate items)
    resp3 = await client.post(
        "/api/v1/purchases/ocr",
        headers=auth_headers,
        data={"purchase_id": draft1["id"]},
        files={"files": ("invoice.txt", itemized_text, "text/plain")},
    )
    assert resp3.status_code == 201
    assert len(resp3.json()["items"]) == 3

    # Verify zero inventory mutation before confirm
    lots_resp = await client.get("/api/v1/inventory/lots", headers=auth_headers)
    assert lots_resp.status_code == 200
    assert lots_resp.json()["items"] == []
