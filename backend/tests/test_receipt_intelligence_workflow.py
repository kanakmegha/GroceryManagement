"""Phase 9 regression tests for receipt intelligence, mixed classification, meal tracking, price
history, and recipes.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from app.infra.ocr.spatial_parser import parse_receipt_text_and_boxes
from app.infra.ocr.validation import validate_extracted_receipt
from tests.conftest import EphemeralJWKSProvider


@pytest.mark.asyncio
async def test_instamart_and_usd_weighted_produce_extraction_independent_of_merchant() -> None:
    """Both Swiggy Instamart (with R$ OCR noise & 'Dates') and USD weighted produce extract
    cleanly.
    """
    instamart_raw = """SWIGGY
instamart
ESSAI NO: 11221302000969
mported Daily Apple (Sebu) R$ 57.00
ion Arabian Dates R$ :114.00
Green Lettuce Rs 20.00
Urg Excel Matic Liquid Detergent Too Load Pouch
Rs 391.00
Handling Fee: R$ 5.00
delivery partner fee; Rs 16,00
Discount : Rs 18:00
Tirand Total: R$ 585.00"""
    r1 = validate_extracted_receipt(
        parse_receipt_text_and_boxes(
            raw_text=instamart_raw,
            filename="instamart.jpg",
            content_type="image/jpeg",
            source_type="image",
            adapter_used="apple_vision_ocrmac",
            base_confidence=0.85,
        )
    )
    assert len(r1.lines) == 4
    assert any("Dates" in line.name and line.total_paise == 11400 for line in r1.lines)
    assert any("Detergent" in line.name and line.total_paise == 39100 for line in r1.lines)
    assert r1.additional_charges_paise == 2100
    assert r1.discount_paise == 1800
    assert r1.math_reconciled is True

    usd_raw = """DATE 06/01/2016 WED
