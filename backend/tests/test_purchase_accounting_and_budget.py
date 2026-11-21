"""End-to-end regression tests for receipt accounting, inventory eligibility, and budget tracking."""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from app.infra.ocr.pipeline import extract_receipt
from app.infra.ocr.providers import RawOcrOutput
from app.infra.ocr.schemas import OcrTokenBox
from tests.conftest import EphemeralJWKSProvider


def _santushti_real_boxes() -> list[OcrTokenBox]:
    """Exact bounding boxes observed from Apple Vision OCR on the uploaded Santushti Bhojanam photo."""
    return [
        OcrTokenBox(text="SSAI i", confidence=0.50, x=0.227, y=0.037, w=0.103, h=0.022),
        OcrTokenBox(text="Santustl Bhojanam", confidence=0.50, x=0.239, y=0.176, w=0.430, h=0.037),
        OcrTokenBox(text="Date: 26/04/26 Pick Up", confidence=1.00, x=0.095, y=0.336, w=0.506, h=0.040),
        OcrTokenBox(text="18:45", confidence=1.00, x=0.090, y=0.364, w=0.117, h=0.024),
        OcrTokenBox(text="Cashier: biller Bill No.: 2043", confidence=1.00, x=0.078, y=0.388, w=0.630, h=0.046),
        OcrTokenBox(text="Item", confidence=0.50, x=0.057, y=0.440, w=0.109, h=0.030),
        OcrTokenBox(text="Qty. Price Amount", confidence=1.00, x=0.355, y=0.452, w=0.417, h=0.039),
        OcrTokenBox(text="Phulka", confidence=0.50, x=0.043, y=0.506, w=0.143, h=0.030),
        OcrTokenBox(text="30", confidence=1.00, x=0.363, y=0.519, w=0.060, h=0.026),
        OcrTokenBox(text="15.00 450.00", confidence=1.00, x=0.458, y=0.521, w=0.315, h=0.044),
        OcrTokenBox(text="Patiala Kadai", confidence=0.50, x=0.031, y=0.545, w=0.292, h=0.042),
        OcrTokenBox(text="5 220.001100.00", confidence=1.00, x=0.370, y=0.560, w=0.402, h=0.051),
        OcrTokenBox(text="Paneer", confidence=1.00, x=0.022, y=0.583, w=0.160, h=0.032),
        OcrTokenBox(text="Chole Masala", confidence=1.00, x=0.011, y=0.625, w=0.314, h=0.042),
        OcrTokenBox(text="3 180,00 540.00", confidence=1.00, x=0.351, y=0.641, w=0.411, h=0.050),
        OcrTokenBox(text="Total Qty: 38", confidence=1.00, x=0.133, y=0.717, w=0.303, h=0.039),
        OcrTokenBox(text="Sub", confidence=1.00, x=0.483, y=0.713, w=0.090, h=0.030),
        OcrTokenBox(text="Total 2090.00", confidence=1.00, x=0.457, y=0.732, w=0.297, h=0.046),
        OcrTokenBox(text="CGST 2.5%", confidence=0.50, x=0.299, y=0.781, w=0.269, h=0.033),
        OcrTokenBox(text="52.25", confidence=1.00, x=0.620, y=0.789, w=0.127, h=0.033),
        OcrTokenBox(text="SGST 2.5%", confidence=0.50, x=0.295, y=0.812, w=0.267, h=0.038),
        OcrTokenBox(text="52.25", confidence=1.00, x=0.613, y=0.823, w=0.127, h=0.030),
        OcrTokenBox(text="Round off", confidence=1.00, x=0.300, y=0.876, w=0.174, h=0.027),
        OcrTokenBox(text="+0.5C", confidence=0.50, x=0.627, y=0.886, w=0.100, h=0.022),
        OcrTokenBox(text="Grand Total € 2195.00", confidence=1.00, x=0.146, y=0.898, w=0.578, h=0.040),
        OcrTokenBox(text="aid via Card", confidence=1.00, x=0.000, y=0.931, w=0.210, h=0.024),
    ]


