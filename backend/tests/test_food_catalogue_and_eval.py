"""Held-out evaluation of the canonical food catalogue & layered classification engine."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.modules.catalog.classifier import FoodClassificationService, FoodNameNormalizer
from tests.conftest import EphemeralJWKSProvider


def test_held_out_indian_and_international_food_classification_metrics() -> None:
    """Evaluate held-out food descriptions (unseen phrasing) across category, state, and pantry eligibility."""
    held_out_cases: list[tuple[str, str, str, str, bool]] = [
        # (raw_description, receipt_category, expected_category, expected_state, expected_pantry_eligible)
        ("Fresh Bhindi 250g", "grocery", "vegetable", "fresh", True),
        ("Green Lauki 1pc", "grocery", "vegetable", "fresh", True),
        ("Desi Tamatar 500g", "grocery", "vegetable", "fresh", True),
        ("Arhar Dal Unpolished 1kg", "grocery", "pulse", "dried", True),
        ("Kala Chana Whole 500g", "grocery", "pulse", "dried", True),
        ("Doodh Full Cream 1L", "grocery", "dairy", "fresh", True),
        ("Haldi Powder 200g", "grocery", "spice", "dried", True),
        ("Jeera Whole 100g", "grocery", "spice", "dried", True),
        ("Chakki Aata 5kg", "grocery", "grain", "processed", True),
        ("Cow Ghee 500ml", "grocery", "dairy", "processed", True),
        ("Malai Paneer 200g", "grocery", "dairy", "fresh", True),
        ("Whole Wheat Brown Bread 400g", "grocery", "bakery", "processed", True),
        ("Marie Biscuits 250g", "grocery", "snack", "ready_to_eat", True),
        ("Refined Soyabean Oil 1 L Pouch", "grocery", "oil", "processed", True),
        ("Butter Roti", "restaurant", "cooked_meal", "cooked", False),
        ("Palak Paneer", "restaurant", "cooked_meal", "cooked", False),
        ("Hyderabadi Veg Biryani", "restaurant", "cooked_meal", "cooked", False),
        ("Masala Dosa", "restaurant", "cooked_meal", "cooked", False),
        ("Chole Masala", "restaurant", "cooked_meal", "cooked", False),
    ]

    cat_hits = 0
    state_hits = 0
    pantry_hits = 0
    false_positive_pantry = 0

    for desc, rec_cat, exp_cat, exp_state, exp_pantry in held_out_cases:
        res = FoodClassificationService.classify_item(desc, receipt_category=rec_cat)
        if res.category == exp_cat:
            cat_hits += 1
        if res.food_state == exp_state:
            state_hits += 1
        if res.pantry_eligible == exp_pantry:
            pantry_hits += 1
        if not exp_pantry and res.pantry_eligible:
            false_positive_pantry += 1

    total = len(held_out_cases)
    assert cat_hits / total >= 0.90
    assert state_hits / total >= 0.90
    assert pantry_hits == total
    assert false_positive_pantry == 0


def test_uncertain_item_flagged_for_review_and_normalizer_preserves_identity() -> None:
    """Non-food items are excluded as household_non_food, and unknown OCR items require review with pantry_eligible=False."""
    assert FoodNameNormalizer.normalize("bb Popular Toor Dal 1 kg Pouch (7 Days Return)") == "toor dal"
    scrubber = FoodClassificationService.classify_item("Stainless Steel Scrubber 2pcs", receipt_category="grocery")
    assert scrubber.budget_treatment == "household_non_food"
    assert scrubber.pantry_eligible is False
    assert scrubber.classification_status == "non_food"

    unknown = FoodClassificationService.classify_item("Xyzqr Unrecognized Ocr Token 2pcs", receipt_category="grocery")
    assert unknown.budget_treatment == "requires_user_review"
    assert unknown.pantry_eligible is False
    assert unknown.classification_status == "ambiguous_review"
    assert unknown.needs_review is True
    assert unknown.confidence < 0.70


def test_required_pantry_vs_meal_vs_nonfood_classification_pairs() -> None:
    """Verify all required classification distinctions (raw vs cooked, food vs non-food, unknown)."""
    cases = [
        ("Fresh Paneer 200 g", "grocery", "pantry_eligible", True),
        ("Patiala Kadai Paneer", "restaurant", "prepared_meal", False),
        ("Paneer Butter Masala", "grocery", "prepared_meal", False),
        ("Basmati Rice 5 kg", "grocery", "pantry_eligible", True),
        ("Cooked Rice Meal", "restaurant", "prepared_meal", False),
        ("Idli Dosa Batter 1 kg", "grocery", "pantry_eligible", True),
        ("Masala Dosa", "restaurant", "prepared_meal", False),
        ("Marie Biscuits 200 g", "grocery", "pantry_eligible", True),
        ("Get-A-Way Belgian Choco Chunk Ice Cream 100 ml", "grocery", "pantry_eligible", True),
        ("Dabur Almond Hair Oil 290 ml", "grocery", "non_food", False),
        ("Fortune Soya Health Refined Soyabean Oil 1 L Pouch", "grocery", "pantry_eligible", True),
        ("Happy Mail Matchbox 1 pack (5 pcs)", "grocery", "non_food", False),
        ("serbher utensil scrubber 15gm", "grocery", "non_food", False),
        ("Qwertyzx Unknown Mystery Pack", "grocery", "ambiguous_review", False),
    ]
    for text, rcat, expected_status, expected_pantry in cases:
        res = FoodClassificationService.classify_item(text, receipt_category=rcat)
        assert res.classification_status == expected_status, f"{text}: got {res.classification_status}"
        assert res.pantry_eligible is expected_pantry, f"{text}: got pantry={res.pantry_eligible}"


@pytest.mark.asyncio
async def test_user_correction_creates_household_scoped_alias(
    client: AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    """User correcting a receipt draft line stores a household-scoped alias without leaking to other households."""
    tok_a = jwks_provider.make_token(sub="user_alias_a", email="a@example.com", name="A")
    tok_b = jwks_provider.make_token(sub="user_alias_b", email="b@example.com", name="B")
    h_a = {"Authorization": f"Bearer {tok_a}"}
    h_b = {"Authorization": f"Bearer {tok_b}"}
    assert (await client.get("/api/v1/auth/me", headers=h_a)).status_code == 200
    assert (await client.get("/api/v1/auth/me", headers=h_b)).status_code == 200

    cat_a = await client.post(
        "/api/v1/catalog/items",
        headers=h_a,
        json={"name": "Toor Dal", "category": "pulse", "default_base_unit": "g"},
    )
    assert cat_a.status_code == 201
    cat_a_id = cat_a.json()["id"]

    draft_resp = await client.post(
        "/api/v1/purchases/ocr",
        headers=h_a,
        json={"raw_text": "Fresh Market\nXyz Special Pulse 500g ₹85.00\nGrand Total ₹85.00"},
    )
    assert draft_resp.status_code == 201
    draft = draft_resp.json()
    item_id = draft["items"][0]["id"]

    # User corrects display_name to Toor Dal
    patch_resp = await client.patch(
        f"/api/v1/purchases/drafts/{draft['id']}",
        headers=h_a,
        json={
            "items": [
                {
                    "id": item_id,
                    "canonical_item_id": cat_a_id,
                    "display_name": "Toor Dal",
                    "qty": 500,
                    "unit": "g",
                    "unit_price_paise": 8500,
                    "line_total_paise": 8500,
                    "track_inventory": True,
                }
            ]
        },
    )
    assert patch_resp.status_code == 200

    # Re-uploading same raw text in Household A resolves via user_alias; Household B does NOT inherit A's alias
    draft_a2 = await client.post(
        "/api/v1/purchases/ocr",
        headers=h_a,
        json={"raw_text": "Fresh Market\nXyz Special Pulse 500g ₹85.00\nGrand Total ₹85.00"},
    )
    assert draft_a2.status_code == 201
    assert draft_a2.json()["items"][0]["matching_method"] == "user_alias"

    draft_b = await client.post(
        "/api/v1/purchases/ocr",
        headers=h_b,
        json={"raw_text": "Fresh Market\nXyz Special Pulse 500g ₹85.00\nGrand Total ₹85.00"},
    )
    assert draft_b.status_code == 201
    assert draft_b.json()["items"][0]["matching_method"] != "user_alias"


@pytest.mark.asyncio
async def test_mixed_food_nonfood_and_restaurant_receipt_confirmation(
    client: AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    """Mixed receipt with rice, milk, hair oil, matchbox, prepared biryani, and biscuits:
    only rice, milk, and biscuits enter pantry; full purchase total is preserved;
    only biryani creates outside-food consumption."""
    tok = jwks_provider.make_token(sub="user_mixed_cls", email="mixed@example.com", name="Mixed")
    headers = {"Authorization": f"Bearer {tok}"}
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 200

    raw_receipt = (
        "City Supermart\n"
        "Basmati Rice 1 kg 120.00\n"
        "Whole Milk 1 L 65.00\n"
        "Dabur Almond Hair Oil 290 ml 180.00\n"
        "Happy Mail Matchbox 1 pack 10.00\n"
        "Veg Biryani 250.00\n"
        "Marie Biscuits 200 g 35.00\n"
        "Grand Total 660.00\n"
    )
    draft_resp = await client.post(
        "/api/v1/purchases/ocr",
        headers=headers,
        json={"raw_text": raw_receipt},
    )
    assert draft_resp.status_code == 201
    draft = draft_resp.json()
    by_name = {it["display_name"]: it for it in draft["items"]}
    assert by_name["Basmati Rice"]["classification_status"] == "pantry_eligible"
    assert by_name["Whole Milk"]["classification_status"] == "pantry_eligible"
    assert by_name["Dabur Almond Hair Oil"]["classification_status"] == "non_food"
    assert by_name["Happy Mail Matchbox"]["classification_status"] == "non_food"
    assert by_name["Veg Biryani"]["classification_status"] == "prepared_meal"
    assert by_name["Marie Biscuits"]["classification_status"] == "pantry_eligible"

    conf_resp = await client.post(
        f"/api/v1/purchases/{draft['id']}/confirm",
        headers=headers,
        json={"meal_type": "lunch", "dining_mode": "takeaway"},
    )
    assert conf_resp.status_code == 200
    confirmed = conf_resp.json()
    assert len(confirmed["created_lots"]) == 3  # Basmati Rice, Whole Milk, Marie Biscuits only
    assert confirmed["food_consumption"] is not None
    fc_names = [x["name"] for x in confirmed["food_consumption"]["items_summary"]]
    assert fc_names == ["Veg Biryani"]

    lots = (await client.get("/api/v1/inventory/lots", headers=headers)).json()["items"]
    lot_names = {lot["item_name"] for lot in lots}
    assert len(lots) == 3
    assert not any("Hair Oil" in n or "Matchbox" in n or "Biryani" in n for n in lot_names)

    # Full purchase total (Rs 660.00 = 66000 paise) is preserved in purchase record
    purchase_detail = (await client.get(f"/api/v1/purchases/{draft['id']}", headers=headers)).json()
    assert purchase_detail["total_amount_paise"] == 66000
