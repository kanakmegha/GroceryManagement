"""Regression tests for Aggarwal Traders receipt OCR extraction, noise resilience, and draft isolation."""

from __future__ import annotations

import io

import httpx
import pytest
from PIL import Image, ImageDraw, ImageFont

from app.infra.ocr.pipeline import extract_receipt
from app.infra.ocr.providers import RawOcrOutput
from app.infra.ocr.schemas import OcrTokenBox, parse_inr_to_paise
from tests.conftest import EphemeralJWKSProvider


def _make_dummy_png(tag: str = "L012633") -> bytes:
    img = Image.new("RGB", (661, 1024), (245, 245, 240))
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=24)
    for idx in range(12):
        d.text((40, 40 + idx * 60), f"AGGARWAL TRADERS {tag} Line {idx}", fill=(20, 20, 20), font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _aggarwal_l012633_boxes() -> list[OcrTokenBox]:
    """Spatial OCR boxes for AGGARWAL TRADERS Bill L012633 (08/10/2026, ₹3,459.00, 10 items)."""
    rows = [
        ("Dhaniya Sabut", "0.500", "KG", "320", "160.00"),
        ("40 Chawal", "10", "KG", "40", "400.00"),
        ("Khula Atta", "10", "KG", "35", "350.00"),
        ("Badam", "0.750", "KG", "1180", "885.00"),
        ("Akhrot", "1", "KG", "1000", "1000.00"),
        ("Kali Malka", "1", "KG", "94", "94.00"),
        ("Chana Dal", "3", "KG", "100", "300.00"),
        ("Moong Sabut", "0.500", "KG", "120", "60.00"),
        ("Chole", "1", "KG", "120", "120.00"),
        ("Matar", "1", "KG", "90", "90.00"),
    ]
    boxes: list[OcrTokenBox] = [
        OcrTokenBox(text="AGGARWAL TRADERS", confidence=0.98, x=0.25, y=0.05, w=0.50, h=0.03),
        OcrTokenBox(text="D-173 West Vinod Nagar Mandawali, Delhi-110092", confidence=0.95, x=0.10, y=0.09, w=0.80, h=0.025),
        OcrTokenBox(text="Bill No. L012633", confidence=0.98, x=0.55, y=0.14, w=0.35, h=0.025),
        OcrTokenBox(text="Date: 08/10/2026", confidence=0.98, x=0.55, y=0.17, w=0.35, h=0.025),
        OcrTokenBox(text="Time: 10:40", confidence=0.98, x=0.10, y=0.17, w=0.25, h=0.025),
        # Table header
        OcrTokenBox(text="Description", confidence=0.98, x=0.08, y=0.23, w=0.30, h=0.025),
        OcrTokenBox(text="QTY", confidence=0.98, x=0.45, y=0.23, w=0.08, h=0.025),
        OcrTokenBox(text="UNIT", confidence=0.98, x=0.55, y=0.23, w=0.08, h=0.025),
        OcrTokenBox(text="Rate", confidence=0.98, x=0.66, y=0.23, w=0.08, h=0.025),
        OcrTokenBox(text="Amt", confidence=0.98, x=0.78, y=0.23, w=0.12, h=0.025),
    ]
    y = 0.27
    for name, qty, unit, rate, amt in rows:
        boxes.extend([
            OcrTokenBox(text=name, confidence=0.95, x=0.08, y=y, w=0.30, h=0.024),
            OcrTokenBox(text=qty, confidence=0.95, x=0.45, y=y, w=0.08, h=0.024),
            OcrTokenBox(text=unit, confidence=0.95, x=0.55, y=y, w=0.08, h=0.024),
            OcrTokenBox(text=rate, confidence=0.95, x=0.66, y=y, w=0.08, h=0.024),
            OcrTokenBox(text=amt, confidence=0.95, x=0.78, y=y, w=0.12, h=0.024),
        ])
        y += 0.04
    # Section divider & crossed-out line & footer & handwritten annotation near total
    boxes.extend([
        OcrTokenBox(text="DRY FRUITS=====>", confidence=0.90, x=0.08, y=0.385, w=0.30, h=0.02),
        OcrTokenBox(text="~~Cancelled Item~~", confidence=0.60, x=0.08, y=0.67, w=0.25, h=0.02),
        OcrTokenBox(text="Item Qty: 10", confidence=0.95, x=0.08, y=0.72, w=0.25, h=0.025),
        OcrTokenBox(text="Round off: 0.00", confidence=0.95, x=0.50, y=0.76, w=0.35, h=0.025),
        OcrTokenBox(text="G.TOTAL : - 3459.00", confidence=0.98, x=0.45, y=0.80, w=0.45, h=0.03),
        OcrTokenBox(text="3500", confidence=0.45, x=0.91, y=0.81, w=0.06, h=0.02),  # handwritten margin note
    ])
    return boxes


@pytest.mark.asyncio
async def test_aggarwal_l012633_exact_extraction_and_noise_resilience(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify exact AGGARWAL TRADERS L012633 extraction: 10 items, ₹3,459, 0 mismatch, ignores dividers/crossed-out/handwritten notes."""
    # Ensure parse_inr_to_paise never concatenates across currency symbols like '110$110.00'
    assert parse_inr_to_paise("110$110.00") is None

    class _MockVision:
        name = "apple_vision_ocrmac"
        def is_available(self) -> bool:
            return True
        def recognize_image(self, _png_bytes: bytes, *, page: int = 1) -> RawOcrOutput:
            boxes = _aggarwal_l012633_boxes()
            return RawOcrOutput(
                adapter_name=self.name,
                boxes=boxes,
                raw_text="\n".join(b.text for b in boxes),
            )

    monkeypatch.setattr("app.infra.ocr.pipeline._select_local_ocr_provider", lambda: _MockVision())
    parsed = await extract_receipt("l012633.png", "image/png", _make_dummy_png("L012633"))

    assert parsed.merchant_name == "AGGARWAL TRADERS"
    assert parsed.receipt_number == "L012633"
    assert parsed.receipt_date == "2026-10-08"
    assert len(parsed.lines) == 10
    assert sum(line.total_paise or 0 for line in parsed.lines) == 345900
    assert parsed.grand_total_paise == 345900
    assert not any("mismatch" in w.lower() for w in parsed.warnings)


@pytest.mark.asyncio
async def test_sequential_receipt_uploads_and_draft_reopen_no_stale_leakage(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify uploading L011139 (with 110$110.00 noise) then L012633 isolates each draft and preserves reopened values."""
    token = jwks_provider.make_token(sub="user_ocr_seq", email="ocrseq@example.com", name="Seq")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/api/v1/auth/me", headers=headers)
    headers["X-Household-Id"] = me.json()["active_household"]["id"]

    l011139_boxes = [
        OcrTokenBox(text="AGGARWAL TRADERS", confidence=0.98, x=0.25, y=0.05, w=0.50, h=0.03),
        OcrTokenBox(text="Bill No. L011139", confidence=0.98, x=0.55, y=0.14, w=0.35, h=0.025),
        OcrTokenBox(text="Date: 17/09/2026", confidence=0.98, x=0.55, y=0.17, w=0.35, h=0.025),
        OcrTokenBox(text="Description", confidence=0.98, x=0.08, y=0.23, w=0.30, h=0.025),
        OcrTokenBox(text="QTY", confidence=0.98, x=0.45, y=0.23, w=0.08, h=0.025),
        OcrTokenBox(text="UNIT", confidence=0.98, x=0.55, y=0.23, w=0.08, h=0.025),
        OcrTokenBox(text="Rate", confidence=0.98, x=0.66, y=0.23, w=0.08, h=0.025),
        OcrTokenBox(text="Amt", confidence=0.98, x=0.78, y=0.23, w=0.12, h=0.025),
        OcrTokenBox(text="CHOLE", confidence=0.95, x=0.08, y=0.30, w=0.20, h=0.025),
        OcrTokenBox(text="1", confidence=0.95, x=0.45, y=0.30, w=0.05, h=0.025),
        OcrTokenBox(text="KC", confidence=0.95, x=0.55, y=0.30, w=0.06, h=0.025),
        OcrTokenBox(text="110$110.00", confidence=0.90, x=0.66, y=0.30, w=0.24, h=0.025),
        OcrTokenBox(text="Item Qty: 1", confidence=0.95, x=0.08, y=0.40, w=0.25, h=0.025),
        OcrTokenBox(text="G.TOTAL : - 110.00", confidence=0.98, x=0.45, y=0.45, w=0.45, h=0.03),
    ]
    current_boxes = [l011139_boxes]

    class _MockVision:
        name = "apple_vision_ocrmac"
        def is_available(self) -> bool:
            return True
        def recognize_image(self, _png_bytes: bytes, *, page: int = 1) -> RawOcrOutput:
            b = current_boxes[0]
            return RawOcrOutput(adapter_name=self.name, boxes=b, raw_text="\n".join(x.text for x in b))

    monkeypatch.setattr("app.infra.ocr.pipeline._select_local_ocr_provider", lambda: _MockVision())
    png1 = _make_dummy_png("L011139")
    png2 = _make_dummy_png("L012633")

    # Upload 1: L011139
    r1 = await client.post("/api/v1/purchases/ocr", headers=headers, files={"file": ("r1.png", png1, "image/png")})
    assert r1.status_code == 201
    d1 = r1.json()
    assert d1["receipt_number"] == "L011139"
    assert d1["purchased_on"] == "2026-09-17"
    assert d1["total_amount_paise"] == 11000
    assert len(d1["items"]) == 1
    assert d1["items"][0]["line_total_paise"] == 11000
    assert d1["items"][0]["base_unit"] == "g"

    # Upload 2: L012633 immediately after
    current_boxes[0] = _aggarwal_l012633_boxes()
    r2 = await client.post("/api/v1/purchases/ocr", headers=headers, files={"file": ("r2.png", png2, "image/png")})
    assert r2.status_code == 201
    d2 = r2.json()
    assert d2["id"] != d1["id"]
    assert d2["receipt_number"] == "L012633"
    assert d2["purchased_on"] == "2026-10-08"
    assert d2["total_amount_paise"] == 345900
    assert len(d2["items"]) == 10
    assert sum(it["line_total_paise"] for it in d2["items"]) == 345900

    # Reopen saved drafts -> each retains its own exact values
    reopen1 = (await client.get(f"/api/v1/purchases/drafts/{d1['id']}", headers=headers)).json()
    reopen2 = (await client.get(f"/api/v1/purchases/drafts/{d2['id']}", headers=headers)).json()
    assert reopen1["receipt_number"] == "L011139" and reopen1["total_amount_paise"] == 11000
    assert reopen2["receipt_number"] == "L012633" and reopen2["total_amount_paise"] == 345900

    # Upload 3: Discarding d2 and re-uploading the same image creates a fresh unique draft
    del_res = await client.delete(f"/api/v1/purchases/drafts/{d2['id']}", headers=headers)
    assert del_res.status_code == 204
    r3 = await client.post("/api/v1/purchases/ocr", headers=headers, files={"file": ("r2_again.png", png2, "image/png")})
    assert r3.status_code == 201
    assert r3.json()["id"] != d2["id"]


@pytest.mark.asyncio
async def test_confirming_grocery_with_ambiguous_review_item_creates_no_outside_food_meal(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    """Confirming a grocery purchase with an ambiguous Needs Review item must not create a FoodConsumption meal record."""
    token = jwks_provider.make_token(sub="user_ambig_groc", email="ambig@example.com", name="Ambig")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/api/v1/auth/me", headers=headers)
    headers["X-Household-Id"] = me.json()["active_household"]["id"]

    draft = await client.post(
        "/api/v1/purchases/drafts",
        headers=headers,
        json={
            "merchant_name": "AGGARWAL TRADERS",
            "purchased_on": "2026-10-08",
            "receipt_category": "grocery",
            "total_amount_paise": 21000,
            "items": [
                {
                    "display_name": "Chole",
                    "normalized_qty": 1,
                    "base_unit": "pcs",
                    "unit_price_paise": 12000,
                    "line_total_paise": 12000,
                    "track_inventory": False,
                    "budget_treatment": "requires_user_review",
                },
                {
                    "display_name": "Matar",
                    "normalized_qty": 1000,
                    "base_unit": "g",
                    "unit_price_paise": 9000,
                    "line_total_paise": 9000,
                    "track_inventory": True,
                    "budget_treatment": "grocery_inventory",
                },
            ],
        },
    )
    assert draft.status_code == 201
    confirm = await client.post(f"/api/v1/purchases/drafts/{draft.json()['id']}/confirm", headers=headers, json={})
    assert confirm.status_code == 200
    assert confirm.json()["food_consumption"] is None


@pytest.mark.asyncio
async def test_manual_edit_reclassifies_clears_warning_and_confirms_without_subtotal(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    """Editing an ambiguous item name to 'Moong dhuli' with track_inventory=True clears stale warnings and confirms cleanly."""
    token = jwks_provider.make_token(sub="user_reclass_clear", email="reclass@example.com", name="Reclass")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/api/v1/auth/me", headers=headers)
    headers["X-Household-Id"] = me.json()["active_household"]["id"]

    # 1. Draft with ambiguous item ('Item Qty: 31')
    draft = await client.post(
        "/api/v1/purchases/drafts",
        headers=headers,
        json={
            "merchant_name": "AGGARWAL TRADERS",
            "purchased_on": "2026-10-08",
            "receipt_category": "grocery",
            "total_amount_paise": 12000,
            "items": [
                {
                    "display_name": "Item Qty: 31",
                    "normalized_qty": 1,
                    "base_unit": "pcs",
                    "unit_price_paise": 12000,
                    "line_total_paise": 12000,
                    "track_inventory": False,
                    "budget_treatment": "requires_user_review",
                }
            ],
        },
    )
    assert draft.status_code == 201
    draft_id = draft.json()["id"]
    item_id = draft.json()["items"][0]["id"]
    assert draft.json()["items"][0]["classification_status"] == "ambiguous_review"
    assert draft.json()["items"][0]["review_reason"] is not None

    # 2. Manually edit name -> 'Moong dhuli', check Add to Kitchen Inventory, and omit/clear subtotal_paise
    patched = await client.patch(
        f"/api/v1/purchases/drafts/{draft_id}",
        headers=headers,
        json={
            "merchant_name": "AGGARWAL TRADERS",
            "purchased_on": "2026-10-08",
            "subtotal_paise": None,
            "total_amount_paise": 12000,
            "items": [
                {
                    "id": item_id,
                    "display_name": "Moong dhuli",
                    "normalized_qty": 1000,
                    "base_unit": "g",
                    "unit_price_paise": 12000,
                    "line_total_paise": 12000,
                    "track_inventory": True,
                }
            ],
        },
    )
    assert patched.status_code == 200
    p_data = patched.json()
    assert p_data["subtotal_paise"] == 12000
    p_item = p_data["items"][0]
    assert p_item["display_name"] == "Moong dhuli"
    assert p_item["track_inventory"] is True
    assert p_item["classification_status"] == "pantry_eligible"
    assert p_item["review_reason"] is None
    assert p_item["warnings"] == []

    # 3. Confirm succeeds -> creates inventory lot and no outside-food meal record
    confirm = await client.post(f"/api/v1/purchases/drafts/{draft_id}/confirm", headers=headers, json={})
    assert confirm.status_code == 200
    c_data = confirm.json()
    assert len(c_data["created_lots"]) == 1
    assert c_data["food_consumption"] is None


@pytest.mark.asyncio
async def test_aggarwal_l011139_exact_10_rows_recovery_no_headings_or_summary() -> None:
    """Exact AGGARWAL TRADERS L011139 (17/09/2026, ₹2,260) fixture: 10 rows, no DRY FRUITS/DAL/Item Qty, ₹0 mismatch."""
    raw_text = "\n".join(
        [
            "AGGARWAL TRADERS",
            "D-173 WEST VINOD NAGAR MANDWALI",
            "DELHI-110092",
            "07BNZPA6067L1ZA Time: 13:30",
            "Customer: GENERAL LEDGER Bill No. L011139",
            "Date: 17/09/2026",
            "S. Description QTY UNIT Rate Amt",
            "1 CHINI DF 2 KG 53 106.00",
            "T 40 CHAWAL 10 KG 40 400.00",
            "S KHULA ATTA 10 KG 35 350.00",
            "DRY FRUITS=====>",
            "4 BADAM 1120 0.500 KG 1120 560.00",
            "DAL =====>",
            "5 LAL MALKA 2 KG 34 188.00",
            "6 KALI Malka 1 KG 94 94.00",
            "* Chana Dal 2 KG 96 192.00",
            "CHOLE 1 KC 110$110.00",
            "URAD, DHULI 1 KG 140 140.00",
            "10 Moong Dhyli 1 KG 120 120.00",
            "Item Qty: 31",
            "2 Round off: 0.00",
            "G.TOTAL : - 2260.00",
        ]
    )
    parsed = await extract_receipt("l011139.txt", "text/plain", raw_text.encode("utf-8"))

    assert parsed.merchant_name == "AGGARWAL TRADERS"
    assert parsed.receipt_number == "L011139"
    assert parsed.receipt_date == "2026-09-17"
    assert parsed.grand_total_paise == 226000
    assert len(parsed.lines) == 10

    names = [ln.name for ln in parsed.lines]
    assert not any("DRY FRUITS" in n.upper() or "DAL =====" in n.upper() or "ITEM QTY" in n.upper() for n in names)

    expected = [
        ("CHINI DF", 2000.0, "g", 5300, 10600),
        ("40 CHAWAL", 10000.0, "g", 4000, 40000),
        ("KHULA ATTA", 10000.0, "g", 3500, 35000),
        ("BADAM 1120", 500.0, "g", 112000, 56000),
        ("LAL MALKA", 2000.0, "g", 3400, 18800),
        ("KALI Malka", 1000.0, "g", 9400, 9400),
        ("Chana Dal", 2000.0, "g", 9600, 19200),
        ("CHOLE", 1000.0, "g", 11000, 11000),
        ("URAD, DHULI", 1000.0, "g", 14000, 14000),
        ("Moong Dhyli", 1000.0, "g", 12000, 12000),
    ]
    for ln, (exp_name, exp_qty, exp_unit, exp_rate, exp_total) in zip(parsed.lines, expected, strict=True):
        assert ln.name == exp_name
        assert ln.normalized_qty == exp_qty
        assert ln.base_unit == exp_unit
        assert ln.unit_price_paise == exp_rate
        assert ln.total_paise == exp_total

    assert sum(ln.total_paise or 0 for ln in parsed.lines) == 226000
    assert parsed.math_reconciled is True

    # LAL MALKA has OCR rate ₹34 vs printed ₹188 -> flagged with implied rate ₹94/kg without overwriting
    lal_malka = parsed.lines[4]
    assert lal_malka.name == "LAL MALKA"
    assert lal_malka.unit_price_paise == 3400
    assert lal_malka.total_paise == 18800
    assert lal_malka.suggested_unit_price_paise == 9400
    assert lal_malka.uncertain_fields == ["unit_price"]
    assert lal_malka.needs_review is True
    assert any("implied rate: ₹94.00/kg" in w for w in lal_malka.warnings)

    # All other 9 items remain clean and unflagged
    for idx, ln in enumerate(parsed.lines):
        if idx != 4:
            assert ln.needs_review is False
            assert ln.uncertain_fields == []


@pytest.mark.asyncio
async def test_aggarwal_l015174_exact_13_rows_annotated_total_and_grid_misalignment() -> None:
    """AGGARWAL TRADERS L015174 (18/11/2026): recovers all 13 rows, skips headings, flags 5319 vs 5119."""
    raw_text = "\n".join(
        [
            "AGGARWAL TRADERS",
            "D-173 WEST VINOD NAGAR MANDWALI",
            "DELHI-110092",
            "07BNZPA6087L1ZA Time : 08:27",
            "Customer: GENERAL LEDGER Bill No. L015174",
            "Date: 18/11/2026",
            "S. Description ATY, UNIT Rate Amt",
            "1 LALCHAWAL 2 KG 200 400.00",
            "2 40 CHAWAL 5 KG 40 200.00",
            "KHULA ATTA 10 KG 36 360.00",
            "CHIANDF 2 KG 67 134.00",
            "-DRY FRUITS=====>",
            "5 BADAM 1180 0.750 KG 1180 885.00",
            "* AKHROT 16001- 1 KG 1600 1600.00",
            "TAKHROT 1000 1 • KG 1000 1000.00",
            "DAL -=",
            "8 Chana-Đal 2 KG 110: 220.00",
            "Moong SabuT KG 130 130.00",
            "TO CHOLE 1 KG 120 120.00",
            "11 CHANA KALA 1 KG 110 110.00",
            "- LOETA LAL 0.500 KG 140 70.00",
            "To Matar 1 KGS 90 90.00",
            "Atem Qty: 28",
            "Round off: 0.00",
            "G. AL : - 5319.00",
            "Rs. Five Thousand Three Hundred and Nineteen ony",
            "5119",
            "change & Return Within 4 Days With Bill",
        ]
    )
    parsed = await extract_receipt("l015174.txt", "text/plain", raw_text.encode("utf-8"))

    assert parsed.merchant_name == "AGGARWAL TRADERS"
    assert parsed.receipt_number == "L015174"
    assert parsed.receipt_date == "2026-11-18"
    assert parsed.printed_item_qty == 28.0
    assert parsed.grand_total_paise == 531900
    assert len(parsed.lines) == 13
    assert sum(ln.total_paise or 0 for ln in parsed.lines) == 531900
    assert parsed.math_reconciled is True
    assert parsed.needs_review is True
    assert any(issue.code == "ANNOTATED_GRAND_TOTAL" for issue in parsed.validation_issues)
    assert any("5319.00" in w and "5119.00" in w for w in parsed.warnings)

    names = [ln.name for ln in parsed.lines]
    assert not any("DRY FRUITS" in n.upper() or "DAL -=" in n.upper() or "ATEM QTY" in n.upper() or "G. AL" in n.upper() for n in names)
    # Two AKHROT products with different rates preserved distinctly
    akhrot_totals = [ln.total_paise for ln in parsed.lines if "AKHROT" in ln.name.upper()]
    assert akhrot_totals == [160000, 100000]


def test_spatial_table_grid_line_inside_description_cannot_drop_rows() -> None:
    """An internal S.No.|Description vertical line at x=0.1317 must not collapse Description and drop 12 of 13 rows."""
    from app.infra.ocr.schemas import OcrTokenBox
    from app.infra.ocr.spatial_parser import parse_receipt_text_and_boxes
    from app.infra.ocr.validation import validate_extracted_receipt

    boxes = [
        # Header: S. Description at x=[0.03..0.29], ATY, at [0.46..0.52], UNIT Rate at [0.55..0.72], Amt at [0.78..0.88]
        OcrTokenBox(text="S. Description", x=0.03, y=0.20, width=0.26, height=0.02, confidence=0.95, page=1),
        OcrTokenBox(text="ATY,", x=0.46, y=0.20, width=0.06, height=0.02, confidence=0.90, page=1),
        OcrTokenBox(text="UNIT Rate", x=0.55, y=0.20, width=0.17, height=0.02, confidence=0.95, page=1),
        OcrTokenBox(text="Amt", x=0.78, y=0.20, width=0.10, height=0.02, confidence=0.95, page=1),
        # Row 1: LALCHAWAL (center 0.15 > 0.1317)
        OcrTokenBox(text="1 LALCHAWAL", x=0.03, y=0.25, width=0.24, height=0.02, confidence=0.95, page=1),
        OcrTokenBox(text="2", x=0.47, y=0.25, width=0.03, height=0.02, confidence=0.95, page=1),
        OcrTokenBox(text="KG 200", x=0.55, y=0.25, width=0.14, height=0.02, confidence=0.95, page=1),
        OcrTokenBox(text="400.00", x=0.79, y=0.25, width=0.10, height=0.02, confidence=0.95, page=1),
        # Row 2: 40 CHAWAL (center 0.145 > 0.1317)
        OcrTokenBox(text="2 40 CHAWAL", x=0.03, y=0.29, width=0.23, height=0.02, confidence=0.95, page=1),
        OcrTokenBox(text="5", x=0.47, y=0.29, width=0.03, height=0.02, confidence=0.95, page=1),
        OcrTokenBox(text="KG 40", x=0.55, y=0.29, width=0.12, height=0.02, confidence=0.95, page=1),
        OcrTokenBox(text="200.00", x=0.79, y=0.29, width=0.10, height=0.02, confidence=0.95, page=1),
        # Row 3: To Matar (center 0.125 < 0.1317)
        OcrTokenBox(text="To Matar", x=0.03, y=0.33, width=0.19, height=0.02, confidence=0.95, page=1),
        OcrTokenBox(text="1", x=0.47, y=0.33, width=0.03, height=0.02, confidence=0.95, page=1),
        OcrTokenBox(text="KGS 90", x=0.55, y=0.33, width=0.14, height=0.02, confidence=0.95, page=1),
        OcrTokenBox(text="90.00", x=0.79, y=0.33, width=0.10, height=0.02, confidence=0.95, page=1),
    ]
    res = parse_receipt_text_and_boxes(
        raw_text="",
        boxes=boxes,
        vertical_grid_xs=[0.1317, 0.75],
        filename="l015174.jpg",
        content_type="image/jpeg",
        source_type="image",
        adapter_used="apple_vision_ocrmac",
        base_confidence=0.85,
    )
    res = validate_extracted_receipt(res)
    assert len(res.lines) == 3
    assert [ln.total_paise for ln in res.lines] == [40000, 20000, 9000]


@pytest.mark.asyncio
async def test_crossout_explicit_cancelled_vs_uncertain_and_incomplete_extraction_safeguards() -> None:
    """Genuine cancelled rows are excluded; uncertain cross-outs are preserved for review; 1-row partial extraction is flagged."""
    from app.infra.ocr.schemas import ExtractedReceipt, ExtractedReceiptLine
    from app.infra.ocr.validation import validate_extracted_receipt

    raw_text = "\n".join(
        [
            "AGGARWAL TRADERS",
            "Bill No. L099999",
            "Date: 18/11/2026",
            "1 LAL CHAWAL 2 KG 200 400.00",
            "~~2 40 CHAWAL 5 KG 40 200.00~~",
            "[CANCELLED] KHULA ATTA 10 KG 36 360.00",
            "~ BADAM 1180 0.750 KG 1180 885.00",
            "Matar 1 KG 90 90.00",
            "G.TOTAL : - 1375.00",
        ]
    )
    parsed = await extract_receipt("crossout.txt", "text/plain", raw_text.encode("utf-8"))
    names = [ln.name for ln in parsed.lines]
    # Explicitly cancelled rows excluded
    assert not any("40 CHAWAL" in n or "KHULA ATTA" in n for n in names)
    # Uncertain cross-out (~ BADAM) preserved and flagged for targeted review
    badam = next(ln for ln in parsed.lines if "BADAM" in ln.name)
    assert badam.needs_review is True
    assert "crossout" in badam.uncertain_fields
    assert any("crossed-out" in w.lower() for w in badam.warnings)
    assert any("excluded 2 explicitly cancelled" in w.lower() for w in parsed.warnings)

    # Incomplete extraction safeguard: 1 row recovered out of 13 candidates / qty 28 -> error & no trusted computed total
    incomplete = validate_extracted_receipt(
        ExtractedReceipt(
            filename="partial.jpg",
            content_type="image/jpeg",
            source_type="image",
            adapter_used="apple_vision_ocrmac",
            candidate_row_count=13,
            printed_item_qty=28.0,
            grand_total_paise=531900,
            lines=[
                ExtractedReceiptLine(
                    raw_text="To Matar 1 KGS 90 90.00",
                    name="To Matar",
                    quantity=1.0,
                    unit="kgs",
                    unit_price_paise=9000,
                    total_paise=9000,
                )
            ],
        )
    )
    assert incomplete.needs_review is True
    assert incomplete.math_reconciled is False
    assert incomplete.computed_total_paise is None
    assert incomplete.confidence <= 0.35
    assert any(i.code == "INCOMPLETE_EXTRACTION" and i.severity == "error" for i in incomplete.validation_issues)

    # Genuine single-item receipt is NOT flagged as incomplete
    genuine_one = validate_extracted_receipt(
        ExtractedReceipt(
            filename="one_item.jpg",
            content_type="image/jpeg",
            source_type="image",
            adapter_used="apple_vision_ocrmac",
            candidate_row_count=1,
            printed_item_qty=1.0,
            grand_total_paise=9000,
            lines=[
                ExtractedReceiptLine(
                    raw_text="Matar 1 KG 90 90.00",
                    name="Matar",
                    quantity=1.0,
                    unit="kg",
                    unit_price_paise=9000,
                    total_paise=9000,
                )
            ],
        )
    )
    assert not any(i.code == "INCOMPLETE_EXTRACTION" for i in genuine_one.validation_issues)
    assert genuine_one.math_reconciled is True