@pytest.mark.asyncio
async def test_santushti_end_to_end_draft_confirm_budget_and_no_restaurant_inventory(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify full Santushti Bhojanam flow: OCR -> DB Draft -> Edit -> Confirm -> Budget (₹2,195) & 0 grocery lots."""
    token = jwks_provider.make_token(sub="user_accounting", email="acc@example.com", name="Acc")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    hh_id = me.json()["active_household"]["id"]
    headers["X-Household-Id"] = hh_id

    class _MockVision:
        name = "apple_vision_ocrmac"
        def is_available(self) -> bool:
            return True
        def recognize_image(self, _png_bytes: bytes, *, page: int = 1) -> RawOcrOutput:
            boxes = _santushti_real_boxes()
            return RawOcrOutput(
                adapter_name=self.name,
                boxes=boxes,
                raw_text="\n".join(b.text for b in boxes),
            )

    monkeypatch.setattr("app.infra.ocr.pipeline._select_local_ocr_provider", lambda: _MockVision())

    # Upload a valid PNG image carrying crisp text lines so IQA passes
    import io
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (661, 1024), (245, 245, 240))
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=24)
    for idx in range(12):
        d.text((40, 40 + idx * 60), f"Santushti Bhojanam Line {idx} 2090.00", fill=(20, 20, 20), font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    img_bytes = buf.getvalue()

    res = await client.post(
        "/api/v1/purchases/ocr",
        headers=headers,
        files={"file": ("santushti.png", img_bytes, "image/png")},
    )
    assert res.status_code == 201
    draft = res.json()

    # 1. Verify all extracted bill-level & item-level fields survived DB persistence + API response
    assert draft["status"] == "draft"
    assert draft["receipt_number"] == "2043"
    assert draft["purchased_on"] == "2026-04-26"
    assert draft["purchased_time"] == "18:45"
    assert draft["receipt_category"] == "restaurant"
    assert draft["payment_method"] == "CARD"
    assert draft["subtotal_paise"] == 209000
    assert draft["cgst_paise"] == 5225
    assert draft["sgst_paise"] == 5225
    assert draft["tax_paise"] == 10450
    assert draft["rounding_paise"] == 50
    assert draft["total_amount_paise"] == 219500
    assert draft["math_reconciled"] is True
    assert len(draft["items"]) == 3

    phulka, kadai, chole = draft["items"]
    assert phulka["display_name"] == "Phulka"
    assert float(phulka["normalized_qty"]) == 30.0
    assert phulka["unit_price_paise"] == 1500
    assert phulka["line_total_paise"] == 45000
    assert phulka["track_inventory"] is False

    assert kadai["display_name"] == "Patiala Kadai Paneer"
    assert float(kadai["normalized_qty"]) == 5.0
    assert kadai["unit_price_paise"] == 22000
    assert kadai["line_total_paise"] == 110000
    assert kadai["track_inventory"] is False

    assert chole["display_name"] == "Chole Masala"
    assert float(chole["normalized_qty"]) == 3.0
    assert chole["unit_price_paise"] == 18000
    assert chole["line_total_paise"] == 54000
    assert chole["track_inventory"] is False

    # 2. Retrying identical upload returns same draft (no duplicate draft)
    retry = await client.post(
        "/api/v1/purchases/ocr",
        headers=headers,
        files={"file": ("santushti.png", img_bytes, "image/png")},
    )
    assert retry.status_code == 201
    assert retry.json()["id"] == draft["id"]

    # 3. Unconfirmed draft must NOT affect budget or inventory
    inv_before = (await client.get("/api/v1/inventory/lots", headers=headers)).json()
    assert inv_before["items"] == []
    budget_before = (await client.get("/api/v1/insights/summary?days=365", headers=headers)).json()
    assert budget_before["total_spend_paise"] == 0

    # 4. Edit merchant spelling in draft and move purchased_on to today for 30d budget check; verify raw_text preserved
    today_str = date.today().isoformat()
    patch_res = await client.patch(
        f"/api/v1/purchases/drafts/{draft['id']}",
        headers=headers,
        json={
            "merchant_name": "Santushti Bhojanam",
            "purchased_on": today_str,
            "subtotal_paise": 209000,
            "cgst_paise": 5225,
            "sgst_paise": 5225,
            "rounding_paise": 50,
            "total_amount_paise": 219500,
            "items": [
                {
                    "id": phulka["id"],
                    "display_name": phulka["display_name"],
                    "normalized_qty": "30",
                    "base_unit": "pcs",
                    "unit_price_paise": 1500,
                    "line_total_paise": 45000,
                    "track_inventory": False,
                },
                {
                    "id": kadai["id"],
                    "display_name": kadai["display_name"],
                    "normalized_qty": "5",
                    "base_unit": "pcs",
                    "unit_price_paise": 22000,
                    "line_total_paise": 110000,
                    "track_inventory": False,
                },
                {
                    "id": chole["id"],
                    "display_name": chole["display_name"],
                    "normalized_qty": "3",
                    "base_unit": "pcs",
                    "unit_price_paise": 18000,
                    "line_total_paise": 54000,
                    "track_inventory": False,
                },
            ],
        },
    )
    assert patch_res.status_code == 200
    edited = patch_res.json()
    assert edited["merchant_name"] == "Santushti Bhojanam"
    assert edited["total_amount_paise"] == 219500
    assert edited["items"][1]["raw_text"] == "Patiala Kadai 5 220.001100.00 Paneer"

    # 5. Confirm purchase -> restaurant meals do NOT create grocery inventory lots!
    confirm_res = await client.post(
        f"/api/v1/purchases/{draft['id']}/confirm",
        headers=headers,
    )
    assert confirm_res.status_code == 200
    confirmed = confirm_res.json()
    assert confirmed["status"] == "confirmed"
    assert confirmed["total_amount_paise"] == 219500
    assert confirmed["created_lots"] == []

    # 6. Duplicate confirmation is rejected with 409
    dup_confirm = await client.post(
        f"/api/v1/purchases/{draft['id']}/confirm",
        headers=headers,
    )
    assert dup_confirm.status_code == 409

    # 7. Budget records ₹2,195.00 final paid with separate subtotal, CGST, SGST, rounding
    budget_after = (await client.get("/api/v1/insights/summary?days=30", headers=headers)).json()
    assert budget_after["final_amount_paid_paise"] == 219500
    assert budget_after["total_spend_paise"] == 219500
    assert budget_after["gross_subtotal_paise"] == 209000
    assert budget_after["total_cgst_paise"] == 5225
    assert budget_after["total_sgst_paise"] == 5225
    assert budget_after["total_tax_paise"] == 10450
    assert budget_after["total_rounding_paise"] == 50
    assert budget_after["spend_today_paise"] == 219500
    assert budget_after["purchase_count"] == 1
    assert budget_after["spend_by_merchant"][0]["merchant_name"] == "Santushti Bhojanam"
    assert budget_after["spend_by_merchant"][0]["total_paise"] == 219500


@pytest.mark.asyncio
async def test_grocery_inventory_and_price_changes_and_discarded_draft_isolation(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    """Verify grocery lots, avg price + price change across purchases, and discarded draft exclusion."""
    token = jwks_provider.make_token(sub="user_grocery", email="groc@example.com", name="Groc")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/api/v1/auth/me", headers=headers)
    hh_id = me.json()["active_household"]["id"]
    headers["X-Household-Id"] = hh_id

    today_str = date.today().isoformat()

    # Purchase 1: 2 kg Rice @ ₹120/kg = ₹240, zero GST
    p1 = await client.post(
        "/api/v1/purchases",
        headers=headers,
        json={
            "merchant_name": "FreshMart",
            "purchased_on": today_str,
            "discount_paise": 1000,
            "items": [
                {
                    "display_name": "Basmati Rice",
                    "category": "grains",
                    "qty": "2",
                    "unit": "kg",
                    "unit_price_paise": 12000,
                    "line_total_paise": 24000,
                    "track_inventory": True,
                }
            ],
        },
    )
    assert p1.status_code == 201
    p1_id = p1.json()["id"]
    c1 = await client.post(f"/api/v1/purchases/{p1_id}/confirm", headers=headers)
    assert c1.status_code == 200
    assert len(c1.json()["created_lots"]) == 1
    assert float(c1.json()["created_lots"][0]["remaining_qty"]) == 2000.0
    assert c1.json()["created_lots"][0]["base_unit"] == "g"

    # Purchase 2: 2 kg Rice @ ₹140/kg = ₹280 (price increased by ₹20/kg = +2000 paise)
    p2 = await client.post(
        "/api/v1/purchases",
        headers=headers,
        json={
            "merchant_name": "FreshMart",
            "purchased_on": today_str,
            "items": [
                {
                    "display_name": "Basmati Rice",
                    "category": "grains",
                    "qty": "2",
                    "unit": "kg",
                    "unit_price_paise": 14000,
                    "line_total_paise": 28000,
                    "track_inventory": True,
                }
            ],
        },
    )
    assert p2.status_code == 201
    c2 = await client.post(f"/api/v1/purchases/{p2.json()['id']}/confirm", headers=headers)
    assert c2.status_code == 200

    # Discarded draft must NOT count in budget or inventory
    d3 = await client.post(
        "/api/v1/purchases",
        headers=headers,
        json={
            "merchant_name": "FakeMart",
            "purchased_on": today_str,
            "items": [
                {
                    "display_name": "Basmati Rice",
                    "qty": "10",
                    "unit": "kg",
                    "unit_price_paise": 50000,
                    "line_total_paise": 500000,
                }
            ],
        },
    )
    assert d3.status_code == 201
    del_res = await client.delete(f"/api/v1/purchases/drafts/{d3.json()['id']}", headers=headers)
    assert del_res.status_code == 204

    insights = (await client.get("/api/v1/insights/summary?days=30", headers=headers)).json()
    assert insights["purchase_count"] == 2
    assert insights["gross_subtotal_paise"] == 52000
    assert insights["total_discounts_paise"] == 1000
    assert insights["final_amount_paid_paise"] == 51000
    rice_prod = insights["spend_by_product"][0]
    assert rice_prod["product_name"] == "basmati rice"
    assert rice_prod["total_qty"] == 4000.0
    assert rice_prod["avg_unit_price_paise"] == 13000
    assert rice_prod["latest_unit_price_paise"] == 14000
    assert rice_prod["price_change_paise"] == 2000


@pytest.mark.asyncio
async def test_tax_inclusive_and_no_gst_receipt_parsing() -> None:
    """Verify tax-inclusive MRP receipt does not double-count tax, and zero-GST receipt stays 0."""
    inclusive_txt = """
    D-Mart Retail
    Date: 2026-11-17
    Tata Salt 1 kg     ₹28.00
    Amul Butter 500 g  ₹275.00
    CGST 2.5%          ₹7.21
    SGST 2.5%          ₹7.21
    Grand Total        ₹303.00
    """
    r_inc = await extract_receipt("dmart.txt", "text/plain", inclusive_txt.encode())
    assert r_inc.math_reconciled is True
    assert r_inc.grand_total_paise == 30300
    assert r_inc.computed_total_paise == 30300

    no_gst_txt = """
    Local Vegetable Cart
    Date: 2026-11-17
    Tomatoes 1 kg      60.00
    Onions 2 kg        80.00
    Grand Total        140.00
    """
    r_nogst = await extract_receipt("veg.txt", "text/plain", no_gst_txt.encode())
    assert r_nogst.tax_paise is None
    assert r_nogst.grand_total_paise == 14000
    assert r_nogst.math_reconciled is True


@pytest.mark.asyncio
async def test_paneer_classification_override_history_access_deletion_and_isolation(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    """Cover grocery vs restaurant paneer, user override, detail access, deletion from history, and isolation."""
    token_a = jwks_provider.make_token(sub="user_issue_a", email="a@example.com", name="A")
    token_b = jwks_provider.make_token(sub="user_issue_b", email="b@example.com", name="B")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    hh_a = (await client.get("/api/v1/auth/me", headers=headers_a)).json()["active_household"]["id"]
    hh_b = (await client.get("/api/v1/auth/me", headers=headers_b)).json()["active_household"]["id"]
    headers_a["X-Household-Id"] = hh_a
    headers_b["X-Household-Id"] = hh_b

    today_str = date.today().isoformat()

    # 1. Grocery paneer -> eligible for inventory by default
    g_res = await client.post(
        "/api/v1/purchases/ocr",
        headers=headers_a,
        files={
            "file": (
                "grocery_paneer.txt",
                f"FreshMart Superstore\nDate: {today_str}\nFresh Paneer 250 g   Rs. 95.00\nGrand Total Rs. 95.00\n".encode(),
                "text/plain",
            )
        },
    )
    assert g_res.status_code == 201
    g_draft = g_res.json()
    assert g_draft["receipt_category"] == "grocery"
    assert g_draft["items"][0]["track_inventory"] is True
    g_conf = await client.post(f"/api/v1/purchases/{g_draft['id']}/confirm", headers=headers_a)
    assert g_conf.status_code == 200
    assert len(g_conf.json()["created_lots"]) == 1

    # Confirmed purchase cannot be silently deleted (409)
    del_confirmed = await client.delete(f"/api/v1/purchases/{g_draft['id']}", headers=headers_a)
    assert del_confirmed.status_code == 409

    # Confirmed purchase detail remains accessible
    detail_confirmed = await client.get(f"/api/v1/purchases/{g_draft['id']}", headers=headers_a)
    assert detail_confirmed.status_code == 200
    assert detail_confirmed.json()["status"] == "confirmed"
    assert detail_confirmed.json()["items"][0]["display_name"] == "Fresh Paneer"

    # Household B cannot access or delete Household A's purchase
    assert (await client.get(f"/api/v1/purchases/{g_draft['id']}", headers=headers_b)).status_code == 404
    assert (await client.delete(f"/api/v1/purchases/{g_draft['id']}", headers=headers_b)).status_code == 404

    # 2. Restaurant paneer dish -> budget-only by default, but user can override track_inventory=True
    r_res = await client.post(
        "/api/v1/purchases/ocr",
        headers=headers_a,
        files={
            "file": (
                "rest_paneer.txt",
                f"Spice Kitchen\nDate: {today_str}\nPatiala Kadai Paneer 1 pcs   Rs. 220.00\nGrand Total Rs. 220.00\n".encode(),
                "text/plain",
            )
        },
    )
    assert r_res.status_code == 201
    r_draft = r_res.json()
    assert r_draft["receipt_category"] == "restaurant"
    assert r_draft["items"][0]["track_inventory"] is False

    # Override track_inventory=True before confirm
    r_item = r_draft["items"][0]
    ovr = await client.patch(
        f"/api/v1/purchases/drafts/{r_draft['id']}",
        headers=headers_a,
        json={
            "items": [
                {
                    "id": r_item["id"],
                    "display_name": r_item["display_name"],
                    "normalized_qty": "1",
                    "base_unit": "pcs",
                    "unit_price_paise": 22000,
                    "line_total_paise": 22000,
                    "track_inventory": True,
                }
            ]
        },
    )
    assert ovr.status_code == 200
    assert ovr.json()["items"][0]["track_inventory"] is True
    r_conf = await client.post(f"/api/v1/purchases/{r_draft['id']}/confirm", headers=headers_a)
    assert r_conf.status_code == 200
    assert len(r_conf.json()["created_lots"]) == 1

    # 3. Failed/unwanted draft deleted -> excluded from GET /api/v1/purchases (history) & drafts forever
    f_res = await client.post(
        "/api/v1/purchases/ocr",
        headers=headers_a,
        files={"file": ("failed.txt", b"Unreadable gibberish", "text/plain")},
    )
    assert f_res.status_code == 201
    f_id = f_res.json()["id"]
    assert (await client.delete(f"/api/v1/purchases/drafts/{f_id}", headers=headers_a)).status_code == 204

    history_ids = [p["id"] for p in (await client.get("/api/v1/purchases", headers=headers_a)).json()["items"]]
    draft_ids = [p["id"] for p in (await client.get("/api/v1/purchases/drafts", headers=headers_a)).json()["items"]]
    assert f_id not in history_ids
    assert f_id not in draft_ids
    assert g_draft["id"] in history_ids
    assert r_draft["id"] in history_ids


@pytest.mark.asyncio
async def test_legacy_restaurant_purchase_backfill_idempotency_and_30d_vs_365d_insights(
    client: AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
    temp_db_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify legacy restaurant purchase without FoodConsumption is counted once in 365D, excluded from 30D, and backfill is idempotent."""
    from datetime import date
    from uuid import UUID
    from sqlalchemy import delete, func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.core.db import build_async_engine
    from app.modules.purchases.models import FoodConsumption
    from app.modules.purchases.service import backfill_missing_restaurant_consumptions

    tok = jwks_provider.make_token(sub="user_legacy_rest", email="leg_rest@example.com", name="LegRest")
    headers = {"Authorization": f"Bearer {tok}"}
    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    hh_id = UUID(me.json()["active_household"]["id"])

    # Create a confirmed restaurant purchase dated 2026-04-26 (₹2,195.00) and a grocery purchase in the last 30D
    rest = await client.post(
        "/api/v1/purchases",
        headers=headers,
        json={
            "merchant_name": "Santushti Bhojanam",
            "purchased_on": "2026-04-26",
            "receipt_category": "restaurant",
            "items": [
                {"display_name": "Phulka", "category": "general", "qty": 30, "unit": "pcs", "unit_price_paise": 1500, "line_total_paise": 45000, "track_inventory": False},
                {"display_name": "Patiala Kadai Paneer", "category": "general", "qty": 5, "unit": "pcs", "unit_price_paise": 22000, "line_total_paise": 110000, "track_inventory": False},
                {"display_name": "Chole Masala", "category": "general", "qty": 3, "unit": "pcs", "unit_price_paise": 18000, "line_total_paise": 54000, "track_inventory": False},
            ],
            "tax_paise": 10450,
            "rounding_paise": 50,
            "total_amount_paise": 219500,
        },
    )
    assert rest.status_code == 201
    rest_id = rest.json()["id"]
    assert (await client.post(f"/api/v1/purchases/{rest_id}/confirm", headers=headers)).status_code == 200

    groc = await client.post(
        "/api/v1/purchases",
        headers=headers,
        json={
            "merchant_name": "Fresh Mart",
            "purchased_on": "2026-11-18",
            "receipt_category": "grocery",
            "items": [
                {"display_name": "Whole Milk 1L", "category": "dairy", "qty": 1000, "unit": "ml", "unit_price_paise": 6000, "line_total_paise": 6000, "track_inventory": True},
            ],
            "total_amount_paise": 6000,
        },
    )
    assert groc.status_code == 201
    assert (await client.post(f"/api/v1/purchases/{groc.json()['id']}/confirm", headers=headers)).status_code == 200

    # Delete the FoodConsumption row to simulate a legacy confirmed restaurant purchase
    engine = build_async_engine(temp_db_url)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    async with sf() as db:
        await db.execute(delete(FoodConsumption).where(FoodConsumption.purchase_id == UUID(rest_id)))
        await db.commit()

    # Pin 'today' in insights router to 2026-11-19 for deterministic 30D vs 365D check
    from app.modules.insights import router as insights_router
    import datetime as _dt

    class _FixedDatetime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return _dt.datetime(2026, 11, 19, 10, 0, 0, tzinfo=_dt.UTC)

    monkeypatch.setattr(insights_router, "datetime", _FixedDatetime)

    # 1. Without FoodConsumption row: 30D excludes April receipt; 365D includes it ONCE under outside food
    s30 = (await client.get("/api/v1/insights/summary?days=30", headers=headers)).json()
    assert s30["total_spend_paise"] == 6000
    assert s30["outside_food_spend_paise"] == 0
    assert s30["grocery_spend_paise"] == 6000

    s365 = (await client.get("/api/v1/insights/summary?days=365", headers=headers)).json()
    assert s365["total_spend_paise"] == 225500
    assert s365["outside_food_spend_paise"] == 219500
    assert s365["grocery_spend_paise"] == 6000

    # 2. Idempotent backfill: creates 1 row on first call, 0 on second, keeps meal_type='unknown' & dining_mode='unknown'
    async with sf() as db:
        c1 = await backfill_missing_restaurant_consumptions(db, household_id=hh_id)
        c2 = await backfill_missing_restaurant_consumptions(db, household_id=hh_id)
        assert len(c1) == 1
        assert len(c2) == 0
        assert c1[0].meal_type == "unknown"
        assert c1[0].dining_mode == "unknown"
        assert c1[0].amount_paise == 219500
        assert c1[0].consumed_on == date(2026, 4, 26)
        fc_count = (await db.execute(select(func.count()).select_from(FoodConsumption).where(FoodConsumption.household_id == hh_id))).scalar_one()
        assert fc_count == 1
    await engine.dispose()

    # 3. After backfill: 365D still counts Santushti Bhojanam ONCE (219500), never double-counted
    s365_after = (await client.get("/api/v1/insights/summary?days=365", headers=headers)).json()
    assert s365_after["total_spend_paise"] == 225500
    assert s365_after["outside_food_spend_paise"] == 219500
    assert s365_after["grocery_spend_paise"] == 6000
    assert s365_after["outside_food_count"] == 1


@pytest.mark.asyncio
async def test_calendar_month_accounting_boundaries_leap_year_and_exclusions(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    """Verify calendar-month filtering, Dec->Jan, leap-year Feb 29, first/last day bounds, discarded exclusion, and tax/discount totals."""
    tok = jwks_provider.make_token(sub="user_cal_month", email="cal@example.com", name="Cal")
    headers = {"Authorization": f"Bearer {tok}"}
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 200

    async def confirm_purchase(
        purchased_on: str,
        merchant: str,
        cat: str,
        total: int,
        tax: int = 0,
        disc: int = 0,
    ) -> str:
        res = await client.post(
            "/api/v1/purchases",
            headers=headers,
            json={
                "merchant_name": merchant,
                "purchased_on": purchased_on,
                "receipt_category": cat,
                "items": [
                    {
                        "display_name": "Milk 500ml" if cat == "grocery" else "Masala Dosa",
                        "category": "dairy" if cat == "grocery" else "general",
                        "qty": 1,
                        "unit": "pcs",
                        "unit_price_paise": total,
                        "line_total_paise": total,
                        "track_inventory": cat == "grocery",
                    }
                ],
                "discount_paise": disc,
                "tax_paise": tax,
                "total_amount_paise": total,
            },
        )
        assert res.status_code == 201
        pid = res.json()["id"]
        assert (await client.post(f"/api/v1/purchases/{pid}/confirm", headers=headers)).status_code == 200
        return pid

    # Leap-year Feb 29, 2024
    await confirm_purchase("2024-02-29", "Leap Store", "grocery", 5000, tax=250, disc=100)
    # Dec 31, 2025 -> Jan 1, 2026 boundary
    await confirm_purchase("2025-12-31", "Dec Cafe", "restaurant", 12000, tax=600)
    await confirm_purchase("2026-01-01", "Jan Mart", "grocery", 8000, tax=400)
    # Oct 1 & Oct 31, 2026 + discarded Oct draft
    await confirm_purchase("2026-10-01", "Oct Start", "grocery", 10000, tax=500, disc=200)
    await confirm_purchase("2026-10-31", "Oct End", "restaurant", 25000, tax=1250)
    disc_draft = await client.post(
        "/api/v1/purchases",
        headers=headers,
        json={
            "merchant_name": "Discarded Oct",
            "purchased_on": "2026-10-15",
            "receipt_category": "grocery",
            "items": [{"display_name": "Rice 1kg", "category": "grains", "qty": 1, "unit": "pcs", "unit_price_paise": 9900, "line_total_paise": 9900}],
            "total_amount_paise": 9900,
        },
    )
    assert disc_draft.status_code == 201
    assert (await client.delete(f"/api/v1/purchases/drafts/{disc_draft.json()['id']}", headers=headers)).status_code == 204
    # April & November 2026
    await confirm_purchase("2026-04-26", "Santushti", "restaurant", 219500, tax=10450)
    await confirm_purchase("2026-11-18", "Nov Mart", "grocery", 73400, tax=660, disc=1800)

    # Leap-year Feb 2024
    feb = (await client.get("/api/v1/insights/summary?year=2024&month=2", headers=headers)).json()
    assert feb["period_start"] == "2024-02-01" and feb["period_end"] == "2024-02-29"
    assert feb["total_spend_paise"] == 5000 and feb["total_tax_paise"] == 250 and feb["total_discounts_paise"] == 100

    # Dec 2025 vs Jan 2026
    dec = (await client.get("/api/v1/insights/summary?year=2025&month=12", headers=headers)).json()
    jan = (await client.get("/api/v1/insights/summary?year=2026&month=1", headers=headers)).json()
    assert dec["total_spend_paise"] == 12000 and dec["outside_food_spend_paise"] == 12000
    assert jan["total_spend_paise"] == 8000 and jan["grocery_spend_paise"] == 8000
    assert jan["previous_period_spend_paise"] == 12000

    # Oct 2026 (first & last day included, discarded draft excluded)
    oct_s = (await client.get("/api/v1/insights/summary?year=2026&month=10", headers=headers)).json()
    assert oct_s["purchase_count"] == 2
    assert oct_s["total_spend_paise"] == 35000
    assert oct_s["grocery_spend_paise"] == 10000
    assert oct_s["outside_food_spend_paise"] == 25000
    assert oct_s["total_tax_paise"] == 1750
    assert oct_s["total_discounts_paise"] == 200

    # Nov 2026 (excludes Oct & April)
    nov_s = (await client.get("/api/v1/insights/summary?year=2026&month=11", headers=headers)).json()
    assert nov_s["purchase_count"] == 1
    assert nov_s["total_spend_paise"] == 73400
    assert nov_s["outside_food_spend_paise"] == 0
    assert nov_s["previous_period_spend_paise"] == 35000


@pytest.mark.asyncio
async def test_budget_daily_weekly_monthly_yearly_and_mixed_accounting(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    """Verify Daily, Mon-Sun Weekly, Monthly, Yearly (incl. leap year & Dec->Jan), mixed receipts, dedup, and Asia/Kolkata."""
    from uuid import UUID
    from sqlalchemy import delete
    from app.modules.purchases.models import FoodConsumption

    token = jwks_provider.make_token(sub="user_budget_4periods", email="budget4@example.com", name="B4")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    hh_id = me.json()["active_household"]["id"]
    headers["X-Household-Id"] = hh_id

    async def confirm_simple(purchased_on: str, merchant: str, cat: str, total: int, tax: int = 0, disc: int = 0, charges: int = 0) -> str:
        res = await client.post(
            "/api/v1/purchases",
            headers=headers,
            json={
                "merchant_name": merchant,
                "purchased_on": purchased_on,
                "receipt_category": cat,
                "items": [
                    {
                        "display_name": "Milk 500ml" if cat == "grocery" else "Paneer Tikka",
                        "category": "dairy" if cat == "grocery" else "general",
                        "qty": 1,
                        "unit": "pcs",
                        "unit_price_paise": total,
                        "line_total_paise": total,
                        "track_inventory": cat == "grocery",
                    }
                ],
                "discount_paise": disc,
                "tax_paise": tax,
                "additional_charges_paise": charges,
                "total_amount_paise": total,
            },
        )
        assert res.status_code == 201
        pid = res.json()["id"]
        assert (await client.post(f"/api/v1/purchases/{pid}/confirm", headers=headers)).status_code == 200
        return pid

    # 2024 leap year
    await confirm_simple("2024-02-29", "Leap Grocer", "grocery", 36600, tax=100, disc=50)
    # 2025 Dec 31 vs 2026 Jan 1
    await confirm_simple("2025-12-31", "NYE Diner", "restaurant", 15000, tax=750)
    await confirm_simple("2026-01-01", "Jan 1 Mart", "grocery", 10000, tax=500)

    # Week of Mon 2026-11-16 .. Sun 2026-11-22
    # Monday 2026-11-16: grocery
    await confirm_simple("2026-11-16", "Mon Mart", "grocery", 20000, tax=400, disc=100, charges=50)
    # Wednesday 2026-11-18: restaurant (with FoodConsumption deleted to test legacy missing FC fallback)
    legacy_rest_id = await confirm_simple("2026-11-18", "Wed Bistro", "restaurant", 30000, tax=1500)
    async with client._transport.app.state.session_maker() as db_session:  # type: ignore[attr-defined]
        await db_session.execute(delete(FoodConsumption).where(FoodConsumption.purchase_id == UUID(legacy_rest_id)))
        await db_session.commit()
    # Thursday 2026-11-19: mixed receipt (grocery 6000 + cooked outside food 4000 = 10000)
    mixed_res = await client.post(
        "/api/v1/purchases",
        headers=headers,
        json={
            "merchant_name": "Thu Mixed Hub",
            "purchased_on": "2026-11-19",
            "receipt_category": "mixed",
            "items": [
                {
                    "display_name": "Atta 5kg",
                    "category": "grains",
                    "qty": 1,
                    "unit": "pcs",
                    "unit_price_paise": 6000,
                    "line_total_paise": 6000,
                    "track_inventory": True,
                    "food_state": "raw",
                    "budget_treatment": "grocery_inventory",
                },
                {
                    "display_name": "Hot Samosa Box",
                    "category": "snacks",
                    "qty": 1,
                    "unit": "pcs",
                    "unit_price_paise": 4000,
                    "line_total_paise": 4000,
                    "track_inventory": False,
                    "food_state": "cooked",
                    "budget_treatment": "outside_food_expenditure",
                },
            ],
            "tax_paise": 200,
            "total_amount_paise": 10000,
        },
    )
    assert mixed_res.status_code == 201
    mixed_pid = mixed_res.json()["id"]
    assert (await client.post(f"/api/v1/purchases/{mixed_pid}/confirm", headers=headers)).status_code == 200
    # Sunday 2026-11-22: restaurant (with FoodConsumption row intact -> must not double count)
    await confirm_simple("2026-11-22", "Sun Cafe", "restaurant", 12000, tax=600)
    # Previous Sunday 2026-11-15: outside week of Nov 16-22
    await confirm_simple("2026-11-15", "Prev Sun Mart", "grocery", 5000)

    # Unconfirmed draft & discarded draft in same week -> must be excluded
    unconf = await client.post(
        "/api/v1/purchases",
        headers=headers,
        json={
            "merchant_name": "Unconfirmed Draft",
            "purchased_on": "2026-11-19",
            "receipt_category": "grocery",
            "items": [{"display_name": "Bread", "category": "bakery", "qty": 1, "unit": "pcs", "unit_price_paise": 4000, "line_total_paise": 4000}],
            "total_amount_paise": 4000,
        },
    )
    assert unconf.status_code == 201
    disc = await client.post(
        "/api/v1/purchases",
        headers=headers,
        json={
            "merchant_name": "Discarded Draft",
            "purchased_on": "2026-11-19",
            "receipt_category": "grocery",
            "items": [{"display_name": "Butter", "category": "dairy", "qty": 1, "unit": "pcs", "unit_price_paise": 5000, "line_total_paise": 5000}],
            "total_amount_paise": 5000,
        },
    )
    assert disc.status_code == 201
    assert (await client.delete(f"/api/v1/purchases/drafts/{disc.json()['id']}", headers=headers)).status_code == 204

    # 1. Daily view (2026-11-19 mixed receipt)
    daily = (await client.get("/api/v1/insights/summary?period=daily&date=2026-11-19", headers=headers)).json()
    assert daily["period_type"] == "daily"
    assert daily["period_start"] == "2026-11-19" and daily["period_end"] == "2026-11-19"
    assert daily["purchase_count"] == 1
    assert daily["total_spend_paise"] == 10000
    assert daily["grocery_spend_paise"] == 6000
    assert daily["outside_food_spend_paise"] == 4000
    assert daily["average_daily_spend_paise"] == 10000
    assert len(daily["period_purchases"]) == 1
    assert daily["period_purchases"][0]["merchant_name"] == "Thu Mixed Hub"

    # 2. Weekly view anchored on Wednesday 2026-11-18 -> Mon 2026-11-16 .. Sun 2026-11-22
    weekly = (await client.get("/api/v1/insights/summary?period=weekly&date=2026-11-18", headers=headers)).json()
    assert weekly["period_type"] == "weekly"
    assert weekly["period_start"] == "2026-11-16" and weekly["period_end"] == "2026-11-22"
    assert weekly["period_days"] == 7
    assert weekly["purchase_count"] == 4
    assert weekly["total_spend_paise"] == 72000  # 20000 + 30000 + 10000 + 12000
    assert weekly["outside_food_spend_paise"] == 46000  # 30000 (legacy) + 4000 (mixed) + 12000 (Sun Cafe)
    assert weekly["grocery_spend_paise"] == 26000  # 20000 + 6000
    assert weekly["total_discounts_paise"] == 100
    assert weekly["total_tax_paise"] == 2700  # 400 + 1500 + 200 + 600
    assert weekly["total_additional_charges_paise"] == 50
    assert weekly["previous_period_spend_paise"] == 5000  # Prev Sun Mart on 2026-11-15
    assert len(weekly["spending_trends"]) == 7
    assert weekly["spending_trends"][0]["date"] == "2026-11-16"
    assert weekly["spending_trends"][6]["date"] == "2026-11-22"
    assert weekly["elapsed_days"] > 0
    assert weekly["average_daily_spend_paise"] == round(72000 / weekly["elapsed_days"])

    # 3. Yearly view 2024 (leap year = 366 days) & 2026 (excludes 2025-12-31)
    y2024 = (await client.get("/api/v1/insights/summary?period=yearly&year=2024", headers=headers)).json()
    assert y2024["period_type"] == "yearly"
    assert y2024["period_start"] == "2024-01-01" and y2024["period_end"] == "2024-12-31"
    assert y2024["period_days"] == 366
    assert y2024["total_spend_paise"] == 36600
    assert y2024["average_daily_spend_paise"] == 100
    assert len(y2024["spending_trends"]) == 12
    assert y2024["spending_trends"][1] == {"date": "2024-02", "total_paise": 36600, "purchase_count": 1}

    y2026 = (await client.get("/api/v1/insights/summary?period=yearly&year=2026", headers=headers)).json()
    assert y2026["period_start"] == "2026-01-01" and y2026["period_end"] == "2026-12-31"
    assert y2026["period_days"] == 365
    assert y2026["total_spend_paise"] == 87000  # 10000 (Jan 1) + 5000 (Nov 15) + 72000 (Nov 16-22)
    assert y2026["previous_period_spend_paise"] == 15000  # 2025-12-31 NYE Diner
    assert len(y2026["spending_trends"]) == 12
    assert y2026["spending_trends"][0]["total_paise"] == 10000
    assert y2026["spending_trends"][10]["total_paise"] == 77000


@pytest.mark.asyncio
async def test_budget_crud_thresholds_history_isolation_and_discrepancy(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    """Verify budget CRUD, validation, 79.99/80/99.99/100/100.01% thresholds, history preservation, isolation, and discrepancies."""
    from app.modules.insights.router import _evaluate_budget_status

    # 1. Exact threshold boundary checks (using 1000000 paise = ₹10,000 limit for exact percentages)
    assert _evaluate_budget_status(1000000, 799900).status == "within_budget"
    assert _evaluate_budget_status(1000000, 799900).percentage_used == 79.99
    assert _evaluate_budget_status(1000000, 800000).status == "approaching_limit"
    assert _evaluate_budget_status(1000000, 800000).percentage_used == 80.0
    assert _evaluate_budget_status(1000000, 999900).status == "approaching_limit"
    assert _evaluate_budget_status(1000000, 999900).percentage_used == 99.99
    assert _evaluate_budget_status(1000000, 1000000).status == "fully_used"
    assert _evaluate_budget_status(1000000, 1000000).percentage_used == 100.0
    exceeded = _evaluate_budget_status(1000000, 1000100)
    assert exceeded.status == "exceeded"
    assert exceeded.percentage_used == 100.01
    assert exceeded.remaining_paise == -100
    assert exceeded.exceeded_paise == 100

    # Setup Household A and Household B
    tok_a = jwks_provider.make_token(sub="user_b_a", email="ba@example.com", name="A")
    hdr_a = {"Authorization": f"Bearer {tok_a}"}
    me_a = await client.get("/api/v1/auth/me", headers=hdr_a)
    hdr_a["X-Household-Id"] = me_a.json()["active_household"]["id"]

    tok_b = jwks_provider.make_token(sub="user_b_b", email="bb@example.com", name="B")
    hdr_b = {"Authorization": f"Bearer {tok_b}"}
    me_b = await client.get("/api/v1/auth/me", headers=hdr_b)
    hdr_b["X-Household-Id"] = me_b.json()["active_household"]["id"]

    # 2. Validation rejections
    bad_zero = await client.put(
        "/api/v1/insights/budgets/monthly",
        headers=hdr_a,
        json={"period_type": "monthly", "overall_limit_paise": 0},
    )
    assert bad_zero.status_code == 422

    bad_cat_gt = await client.put(
        "/api/v1/insights/budgets/monthly",
        headers=hdr_a,
        json={"period_type": "monthly", "overall_limit_paise": 100000, "grocery_limit_paise": 120000},
    )
    assert bad_cat_gt.status_code == 422

    bad_sum = await client.put(
        "/api/v1/insights/budgets/monthly",
        headers=hdr_a,
        json={
            "period_type": "monthly",
            "overall_limit_paise": 100000,
            "grocery_limit_paise": 70000,
            "outside_food_limit_paise": 40000,
        },
    )
    assert bad_sum.status_code == 422

    # 3. Create October monthly budget (₹10,000 overall, ₹7,000 grocery) and November update (₹12,000 overall, ₹8,000 grocery, ₹4,000 outside)
    oct_b = await client.put(
        "/api/v1/insights/budgets/monthly",
        headers=hdr_a,
        json={
            "period_type": "monthly",
            "overall_limit_paise": 1000000,
            "grocery_limit_paise": 700000,
            "effective_from": "2026-10-15",
        },
    )
    assert oct_b.status_code == 200
    assert oct_b.json()["effective_from"] == "2026-10-01"

    nov_b = await client.put(
        "/api/v1/insights/budgets/monthly",
        headers=hdr_a,
        json={
            "period_type": "monthly",
            "overall_limit_paise": 1200000,
            "grocery_limit_paise": 800000,
            "outside_food_limit_paise": 400000,
            "effective_from": "2026-11-01",
        },
    )
    assert nov_b.status_code == 200

    # Confirm purchases in Oct (₹8,500 grocery -> 85% approaching_limit) and Nov (₹13,000 restaurant -> exceeded)
    async def add_confirmed(purchased_on: str, merchant: str, cat: str, total: int) -> None:
        r = await client.post(
            "/api/v1/purchases",
            headers=hdr_a,
            json={
                "merchant_name": merchant,
                "purchased_on": purchased_on,
                "receipt_category": cat,
                "items": [
                    {
                        "display_name": "Item",
                        "category": "general",
                        "qty": 1,
                        "unit": "pcs",
                        "unit_price_paise": total,
                        "line_total_paise": total,
                        "track_inventory": cat == "grocery",
                    }
                ],
                "total_amount_paise": total,
            },
        )
        assert r.status_code == 201
        assert (await client.post(f"/api/v1/purchases/{r.json()['id']}/confirm", headers=hdr_a)).status_code == 200

    await add_confirmed("2026-10-10", "Oct Grocer", "grocery", 850000)
    await add_confirmed("2026-11-10", "Nov Bistro", "restaurant", 1300000)

    # Historical Oct budget still applies ₹10,000 limit (not overwritten by Nov's ₹12,000)
    oct_summary = (await client.get("/api/v1/insights/summary?period=monthly&year=2026&month=10", headers=hdr_a)).json()
    assert oct_summary["budget"]["overall"]["limit_paise"] == 1000000
    assert oct_summary["budget"]["overall"]["status"] == "approaching_limit"
    assert oct_summary["budget"]["grocery"]["limit_paise"] == 700000
    assert oct_summary["budget"]["grocery"]["status"] == "exceeded"
    assert oct_summary["budget"]["outside_food"] is None

    # Sept (before Oct budget) has no budget configured
    sep_summary = (await client.get("/api/v1/insights/summary?period=monthly&year=2026&month=9", headers=hdr_a)).json()
    assert sep_summary["budget"] is None

    # Nov uses ₹12,000 limit -> exceeded by ₹1,000
    nov_summary = (await client.get("/api/v1/insights/summary?period=monthly&year=2026&month=11", headers=hdr_a)).json()
    assert nov_summary["budget"]["overall"]["limit_paise"] == 1200000
    assert nov_summary["budget"]["overall"]["status"] == "exceeded"
    assert nov_summary["budget"]["overall"]["exceeded_paise"] == 100000
    assert nov_summary["budget"]["outside_food"]["status"] == "exceeded"

    # 4. Household isolation: Household B sees 0 budgets and cannot delete A's budget
    list_b = (await client.get("/api/v1/insights/budgets", headers=hdr_b)).json()
    assert list_b["items"] == []
    del_b = await client.delete(f"/api/v1/insights/budgets/{nov_b.json()['id']}", headers=hdr_b)
    assert del_b.status_code == 404

    # 5. Unclassified receipt discrepancy check (does not silently assign to grocery)
    await add_confirmed("2026-08-05", "Unresolved Store", "other", 5000)
    aug_summary = (await client.get("/api/v1/insights/summary?period=monthly&year=2026&month=8", headers=hdr_a)).json()
    assert aug_summary["total_spend_paise"] == 5000
    assert aug_summary["grocery_spend_paise"] == 0
    assert aug_summary["outside_food_spend_paise"] == 0
    assert aug_summary["unclassified_spend_paise"] == 5000
    assert aug_summary["accounting_discrepancy_paise"] == 5000





