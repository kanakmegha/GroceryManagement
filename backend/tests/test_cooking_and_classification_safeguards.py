from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from app.infra.llm.hf_provider import HuggingFaceRecipeProvider
from app.modules.catalog.classifier import FoodClassificationService
from tests.conftest import EphemeralJWKSProvider


@pytest.mark.asyncio
async def test_cooking_completion_fefo_waste_omission_duplicate_lines_and_safeguards(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    """Verify duplicate canonical lines, multi-lot FEFO, actual+waste deduction, omission, cancel, and duplicate completion."""
    token = jwks_provider.make_token(sub="user_cook_safe", email="cook@example.com", name="Cook")
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 200

    # Create Spinach (200g across 2 FEFO lots: 80g expiring sooner, 120g expiring later) and Oil (1000ml)
    spinach_cat = await client.post(
        "/api/v1/catalog/items",
        headers=headers,
        json={"name": "Spinach", "category": "produce", "default_base_unit": "g"},
    )
    assert spinach_cat.status_code == 201
    spinach_id = spinach_cat.json()["id"]

    oil_cat = await client.post(
        "/api/v1/catalog/items",
        headers=headers,
        json={"name": "Fortune Soya Health Refined Soyabean Oil", "category": "oils", "default_base_unit": "ml"},
    )
    assert oil_cat.status_code == 201
    oil_id = oil_cat.json()["id"]

    today = datetime.now(UTC).date()
    lot_soon = await client.post(
        "/api/v1/inventory/lots",
        headers=headers,
        json={
            "canonical_item_id": spinach_id,
            "remaining_qty": "80.000",
            "base_unit": "g",
            "expires_on": (today + timedelta(days=1)).isoformat(),
        },
    )
    lot_later = await client.post(
        "/api/v1/inventory/lots",
        headers=headers,
        json={
            "canonical_item_id": spinach_id,
            "remaining_qty": "120.000",
            "base_unit": "g",
            "expires_on": (today + timedelta(days=7)).isoformat(),
        },
    )
    lot_oil = await client.post(
        "/api/v1/inventory/lots",
        headers=headers,
        json={
            "canonical_item_id": oil_id,
            "remaining_qty": "1000.000",
            "base_unit": "ml",
            "expires_on": (today + timedelta(days=90)).isoformat(),
        },
    )
    assert lot_soon.status_code == 201 and lot_later.status_code == 201 and lot_oil.status_code == 201
    lot_soon_id = lot_soon.json()["id"]
    lot_later_id = lot_later.json()["id"]

    # Create a plan to cancel -> must cause ZERO mutation
    cancel_plan = await client.post(
        "/api/v1/cooking-plans",
        headers=headers,
        json={
            "recipe_title": "Cancelled Spinach Dish",
            "planned_servings": 2,
            "ingredients": [
                {"canonical_item_id": spinach_id, "name": "Spinach", "planned_qty": "50.000", "base_unit": "g"}
            ],
        },
    )
    assert cancel_plan.status_code == 201
    cancel_res = await client.post(
        f"/api/v1/cooking-plans/{cancel_plan.json()['id']}/cancel",
        headers=headers,
    )
    assert cancel_res.status_code == 200
    assert cancel_res.json()["status"] == "cancelled"

    # Verify stock untouched after cancel
    lots_check = (await client.get("/api/v1/inventory/lots", headers=headers)).json()["items"]
    assert sum(Decimal(l["remaining_qty"]) for l in lots_check if l["canonical_item_id"] == spinach_id) == Decimal("200.000")

    # Create a plan with TWO lines sharing the SAME canonical_item_id (Spinach) + Oil line
    plan_res = await client.post(
        "/api/v1/cooking-plans",
        headers=headers,
        json={
            "recipe_title": "Palak Tadka",
            "planned_servings": 2,
            "ingredients": [
                {
                    "line_id": "line-spinach-base",
                    "canonical_item_id": spinach_id,
                    "name": "Spinach (base)",
                    "planned_qty": "60.000",
                    "base_unit": "g",
                },
                {
                    "line_id": "line-spinach-garnish",
                    "canonical_item_id": spinach_id,
                    "name": "Spinach (garnish)",
                    "planned_qty": "40.000",
                    "base_unit": "g",
                },
                {
                    "line_id": "line-oil",
                    "canonical_item_id": oil_id,
                    "name": "Soyabean Oil",
                    "planned_qty": "100.000",
                    "base_unit": "ml",
                },
            ],
        },
    )
    assert plan_res.status_code == 201
    plan = plan_res.json()
    assert len({ing["line_id"] for ing in plan["ingredients"]}) == 3

    # Attempting to deduct > available stock (250g > 200g) must fail 409 with zero partial deduction
    fail_complete = await client.post(
        f"/api/v1/cooking-plans/{plan['id']}/complete",
        headers=headers,
        json={
            "items": [
                {"line_id": "line-spinach-base", "canonical_item_id": spinach_id, "actual_used_qty": "250.000"},
                {"line_id": "line-spinach-garnish", "canonical_item_id": spinach_id, "actual_used_qty": "0.000", "omitted": True},
                {"line_id": "line-oil", "canonical_item_id": oil_id, "actual_used_qty": "100.000"},
            ]
        },
    )
    assert fail_complete.status_code == 409
    assert fail_complete.json()["error"]["code"] == "INSUFFICIENT_STOCK"

    # Complete with:
    # - line-spinach-base: 80g used + 10g waste = 90g deducted (depletes 80g soon lot + 10g from later lot -> 110g left)
    # - line-spinach-garnish: omitted -> 0 deducted
    # - line-oil: 100ml used + 0ml waste -> 900ml left
    ok_complete = await client.post(
        f"/api/v1/cooking-plans/{plan['id']}/complete",
        headers=headers,
        json={
            "items": [
                {
                    "line_id": "line-spinach-base",
                    "canonical_item_id": spinach_id,
                    "actual_used_qty": "80.000",
                    "waste_qty": "10.000",
                },
                {
                    "line_id": "line-spinach-garnish",
                    "canonical_item_id": spinach_id,
                    "actual_used_qty": "40.000",
                    "waste_qty": "5.000",
                    "omitted": True,
                },
                {
                    "line_id": "line-oil",
                    "canonical_item_id": oil_id,
                    "actual_used_qty": "100.000",
                    "waste_qty": "0.000",
                },
            ]
        },
    )
    assert ok_complete.status_code == 200
    completed = ok_complete.json()
    assert completed["status"] == "completed"

    # Duplicate completion must be prevented (409)
    dup_complete = await client.post(
        f"/api/v1/cooking-plans/{plan['id']}/complete",
        headers=headers,
        json={"items": []},
    )
    assert dup_complete.status_code == 409

    # Verify FEFO remaining quantities
    lots_active = {l["id"]: l for l in (await client.get("/api/v1/inventory/lots", headers=headers)).json()["items"]}
    assert lot_soon_id not in lots_active
    assert Decimal(lots_active[lot_later_id]["remaining_qty"]) == Decimal("110.000")
    assert Decimal(lots_active[lot_oil.json()["id"]]["remaining_qty"]) == Decimal("900.000")


def test_required_classification_pairs() -> None:
    """Verify raw paneer vs curry, cooking oil vs hair oil, matchbox, scrubber, biscuits, RTE, restaurant meals."""
    assert FoodClassificationService.classify_item("Fresh Paneer 200g").pantry_eligible is True
    curry = FoodClassificationService.classify_item("Paneer Butter Masala")
    assert curry.pantry_eligible is False and curry.classification_status == "prepared_meal"

    assert FoodClassificationService.classify_item("Fortune Soyabean Oil 1L").pantry_eligible is True
    hair_oil = FoodClassificationService.classify_item("Dabur Almond Hair Oil 200ml")
    assert hair_oil.pantry_eligible is False and hair_oil.classification_status == "non_food"

    matchbox = FoodClassificationService.classify_item("Happy Mail Matchbox")
    assert matchbox.pantry_eligible is False and matchbox.classification_status == "non_food"

    scrubber = FoodClassificationService.classify_item("Scotch Brite Utensil Scrubber")
    assert scrubber.pantry_eligible is False and scrubber.classification_status == "non_food"

    biscuits = FoodClassificationService.classify_item("Britannia Good Day Biscuits 100g")
    assert biscuits.pantry_eligible is True

    rte = FoodClassificationService.classify_item("MTR Ready To Eat Rajma Masala 300g")
    assert rte.pantry_eligible is True

    rest = FoodClassificationService.classify_item("Masala Dosa", receipt_category="restaurant")
    assert rest.pantry_eligible is False and rest.classification_status == "prepared_meal"


@pytest.mark.asyncio
async def test_non_food_cannot_enter_pantry_via_manual_patch_or_confirm_and_llm_fallback(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify non-food items are blocked across manual create, draft PATCH override, and confirm, and test HF LLM fallback."""
    token = jwks_provider.make_token(sub="user_cls_safe", email="cls@example.com", name="Safe")
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 200

    # 1. Direct manual purchase API attempting track_inventory=True on non-food items
    manual = await client.post(
        "/api/v1/purchases",
        headers=headers,
        json={
            "merchant_name": "Local Kirana",
            "purchased_on": "2026-11-19",
            "receipt_category": "grocery",
            "items": [
                {
                    "display_name": "Happy Mail Matchbox",
                    "category": "grocery",
                    "qty": 2,
                    "unit": "pcs",
                    "unit_price_paise": 500,
                    "line_total_paise": 1000,
                    "track_inventory": True,
                },
                {
                    "display_name": "Dabur Almond Hair Oil 200ml",
                    "category": "oils",
                    "qty": 200,
                    "unit": "ml",
                    "unit_price_paise": 12000,
                    "line_total_paise": 12000,
                    "track_inventory": True,
                },
                {
                    "display_name": "Utensil Scrubber",
                    "category": "grocery",
                    "qty": 1,
                    "unit": "pcs",
                    "unit_price_paise": 2500,
                    "line_total_paise": 2500,
                    "track_inventory": True,
                },
                {
                    "display_name": "Fortune Soyabean Oil 1L",
                    "category": "oils",
                    "qty": 1000,
                    "unit": "ml",
                    "unit_price_paise": 15000,
                    "line_total_paise": 15000,
                    "track_inventory": True,
                },
            ],
        },
    )
    assert manual.status_code == 201
    draft = manual.json()
    by_name = {it["display_name"]: it for it in draft["items"]}
    assert by_name["Happy Mail Matchbox"]["track_inventory"] is False
    assert by_name["Dabur Almond Hair Oil 200ml"]["track_inventory"] is False
    assert by_name["Utensil Scrubber"]["track_inventory"] is False
    assert by_name["Fortune Soyabean Oil 1L"]["track_inventory"] is True

    # 2. Mobile / API PATCH attempt to force track_inventory=True on non-food
    patch_res = await client.patch(
        f"/api/v1/purchases/drafts/{draft['id']}",
        headers=headers,
        json={
            "items": [
                {
                    "id": it["id"],
                    "display_name": it["display_name"],
                    "category": "grocery",
                    "normalized_qty": it["normalized_qty"],
                    "base_unit": it["base_unit"],
                    "unit_price_paise": it["unit_price_paise"],
                    "line_total_paise": it["line_total_paise"],
                    "track_inventory": True,
                }
                for it in draft["items"]
            ]
        },
    )
    assert patch_res.status_code == 200
    patched_by_name = {it["display_name"]: it for it in patch_res.json()["items"]}
    assert patched_by_name["Happy Mail Matchbox"]["track_inventory"] is False
    assert patched_by_name["Dabur Almond Hair Oil 200ml"]["track_inventory"] is False
    assert patched_by_name["Utensil Scrubber"]["track_inventory"] is False

    # 3. Confirm purchase -> ONLY Fortune Soyabean Oil enters active pantry; non-food stays in purchase accounting
    conf = await client.post(f"/api/v1/purchases/{draft['id']}/confirm", headers=headers)
    assert conf.status_code == 200
    assert len(conf.json()["created_lots"]) == 1
    lots = (await client.get("/api/v1/inventory/lots", headers=headers)).json()["items"]
    assert len(lots) == 1
    assert "soyabean oil" in lots[0]["item_name"].lower()

    # 4. Hybrid HF LLM classification for ambiguous/unfamiliar receipt item
    called = {"count": 0}

    async def fake_classify_receipt_item(
        self: HuggingFaceRecipeProvider,
        *,
        raw_name: str,
        receipt_category: str = "grocery",
        merchant_name: str | None = None,
    ):
        called["count"] += 1
        from app.infra.llm.hf_provider import ReceiptItemLLMClassification

        return ReceiptItemLLMClassification(
            original_name=raw_name,
            normalized_name="herbal dishwash gel",
            category="household",
            food_state="non_food",
            pantry_eligible=False,
            accounting_treatment="household_non_food",
            confidence=0.92,
            explanation="Dishwashing liquid is a non-food cleaning supply.",
            needs_review=False,
        )

    monkeypatch.setattr(HuggingFaceRecipeProvider, "classify_receipt_item", fake_classify_receipt_item)
    ocr_res = await client.post(
        "/api/v1/purchases/ocr",
        headers=headers,
        json={"raw_text": "SuperMart\nZyx Herbal Gel 500ml ₹99.00\nGrand Total ₹99.00"},
    )
    assert ocr_res.status_code == 201
    assert called["count"] == 1
    assert ocr_res.json()["items"][0]["track_inventory"] is False
    assert ocr_res.json()["items"][0]["matching_method"] == "hf_llm"


@pytest.mark.asyncio
async def test_legacy_cooking_plan_without_line_id_backward_compatible(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Old cooking plans without persisted line_id must synthesize unique line_ids and deduct independently."""
    from uuid import UUID
    from sqlalchemy import select
    from app.modules.cooking.models import CookingPlan

    token = jwks_provider.make_token(sub="user_legacy_plan", email="leg@example.com", name="Legacy")
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 200

    cat = await client.post(
        "/api/v1/catalog/items",
        headers=headers,
        json={"name": "Spinach", "category": "produce", "default_base_unit": "g"},
    )
    assert cat.status_code == 201
    cid = cat.json()["id"]
    lot = await client.post(
        "/api/v1/inventory/lots",
        headers=headers,
        json={"canonical_item_id": cid, "remaining_qty": "200.000", "base_unit": "g"},
    )
    assert lot.status_code == 201

    # Create a plan normally, then strip line_id via _to_plan_out / service check:
    # We simulate an old DB row by calling create and verifying _to_plan_out synthesizes line_ids
    plan_res = await client.post(
        "/api/v1/cooking-plans",
        headers=headers,
        json={
            "recipe_title": "Legacy Palak",
            "planned_servings": 2,
            "ingredients": [
                {"canonical_item_id": cid, "name": "Spinach A", "planned_qty": "50.000", "base_unit": "g"},
                {"canonical_item_id": cid, "name": "Spinach B", "planned_qty": "30.000", "base_unit": "g"},
            ],
        },
    )
    assert plan_res.status_code == 201
    plan_id = plan_res.json()["id"]

    # Simulate a pre-migration legacy DB row without line_id and verify _to_plan_out synthesizes unique line_ids
    from app.modules.cooking import service as cooking_service

    legacy_plan = CookingPlan(
        id=UUID(plan_id),
        household_id=UUID((await client.get("/api/v1/auth/me", headers=headers)).json()["active_household"]["id"]),
        created_by=UUID((await client.get("/api/v1/auth/me", headers=headers)).json()["user"]["id"]),
        recipe_title="Legacy Palak",
        status="planned",
        planned_servings=2,
        started_at=datetime.now(UTC),
        ingredients=[
            {"canonical_item_id": cid, "name": "Spinach A", "planned_qty": "50.000", "base_unit": "g"},
            {"canonical_item_id": cid, "name": "Spinach B", "planned_qty": "30.000", "base_unit": "g"},
        ],
    )
    synthesized = cooking_service._to_plan_out(legacy_plan)
    assert len(synthesized.items) == 2
    assert synthesized.items[0]["line_id"] != synthesized.items[1]["line_id"]
    assert synthesized.items[0]["line_id"] == f"{plan_id}:{cid}:0"
    assert synthesized.items[1]["line_id"] == f"{plan_id}:{cid}:1"

    # Complete the plan using synthesized line_ids: line 0 = 60g+10g waste, line 1 = omitted
    comp = await client.post(
        f"/api/v1/cooking-plans/{plan_id}/complete",
        headers=headers,
        json={
            "items": [
                {
                    "line_id": plan_res.json()["items"][0]["line_id"],
                    "canonical_item_id": cid,
                    "actual_used_qty": "60.000",
                    "waste_qty": "10.000",
                },
                {
                    "line_id": plan_res.json()["items"][1]["line_id"],
                    "canonical_item_id": cid,
                    "actual_used_qty": "30.000",
                    "omitted": True,
                },
            ]
        },
    )
    assert comp.status_code == 200
    lots_after = (await client.get("/api/v1/inventory/lots", headers=headers)).json()["items"]
    assert Decimal(lots_after[0]["remaining_qty"]) == Decimal("130.000")


@pytest.mark.asyncio
async def test_hf_receipt_classification_failure_modes_and_low_confidence_safety(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify HTTP 429, 5xx, ReadTimeout, invalid JSON, missing fields, low confidence, and unknown state stay pending review."""
    import json as _json
    from app.modules.catalog.classifier import classify_with_household_db

    # 1. Provider-level failure modes via MockTransport
    failure_transports = [
        httpx.MockTransport(lambda req: httpx.Response(429, json={"error": "rate limited"})),
        httpx.MockTransport(lambda req: httpx.Response(503, json={"error": "upstream unavailable"})),
        httpx.MockTransport(lambda req: (_ for _ in ()).throw(httpx.ReadTimeout("timed out", request=req))),
        httpx.MockTransport(lambda req: httpx.Response(200, json={"choices": [{"message": {"content": "NOT_JSON"}}]})),
        httpx.MockTransport(lambda req: httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})),
    ]
    for tr in failure_transports:
        prov = HuggingFaceRecipeProvider(hf_token="hf_test_dummy", transport=tr)
        res = await prov.classify_receipt_item(raw_name="Mystery Pack 250g", receipt_category="grocery")
        assert res is None

    # 2. Low confidence & unknown food_state returned by LLM must STILL be forced to requires_user_review
    token = jwks_provider.make_token(sub="user_hf_fail_safe", email="hf@example.com", name="HF Safe")
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 200

    async def low_conf_llm(self, *, raw_name: str, receipt_category: str = "grocery", merchant_name: str | None = None):
        from app.infra.llm.hf_provider import ReceiptItemLLMClassification

        return ReceiptItemLLMClassification(
            original_name=raw_name,
            normalized_name="mystery pack",
            category="other",
            food_state="packaged_edible",
            pantry_eligible=True,
            accounting_treatment="pantry_inventory",
            confidence=0.42,
            explanation="Low confidence guess.",
            needs_review=False,
        )

    monkeypatch.setattr(HuggingFaceRecipeProvider, "classify_receipt_item", low_conf_llm)
    ocr_low = await client.post(
        "/api/v1/purchases/ocr",
        headers=headers,
        json={"raw_text": "Local Store\nMystery Xyz Pack 250g ₹50.00\nGrand Total ₹50.00"},
    )
    assert ocr_low.status_code == 201
    item_low = ocr_low.json()["items"][0]
    assert item_low["track_inventory"] is False
    assert item_low["budget_treatment"] == "requires_user_review"

    async def unknown_state_llm(self, *, raw_name: str, receipt_category: str = "grocery", merchant_name: str | None = None):
        from app.infra.llm.hf_provider import ReceiptItemLLMClassification

        return ReceiptItemLLMClassification(
            original_name=raw_name,
            normalized_name="unknown item",
            category="other",
            food_state="unknown",
            pantry_eligible=True,
            accounting_treatment="pantry_inventory",
            confidence=0.90,
            explanation="Unknown state.",
            needs_review=False,
        )

    monkeypatch.setattr(HuggingFaceRecipeProvider, "classify_receipt_item", unknown_state_llm)
    ocr_unk = await client.post(
        "/api/v1/purchases/ocr",
        headers=headers,
        json={"raw_text": "Local Store\nQrs Zyx Pack 100g ₹40.00\nGrand Total ₹40.00"},
    )
    assert ocr_unk.status_code == 201
    item_unk = ocr_unk.json()["items"][0]
    assert item_unk["track_inventory"] is False
    assert item_unk["budget_treatment"] == "requires_user_review"

    # Confirming unresolved review items must NOT create pantry lots
    conf = await client.post(f"/api/v1/purchases/{ocr_unk.json()['id']}/confirm", headers=headers)
    assert conf.status_code == 200
    assert conf.json()["created_lots"] == []


@pytest.mark.asyncio
async def test_unit_mismatch_milk_chocolate_collision_and_missing_ingredient_resolutions(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    """Reproduce Part 7 scenarios: Milk vs Amul Milk Chocolate, 1 pcs Ice Cream vs 50 g, empty plan name enrichment, and I have it / I bought it."""
    from app.modules.recipes.grounding import reconcile_ingredient

    inv = [
        {
            "canonical_item_id": "ee0cd354-ae18-41fe-bd30-cd7b8177ae22",
            "name": "Amul Milk Chocolate- Smooth & Creamy 150 g Carton",
            "qty": 1.0,
            "base_unit": "pcs",
        },
        {
            "canonical_item_id": "12cbf50e-d284-423f-9299-4627851373f7",
            "name": "Get-A-Way Belgian Choco Chunk High Protein Ice Cream Cup 100 ml",
            "qty": 1.0,
            "base_unit": "pcs",
        },
    ]

    # 1. 'Milk 500 ml' must NOT match 'Amul Milk Chocolate'
    milk_rec = reconcile_ingredient({"name": "Milk", "quantity": "500 ml"}, inv)
    assert milk_rec is not None
    assert milk_rec["status"] == "missing"
    assert milk_rec["canonical_item_id"] is None

    # 2. 'Get-A-Way Ice Cream 50 g' vs 1 pcs in pantry -> unit mismatch (partial, not available!)
    ice_rec = reconcile_ingredient({"name": "Get-A-Way Ice Cream", "quantity": "50 g"}, inv)
    assert ice_rec is not None
    assert ice_rec["status"] == "partial"
    assert ice_rec["missing_qty"] == "50"

    # 3. Empty ingredient name in cooking_plans is enriched from CanonicalItem
    token = jwks_provider.make_token(sub="user_part7_res", email="p7@example.com", name="Part7")
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 200

    banana_cat = await client.post(
        "/api/v1/catalog/items",
        headers=headers,
        json={"name": "Robusta Banana", "category": "produce", "default_base_unit": "pcs"},
    )
    assert banana_cat.status_code == 201
    banana_id = banana_cat.json()["id"]

    plan = await client.post(
        "/api/v1/cooking-plans",
        headers=headers,
        json={
            "recipe_title": "Banana Pudding",
            "planned_servings": 2,
            "ingredients": [
                {"canonical_item_id": banana_id, "planned_qty": "1", "base_unit": "pcs"},
            ],
        },
    )
    assert plan.status_code == 201
    fetched = await client.get(f"/api/v1/cooking-plans/{plan.json()['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["items"][0]["name"] == "Robusta Banana"

    # 4. 'I have it' (auditable ADJUSTMENT_IN) and 'I bought it' (manual purchase + confirm)
    lot_res = await client.post(
        "/api/v1/inventory/lots",
        headers=headers,
        json={"canonical_item_id": banana_id, "remaining_qty": "1.000", "base_unit": "pcs"},
    )
    assert lot_res.status_code == 201
    adj = await client.post(
        f"/api/v1/inventory/lots/{lot_res.json()['id']}/adjust",
        headers=headers,
        json={"event_type": "ADJUSTMENT_IN", "quantity_delta": "2.000", "notes": "I have it"},
    )
    assert adj.status_code == 200

    pur = await client.post(
        "/api/v1/purchases",
        headers=headers,
        json={
            "merchant_name": "FreshMart",
            "purchased_on": "2026-11-19",
            "receipt_category": "grocery",
            "items": [
                {
                    "display_name": "Whole Milk 500ml",
                    "category": "dairy",
                    "qty": 500,
                    "unit": "ml",
                    "unit_price_paise": 3000,
                    "line_total_paise": 3000,
                    "track_inventory": True,
                }
            ],
        },
    )
    assert pur.status_code == 201
    conf_pur = await client.post(f"/api/v1/purchases/{pur.json()['id']}/confirm", headers=headers)
    assert conf_pur.status_code == 200
    assert len(conf_pur.json()["created_lots"]) == 1
    lots_all = (await client.get("/api/v1/inventory/lots", headers=headers)).json()["items"]
    assert any(l["canonical_item_id"] == banana_id and Decimal(l["remaining_qty"]) == Decimal("3.000") for l in lots_all)
    assert any("milk" in l["item_name"].lower() and Decimal(l["remaining_qty"]) == Decimal("500.000") for l in lots_all)


