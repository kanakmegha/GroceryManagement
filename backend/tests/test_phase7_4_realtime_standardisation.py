"""Phase 7.4 Real-Time Ingredient Standardisation During Receipt Extraction tests."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.db import build_async_engine, build_session_maker
from app.modules.catalog.ingredient_catalogue_sync import sync_catalogue_to_db
from app.modules.inventory.models import CanonicalIngredient


async def _bootstrap_household(
    client: AsyncClient, jwks_provider, prefix: str
) -> tuple[dict[str, str], str]:
    sub = f"{prefix}-{uuid.uuid4().hex[:8]}"
    token = jwks_provider.make_token(sub=sub, email=f"{sub}@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    hh_id = me.json()["active_household"]["id"]
    return {**headers, "X-Household-Id": hh_id}, hh_id


@pytest.mark.asyncio
async def test_phase7_4_realtime_standardisation_and_confidence_separation(
    client: AsyncClient,
    jwks_provider,
    temp_db_url: str,
) -> None:
    engine = build_async_engine(temp_db_url)
    session_maker = build_session_maker(engine)
    async with session_maker() as db:
        await sync_catalogue_to_db(db)

    headers, _ = await _bootstrap_household(client, jwks_provider, "p74-a")

    # 1. Manual/JSON draft creation runs real-time IngredientResolver + separate qty/financial
    # checks
    resp = await client.post(
        "/api/v1/purchases/drafts",
        headers=headers,
        json={
            "merchant_name": "Reliance Smart",
            "receipt_category": "grocery",
            "items": [
                {
                    "raw_text": "AMUL TAAZA TONED MILK 1L",
                    "display_name": "AMUL TAAZA TONED MILK 1L",
                    "qty": 1000,
                    "unit": "ml",
                    "unit_price_paise": 5400,
                    "line_total_paise": 5400,
                },
                {
                    "raw_text": "MILK 1L",
                    "display_name": "Milk",
                    "qty": 1000,
                    "unit": "ml",
                    "unit_price_paise": 6000,
                    "line_total_paise": 6000,
                },
                {
                    "raw_text": "XYZZZ MYSTERY PACK 200G",
                    "display_name": "XYZZZ MYSTERY PACK",
                    "qty": 2,
                    "unit": "pcs",
                    "unit_price_paise": 5000,
                    "line_total_paise": 15000,  # 2 * 5000 != 15000 -> financial mismatch
                },
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    draft = resp.json()
    items = draft["items"]
    assert len(items) == 3

    # Item 0: Amul Taaza Toned Milk -> HIGH Toned Milk, raw_text preserved
    milk_item = items[0]
    assert milk_item["raw_text"] == "AMUL TAAZA TONED MILK 1L"
    assert milk_item["standardized_name"] == "Toned Milk"
    assert milk_item["ingredient_confidence_tier"] == "HIGH"
    assert milk_item["ingredient_confidence_score"] >= 90
    assert milk_item["quantity_confidence_tier"] == "HIGH"
    assert milk_item["financial_mismatch"] is False

    # Item 1: Generic Milk -> unspecified Milk variant (never invents Toned/Full Cream)
    generic_milk = items[1]
    assert generic_milk["raw_text"] == "MILK 1L"
    assert generic_milk["standardized_name"] == "Milk"
    assert generic_milk["is_unspecified_variant"] is True
    assert generic_milk["ingredient_confidence_tier"] == "HIGH"

    # Item 2: Unmatched + separate financial mismatch flag
    mystery = items[2]
    assert mystery["raw_text"] == "XYZZZ MYSTERY PACK 200G"
    assert mystery["ingredient_confidence_tier"] == "UNMATCHED"
    assert mystery["standardized_name"] is None
    assert mystery["financial_mismatch"] is True
    assert mystery["financial_review_reason"]

    # 2. Confirmation safeguard: enforce_ingredient_review=True blocks UNMATCHED tracked items
    blocked = await client.post(
        f"/api/v1/purchases/drafts/{draft['id']}/confirm",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        json={"enforce_ingredient_review": True},
    )
    assert blocked.status_code == 422, blocked.text
    assert blocked.json()["error"]["code"] == "INGREDIENT_REVIEW_REQUIRED"

    # 3. User selects a canonical ingredient for the unmatched item via PATCH /drafts/{id}
    async with session_maker() as db:
        ghee_ing = (
            await db.execute(
                select(CanonicalIngredient).where(CanonicalIngredient.normalized_name == "ghee")
            )
        ).scalar_one()

    patch_resp = await client.patch(
        f"/api/v1/purchases/drafts/{draft['id']}",
        headers=headers,
        json={
            "merchant_name": "Reliance Smart",
            "items": [
                {
                    "id": milk_item["id"],
                    "raw_text": milk_item["raw_text"],
                    "canonical_item_id": milk_item["canonical_item_id"],
                    "display_name": milk_item["display_name"],
                    "normalized_qty": milk_item["normalized_qty"],
                    "base_unit": milk_item["base_unit"],
                    "unit_price_paise": milk_item["unit_price_paise"],
                    "line_total_paise": milk_item["line_total_paise"],
                    "track_inventory": True,
                },
                {
                    "id": generic_milk["id"],
                    "raw_text": generic_milk["raw_text"],
                    "canonical_item_id": generic_milk["canonical_item_id"],
                    "display_name": generic_milk["display_name"],
                    "normalized_qty": generic_milk["normalized_qty"],
                    "base_unit": generic_milk["base_unit"],
                    "unit_price_paise": generic_milk["unit_price_paise"],
                    "line_total_paise": generic_milk["line_total_paise"],
                    "track_inventory": True,
                },
                {
                    "id": mystery["id"],
                    "raw_text": mystery["raw_text"],
                    "canonical_item_id": mystery["canonical_item_id"],
                    "display_name": ghee_ing.name,
                    "normalized_qty": "200",
                    "base_unit": "g",
                    "unit_price_paise": 15000,
                    "line_total_paise": 15000,
                    "track_inventory": True,
                    "canonical_ingredient_id": str(ghee_ing.id),
                    "user_approved_ingredient": True,
                },
            ],
        },
    )
    assert patch_resp.status_code == 200, patch_resp.text
    patched_items = patch_resp.json()["items"]
    assert patched_items[2]["standardized_name"] == ghee_ing.name
    assert patched_items[2]["ingredient_confidence_tier"] == "HIGH"
    assert patched_items[2]["user_approved_ingredient"] is True
    assert patched_items[2]["raw_text"] == "XYZZZ MYSTERY PACK 200G"
    assert patched_items[2]["financial_mismatch"] is False

    # 4. Now confirm succeeds with enforce_ingredient_review=True
    confirm_ok = await client.post(
        f"/api/v1/purchases/drafts/{draft['id']}/confirm",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        json={"enforce_ingredient_review": True},
    )
    assert confirm_ok.status_code == 200, confirm_ok.text
    assert confirm_ok.json()["status"] == "confirmed"
    assert len(confirm_ok.json()["created_lots"]) == 3

    # 5. Household-scoped learning: Household A now resolves "XYZZZ MYSTERY PACK 200G" -> Ghee
    # (HIGH)
    next_a = await client.post(
        "/api/v1/purchases/drafts",
        headers=headers,
        json={
            "merchant_name": "Reliance Smart",
            "items": [
                {
                    "raw_text": "XYZZZ MYSTERY PACK 200G",
                    "display_name": "XYZZZ MYSTERY PACK 200G",
                    "qty": 200,
                    "unit": "g",
                    "unit_price_paise": 15000,
                    "line_total_paise": 15000,
                }
            ],
        },
    )
    assert next_a.status_code == 201, next_a.text
    assert next_a.json()["items"][0]["standardized_name"] == ghee_ing.name
    assert next_a.json()["items"][0]["ingredient_confidence_tier"] == "HIGH"

    # Household B does NOT inherit Household A's learned alias
    headers_b, _ = await _bootstrap_household(client, jwks_provider, "p74-b")
    next_b = await client.post(
        "/api/v1/purchases/drafts",
        headers=headers_b,
        json={
            "merchant_name": "Reliance Smart",
            "items": [
                {
                    "raw_text": "XYZZZ MYSTERY PACK 200G",
                    "display_name": "XYZZZ MYSTERY PACK 200G",
                    "qty": 200,
                    "unit": "g",
                    "unit_price_paise": 15000,
                    "line_total_paise": 15000,
                }
            ],
        },
    )
    assert next_b.status_code == 201, next_b.text
    assert next_b.json()["items"][0]["ingredient_confidence_tier"] == "UNMATCHED"
    await engine.dispose()


@pytest.mark.asyncio
async def test_phase7_4_ocr_json_standardises_immediately(
    client: AsyncClient,
    jwks_provider,
    temp_db_url: str,
) -> None:
    engine = build_async_engine(temp_db_url)
    session_maker = build_session_maker(engine)
    async with session_maker() as db:
        await sync_catalogue_to_db(db)

    headers, _ = await _bootstrap_household(client, jwks_provider, "p74-ocr")

    resp = await client.post(
        "/api/v1/purchases/ocr",
        headers=headers,
        json={
            "merchant_name": "DMart",
            "raw_text": "AASHIRVAAD SHARBATI ATTA 5KG 265.00\nFORTUNE SUNFLOWER OIL 1L 145.00",
        },
    )
    assert resp.status_code == 201, resp.text
    items = resp.json()["items"]
    assert len(items) >= 2
    names = [it.get("standardized_name") for it in items]
    assert any(n and "Wheat Flour" in n for n in names), names
    assert any(n and "Oil" in n for n in names), names
    await engine.dispose()


@pytest.mark.asyncio
async def test_phase7_4_edit_approve_change_unit_and_qty_then_confirm_idempotent(
    client: AsyncClient,
    jwks_provider,
    temp_db_url: str,
) -> None:
    engine = build_async_engine(temp_db_url)
    session_maker = build_session_maker(engine)
    async with session_maker() as db:
        await sync_catalogue_to_db(db)

    headers, _ = await _bootstrap_household(client, jwks_provider, "p74-edit-confirm")

    # 1. Create draft mirroring real OCR extraction (unit_price_paise=0, line_total_paise=27200)
    draft_resp = await client.post(
        "/api/v1/purchases/drafts",
        headers=headers,
        json={
            "merchant_name": "Blinkit",
            "total_amount_paise": 29200,
            "items": [
                {
                    "raw_text": "Amul Gold Full Cream Fresh Milk (Pouch)",
                    "display_name": "Amul Gold Full Cream Fresh Milk (Pouch)",
                    "qty": 4,
                    "unit": "pcs",
                    "unit_price_paise": 0,
                    "line_total_paise": 27200,
                },
                {
                    "raw_text": "Milk 500ml",
                    "display_name": "Milk",
                    "qty": 500,
                    "unit": "ml",
                    "unit_price_paise": 0,
                    "line_total_paise": 2000,
                },
            ],
        },
    )
    assert draft_resp.status_code == 201, draft_resp.text
    draft = draft_resp.json()
    item0, item1 = draft["items"][0], draft["items"][1]

    # 2-5. Edit standardised name -> approve -> change unit (pcs -> ml) -> change qty (4 -> 2000)
    patch_resp = await client.patch(
        f"/api/v1/purchases/drafts/{draft['id']}",
        headers=headers,
        json={
            "merchant_name": "Blinkit",
            "total_amount_paise": 29200,
            "items": [
                {
                    "id": item0["id"],
                    "raw_text": item0["raw_text"],
                    "canonical_item_id": item0["canonical_item_id"],
                    "display_name": "Full Cream Milk",
                    "normalized_qty": "2000",
                    "base_unit": "ml",
                    "unit_price_paise": 0,
                    "line_total_paise": 27200,
                    "track_inventory": True,
                    "user_approved_ingredient": True,
                },
                {
                    "id": item1["id"],
                    "raw_text": item1["raw_text"],
                    "canonical_item_id": item1["canonical_item_id"],
                    "display_name": item1["display_name"],
                    "normalized_qty": item1["normalized_qty"],
                    "base_unit": item1["base_unit"],
                    "unit_price_paise": 0,
                    "line_total_paise": 2000,
                    "track_inventory": True,
                    "user_approved_ingredient": True,
                },
            ],
        },
    )
    assert patch_resp.status_code == 200, patch_resp.text
    patched0 = patch_resp.json()["items"][0]
    assert patched0["raw_text"] == "Amul Gold Full Cream Fresh Milk (Pouch)"
    assert patched0["standardized_name"] == "Full Cream Milk"
    assert patched0["ingredient_confidence_tier"] == "HIGH"
    assert patched0["user_approved_ingredient"] is True
    assert patched0["base_unit"] == "ml"
    assert float(patched0["normalized_qty"]) == 2000.0
    assert patched0["line_total_paise"] == 27200

    # 6. Confirm purchase + idempotency check (no duplicate stock/purchase)
    idem = str(uuid.uuid4())
    confirm_1 = await client.post(
        f"/api/v1/purchases/drafts/{draft['id']}/confirm",
        headers={**headers, "Idempotency-Key": idem},
        json={"enforce_ingredient_review": True},
    )
    assert confirm_1.status_code == 200, confirm_1.text
    assert confirm_1.json()["status"] == "confirmed"
    assert len(confirm_1.json()["created_lots"]) == 2

    confirm_2 = await client.post(
        f"/api/v1/purchases/drafts/{draft['id']}/confirm",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        json={"enforce_ingredient_review": True},
    )
    assert confirm_2.status_code == 409, confirm_2.text
    await engine.dispose()