****** ****
ZUCHINNI GREEN $4.66
0.778kg NET # $5.99/kg
BANANA CAVENDISH $1.32
0.442kg NET B $2.99/kg
LOYALTY -15.00
TOTAL. $5.98
CASH $20.00
CHANGE $14.02"""
    r2 = validate_extracted_receipt(
        parse_receipt_text_and_boxes(
            raw_text=usd_raw,
            filename="produce.jpg",
            content_type="image/jpeg",
            source_type="image",
            adapter_used="apple_vision_ocrmac",
            base_confidence=0.85,
        )
    )
    assert r2.currency == "USD"
    assert r2.receipt_date == "2016-01-06"
    assert len(r2.lines) == 2
    assert r2.lines[0].normalized_qty == 778.0
    assert r2.lines[0].base_unit == "g"
    assert r2.lines[0].unit_price_paise == 599
    assert r2.lines[0].total_paise == 466
    assert r2.discount_paise == 1500
    assert r2.grand_total_paise == 598


@pytest.mark.asyncio
async def test_mixed_receipt_meal_tracking_price_history_and_recipes(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    """Verify mixed receipt, uncertain classification warning, meal tracking, ₹/kg price history,
    and recipes.
    """
    token = jwks_provider.make_token(sub="user_intel", email="intel@example.com", name="Intel")
    headers = {"Authorization": f"Bearer {token}"}
    hh_id = (await client.get("/api/v1/auth/me", headers=headers)).json()["active_household"]["id"]
    headers["X-Household-Id"] = hh_id
    today_str = date.today().isoformat()

    # 1. Uncertain item classification on unknown merchant preserves receipt + emits warning
    uncertain_txt = (
        f"Corner Shop\nDate: {today_str}\nPhulka 10 pcs   Rs. 150.00\nGrand Total Rs. 150.00\n"
    )
    u_res = await client.post(
        "/api/v1/purchases/ocr",
        headers=headers,
        files={"file": ("uncertain.txt", uncertain_txt.encode(), "text/plain")},
    )
    assert u_res.status_code == 201
    u_draft = u_res.json()
    assert len(u_draft["items"]) == 1
    assert any("budget-only" in w for w in u_draft["ocr_warnings"])

    # Confirm with explicit meal tracking
    u_conf = await client.post(
        f"/api/v1/purchases/drafts/{u_draft['id']}/confirm",
        headers=headers,
        json={"meal_type": "dinner", "dining_mode": "takeaway", "people_count": 2},
    )
    assert u_conf.status_code == 200
    assert u_conf.json()["created_lots"] == []
    assert u_conf.json()["food_consumption"]["meal_type"] == "dinner"
    assert u_conf.json()["food_consumption"]["dining_mode"] == "takeaway"
    assert u_conf.json()["food_consumption"]["people_count"] == 2

    # Edit meal classification after confirmation
    meal_patch = await client.patch(
        f"/api/v1/purchases/{u_draft['id']}/meal",
        headers=headers,
        json={"meal_type": "lunch", "people_count": 3},
    )
    assert meal_patch.status_code == 200
    assert meal_patch.json()["meal_type"] == "lunch"
    assert meal_patch.json()["people_count"] == 3

    # 2. Mixed receipt: 1 grocery item (Basmati Rice 2 kg) + 1 prepared food item (Biryani)
    mixed_txt = (
        f"Bazaar Food Court\nDate: {today_str}\n"
        "Basmati Rice 2 kg   Rs. 240.00\nChicken Biryani 1 pcs   Rs. 260.00\n"
        "Grand Total Rs. 500.00\n"
    )
    m_res = await client.post(
        "/api/v1/purchases/ocr",
        headers=headers,
        files={"file": ("mixed.txt", mixed_txt.encode(), "text/plain")},
    )
    assert m_res.status_code == 201
    m_draft = m_res.json()
    assert m_draft["receipt_category"] == "mixed"
    m_conf = await client.post(
        f"/api/v1/purchases/{m_draft['id']}/confirm",
        headers=headers,
        json={"meal_type": "lunch", "dining_mode": "delivery"},
    )
    assert m_conf.status_code == 200
    # Only Basmati Rice enters pantry inventory; Biryani goes to FoodConsumption
    assert len(m_conf.json()["created_lots"]) == 1
    rice_cid = m_conf.json()["created_lots"][0]["canonical_item_id"]
    assert m_conf.json()["food_consumption"]["amount_paise"] == 26000

    # 3. Second grocery purchase of Basmati Rice at higher ₹/kg -> verify Price History & Recipes
    g2_res = await client.post(
        "/api/v1/purchases",
        headers=headers,
        json={
            "merchant_name": "FreshMart",
            "purchased_on": today_str,
            "items": [
                {
                    "canonical_item_id": rice_cid,
                    "display_name": "Basmati Rice",
                    "qty": "1",
                    "unit": "kg",
                    "unit_price_paise": 15000,
                    "line_total_paise": 15000,
                    "track_inventory": True,
                }
            ],
        },
    )
    assert g2_res.status_code == 201
    await client.post(f"/api/v1/purchases/{g2_res.json()['id']}/confirm", headers=headers)

    ph = (
        await client.get(f"/api/v1/inventory/items/{rice_cid}/price-history", headers=headers)
    ).json()
    assert ph["standard_unit_label"] == "₹/kg"
    assert ph["latest_price_paise_per_std"] == 15000
    assert ph["avg_price_paise_per_std"] == 13500
    assert ph["price_change_paise"] == 3000
    assert ph["price_change_pct"] == 25.0
    assert len(ph["entries"]) == 2

    # 4. Budget counts food + grocery once without double-counting
    insights = (await client.get("/api/v1/insights/summary?days=30", headers=headers)).json()
    assert insights["final_amount_paid_paise"] == 15000 + 50000 + 15000  # ₹800.00
    assert insights["outside_food_spend_paise"] == 15000 + 26000  # ₹410.00
    assert insights["grocery_spend_paise"] == 80000 - 41000  # ₹390.00
    assert insights["outside_food_count"] == 2

    outside_list = (
        await client.get("/api/v1/insights/outside-food?days=30", headers=headers)
    ).json()
    assert len(outside_list) == 2

    # 5. Recipe recommendations work with confirmed grocery inventory (no crash, grounded in
    # inventory)
    recs = await client.get("/api/v1/recipes/recommendations", headers=headers)
    assert recs.status_code == 200
