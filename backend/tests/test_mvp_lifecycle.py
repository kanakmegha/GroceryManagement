from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from app.core.money import decimal_inr_to_paise, paise_to_inr_display
from app.core.units import normalize_to_base_unit
from app.modules.purchases import service as purchases_service
from app.modules.recipes import service as recipes_service
from tests.conftest import EphemeralJWKSProvider, assert_error_envelope


def test_money_and_unit_helpers() -> None:
    assert decimal_inr_to_paise("54.00") == 5400
    assert decimal_inr_to_paise(Decimal("12.345")) == 1235
    assert paise_to_inr_display(5400) == "₹54.00"
    assert paise_to_inr_display(5400, include_symbol=False) == "54.00"

    assert normalize_to_base_unit(1.5, "kg") == (Decimal("1500.000"), "g")
    assert normalize_to_base_unit("0.5", "l") == (Decimal("500.000"), "ml")
    assert normalize_to_base_unit(2, "dozen") == (Decimal("24.000"), "pcs")

    with pytest.raises(ValueError):
        decimal_inr_to_paise("-1.00")
    with pytest.raises(ValueError):
        normalize_to_base_unit(1, "bushel")


async def test_mvp_full_lifecycle_and_isolation(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_a = jwks_provider.make_token(sub="user_alice", email="alice@example.com", name="Alice")
    token_b = jwks_provider.make_token(sub="user_bob", email="bob@example.com", name="Bob")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # Bootstrap Alice & Bob households
    me_a = await client.get("/api/v1/auth/me", headers=headers_a)
    assert me_a.status_code == 200
    hh_a = me_a.json()["active_household"]["id"]

    me_b = await client.get("/api/v1/auth/me", headers=headers_b)
    assert me_b.status_code == 200

    # 1. Empty inventory -> recommendations must return empty_inventory=True, items=[]
    empty_rec = await client.get("/api/v1/recipes/recommendations", headers=headers_a)
    assert empty_rec.status_code == 200
    assert empty_rec.json()["empty_inventory"] is True
    assert empty_rec.json()["items"] == []

    # 2. OCR draft NEVER mutates inventory_lots or stock_ledger
    today = datetime.now(UTC).date()
    soon_exp = (today + timedelta(days=2)).isoformat()
    later_exp = (today + timedelta(days=10)).isoformat()

    async def fake_extract_receipt(filename: str, content_type: str, data: bytes) -> dict:
        return {
            "adapter": "stub_ocr",
            "merchant_name": "BigBasket",
            "purchased_on": today.isoformat(),
            "items": [
                {
                    "raw_text": "AMUL TAAZA 500ML",
                    "display_name": "Whole Milk",
                    "category": "dairy",
                    "qty": 500,
                    "unit": "ml",
                    "line_total_paise": 3000,
                    "expires_on": soon_exp,
                }
            ],
        }

    monkeypatch.setattr(purchases_service, "_get_extract_receipt", lambda: fake_extract_receipt)

    ocr_resp = await client.post(
        "/api/v1/purchases/ocr",
        headers=headers_a,
        json={"raw_text": "AMUL TAAZA 500ML 30.00"},
    )
    assert ocr_resp.status_code == 201
    draft = ocr_resp.json()
    assert draft["status"] == "draft"
    assert draft["total_amount_paise"] == 3000
    assert len(draft["items"]) == 1

    # Verify inventory & ledger are still empty
    assert (await client.get("/api/v1/inventory/summary", headers=headers_a)).json()["items"] == []
    assert (await client.get("/api/v1/inventory/lots", headers=headers_a)).json()["items"] == []
    assert (await client.get("/api/v1/inventory/ledger", headers=headers_a)).json()["items"] == []

    # 3. Confirm draft -> creates inventory_lots + stock_ledger (PURCHASE_IN)
    confirm_resp = await client.post(
        f"/api/v1/purchases/{draft['id']}/confirm",
        headers=headers_a,
    )
    assert confirm_resp.status_code == 200
    confirmed = confirm_resp.json()
    assert confirmed["status"] == "confirmed"
    assert len(confirmed["created_lots"]) == 1
    milk_id = confirmed["created_lots"][0]["canonical_item_id"]
    lot_1_id = confirmed["created_lots"][0]["lot_id"]

    # Confirming twice -> 409 DRAFT_ALREADY_CONFIRMED
    reconfirm_resp = await client.post(
        f"/api/v1/purchases/{draft['id']}/confirm",
        headers=headers_a,
    )
    assert_error_envelope(reconfirm_resp, 409, "DRAFT_ALREADY_CONFIRMED")

    # Add a second lot of Whole Milk expiring later (manual purchase + confirm)
    manual_resp = await client.post(
        "/api/v1/purchases",
        headers=headers_a,
        json={
            "merchant_name": "Local Dairy",
            "purchased_on": today.isoformat(),
            "items": [
                {
                    "display_name": "Whole Milk",
                    "category": "dairy",
                    "qty": "1",
                    "unit": "l",
                    "line_total_paise": 6000,
                    "expires_on": later_exp,
                }
            ],
        },
    )
    assert manual_resp.status_code == 201
    lot_2_confirm = await client.post(
        f"/api/v1/purchases/{manual_resp.json()['id']}/confirm",
        headers=headers_a,
    )
    assert lot_2_confirm.status_code == 200
    lot_2_id = lot_2_confirm.json()["created_lots"][0]["lot_id"]

    # 4. Inventory search, expiring filter, adjust & waste
    search_resp = await client.get("/api/v1/inventory/summary?q=milk", headers=headers_a)
    assert search_resp.status_code == 200
    summary_items = search_resp.json()["items"]
    assert len(summary_items) == 1
    assert Decimal(summary_items[0]["total_remaining_qty"]) == Decimal("1500.000")

    expiring_resp = await client.get(
        "/api/v1/inventory/lots?expiring_within_days=3", headers=headers_a
    )
    assert expiring_resp.status_code == 200
    assert len(expiring_resp.json()["items"]) == 1
    assert expiring_resp.json()["items"][0]["id"] == lot_1_id

    # Waste 100ml from lot_1 (remaining in lot_1 becomes 400ml)
    waste_resp = await client.post(
        f"/api/v1/inventory/lots/{lot_1_id}/adjust",
        headers=headers_a,
        json={"event_type": "WASTE_OUT", "quantity_delta": "-100.000", "notes": "Spilled"},
    )
    assert waste_resp.status_code == 200
    assert Decimal(waste_resp.json()["lot"]["remaining_qty"]) == Decimal("400.000")

    # 5. Non-empty inventory -> recommendations calls HF provider + reference discovery
    class FakeHFProvider:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def recommend_recipes(
            self, inventory_items: list[dict], limit: int = 10
        ) -> list[dict]:
            return [
                {
                    "recipe_id": "rec-milk-tea",
                    "title": "Masala Chai",
                    "servings": 2,
                    "prep_minutes": 10,
                    "ingredients": [
                        {
                            "name": inventory_items[0]["name"],
                            "required_qty": 250,
                            "base_unit": "ml",
                        }
                    ],
                    "instructions": ["Boil milk with tea leaves."],
                }
            ][:limit]

    async def fake_discover_refs(query: str, limit: int = 3) -> list[dict]:
        return [{"title": f"{query} Guide", "url": "https://example.com/chai", "source": "web"}][
            :limit
        ]

    monkeypatch.setattr(recipes_service, "_get_hf_provider_cls", lambda: FakeHFProvider)
    monkeypatch.setattr(recipes_service, "_get_discover_references_fn", lambda: fake_discover_refs)

    rec_resp = await client.get("/api/v1/recipes/recommendations?mode=refined", headers=headers_a)
    assert rec_resp.status_code == 200
    rec_body = rec_resp.json()
    assert rec_body["empty_inventory"] is False
    assert len(rec_body["items"]) == 1
    assert rec_body["items"][0]["title"] == "Masala Chai"
    assert len(rec_body["items"][0]["references"]) == 1

    # 6. Cooking plans: start = 0 deduction; cancel = 0 deduction; complete = FEFO deduction
    plan_payload = {
        "recipe_id": "rec-milk-tea",
        "recipe_title": "Masala Chai",
        "planned_servings": 2,
        "ingredients": [
            {
                "canonical_item_id": milk_id,
                "name": "Whole Milk",
                "planned_qty": "600.000",
                "base_unit": "ml",
            }
        ],
    }

    # Start + Cancel -> 0 deduction
    plan_cancel_resp = await client.post(
        "/api/v1/cooking-plans", headers=headers_a, json=plan_payload
    )
    assert plan_cancel_resp.status_code == 201
    cancel_plan_id = plan_cancel_resp.json()["id"]

    # Stock unchanged (400 + 1000 = 1400)
    mid_summary = await client.get("/api/v1/inventory/summary?q=milk", headers=headers_a)
    assert Decimal(mid_summary.json()["items"][0]["total_remaining_qty"]) == Decimal("1400.000")

    cancelled = await client.post(
        f"/api/v1/cooking-plans/{cancel_plan_id}/cancel", headers=headers_a
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    # Stock still 1400 after cancel
    after_cancel_summary = await client.get("/api/v1/inventory/summary?q=milk", headers=headers_a)
    assert Decimal(after_cancel_summary.json()["items"][0]["total_remaining_qty"]) == Decimal(
        "1400.000"
    )

    # Start + Complete 600ml -> FEFO deducts 400ml from lot_1 (depleting it) + 200ml from lot_2
    plan_ok_resp = await client.post("/api/v1/cooking-plans", headers=headers_a, json=plan_payload)
    assert plan_ok_resp.status_code == 201
    ok_plan_id = plan_ok_resp.json()["id"]

    complete_resp = await client.post(
        f"/api/v1/cooking-plans/{ok_plan_id}/complete",
        headers=headers_a,
        json={"items": [{"canonical_item_id": milk_id, "actual_qty": "600.000"}]},
    )
    assert complete_resp.status_code == 200
    completed = complete_resp.json()
    assert completed["status"] == "completed"
    assert len(completed["deductions"]) == 2
    assert completed["deductions"][0]["lot_id"] == lot_1_id
    assert Decimal(completed["deductions"][0]["deducted_qty"]) == Decimal("400.000")
    assert completed["deductions"][1]["lot_id"] == lot_2_id
    assert Decimal(completed["deductions"][1]["deducted_qty"]) == Decimal("200.000")

    # Completing again -> 409 INVALID_STATE_TRANSITION
    recomplete = await client.post(
        f"/api/v1/cooking-plans/{ok_plan_id}/complete", headers=headers_a
    )
    assert_error_envelope(recomplete, 409, "INVALID_STATE_TRANSITION")

    # Insights summary check
    insights = await client.get("/api/v1/insights/summary?days=30", headers=headers_a)
    assert insights.status_code == 200
    assert insights.json()["total_spend_paise"] == 9000

    # 7. Cross-household 403 isolation
    for path in (
        "/api/v1/purchases",
        "/api/v1/inventory/summary",
        "/api/v1/recipes/recommendations",
        "/api/v1/cooking-plans",
        "/api/v1/insights/summary",
    ):
        cross_resp = await client.get(
            path,
            headers={**headers_b, "X-Household-Id": hh_a},
        )
        assert_error_envelope(cross_resp, 403, "FORBIDDEN")


async def test_mobile_contract_routes_chat_drafts_catalog_and_manual_lots(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = jwks_provider.make_token(sub="user_mobile", email="mob@example.com", name="Mobile")
    headers = {"Authorization": f"Bearer {token}"}
    me_resp = await client.get("/api/v1/auth/me", headers=headers)
    assert me_resp.status_code == 200

    # Empty inventory chat -> empty_inventory=True, suggestions=[]
    empty_chat = await client.post(
        "/api/v1/recipes/chat",
        headers=headers,
        json={"message": "What can I cook?", "servings": 2},
    )
    assert empty_chat.status_code == 200
    assert empty_chat.json()["empty_inventory"] is True
    assert empty_chat.json()["suggestions"] == []

    # Create catalog item & list catalog items
    cat_create = await client.post(
        "/api/v1/catalog/items",
        headers=headers,
        json={"name": "Basmati Rice", "category": "grains", "default_base_unit": "g"},
    )
    assert cat_create.status_code == 201
    rice_id = cat_create.json()["id"]

    cat_list = await client.get("/api/v1/catalog/items?q=rice", headers=headers)
    assert cat_list.status_code == 200
    assert cat_list.json()["total"] == 1
    assert cat_list.json()["items"][0]["id"] == rice_id

    # Create manual lot via POST /api/v1/inventory/lots
    lot_create = await client.post(
        "/api/v1/inventory/lots",
        headers=headers,
        json={
            "canonical_item_id": rice_id,
            "remaining_qty": "1000.000",
            "base_unit": "g",
            "expires_on": None,
        },
    )
    assert lot_create.status_code == 201
    assert lot_create.json()["status"] == "active"
    assert lot_create.json()["item_name"] == "Basmati Rice"

    # Purchase drafts alias routes: POST /drafts -> GET /drafts -> PATCH /drafts/{id} -> confirm
    draft_create = await client.post(
        "/api/v1/purchases/drafts",
        headers=headers,
        json={
            "merchant_name": "SuperMart",
            "purchased_on": datetime.now(UTC).date().isoformat(),
            "items": [
                {
                    "canonical_item_id": rice_id,
                    "display_name": "Basmati Rice",
                    "normalized_qty": "500.000",
                    "base_unit": "g",
                    "line_total_paise": 7500,
                }
            ],
        },
    )
    assert draft_create.status_code == 201
    draft_id = draft_create.json()["id"]

    drafts_list = await client.get("/api/v1/purchases/drafts?status=draft", headers=headers)
    assert drafts_list.status_code == 200
    assert len(drafts_list.json()["items"]) == 1

    draft_patch = await client.patch(
        f"/api/v1/purchases/drafts/{draft_id}",
        headers=headers,
        json={
            "merchant_name": "SuperMart Express",
            "items": [
                {
                    "canonical_item_id": rice_id,
                    "display_name": "Basmati Rice",
                    "normalized_qty": "500.000",
                    "base_unit": "g",
                    "line_total_paise": 8000,
                }
            ],
        },
    )
    assert draft_patch.status_code == 200
    assert draft_patch.json()["merchant_name"] == "SuperMart Express"

    draft_confirm = await client.post(
        f"/api/v1/purchases/drafts/{draft_id}/confirm",
        headers=headers,
    )
    assert draft_confirm.status_code == 200
    assert draft_confirm.json()["draft_id"] == draft_id

    # Non-empty inventory POST /api/v1/recipes/chat
    class FakeChatHFProvider:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def generate_recipes(self, **_kwargs: object) -> dict:
            return {
                "provider_status": "live",
                "reply": "You can make Jeera Rice!",
                "follow_up_questions": ["Want a side dish?"],
                "recipes": [
                    {
                        "recipe_id": "rec-jeera-rice",
                        "title": "Jeera Rice",
                        "servings": 2,
                        "prep_minutes": 20,
                        "ingredients": [
                            {"name": "Basmati Rice", "qty": "250", "unit": "g", "available": True}
                        ],
                        "instructions": ["Wash and cook rice."],
                    }
                ],
            }

    async def fake_refs(recipe_title: str, **_kwargs: object) -> list[dict]:
        return [{"title": recipe_title, "url": "https://example.com/jeera-rice", "verified": True}]

    monkeypatch.setattr(recipes_service, "_get_hf_provider_cls", lambda: FakeChatHFProvider)
    monkeypatch.setattr(recipes_service, "_get_discover_references_fn", lambda: fake_refs)

    chat_resp = await client.post(
        "/api/v1/recipes/chat",
        headers=headers,
        json={"message": "Quick rice dinner", "servings": 2, "max_prep_minutes": 30},
    )
    assert chat_resp.status_code == 200
    chat_body = chat_resp.json()
    assert chat_body["empty_inventory"] is False
    assert chat_body["provider_status"] == "live"
    assert chat_body["reply"] == "You can make Jeera Rice!"
    assert len(chat_body["suggestions"]) == 1
    assert chat_body["suggestions"][0]["title"] == "Jeera Rice"
    assert len(chat_body["suggestions"][0]["external_links"]) == 1
