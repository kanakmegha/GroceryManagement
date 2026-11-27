"""Regression tests for purchase commercial qty/unit vs normalized inventory qty/unit."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from tests.conftest import EphemeralJWKSProvider


def _auth_headers(jwks_provider: EphemeralJWKSProvider, sub: str) -> dict[str, str]:
    token = jwks_provider.make_token(sub=sub, email=f"{sub}@example.com", name="QtyUnit User")
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_2kg_at_53_per_kg_preserves_purchase_unit_and_normalizes_inventory_to_2000g(
    client: AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    headers = _auth_headers(jwks_provider, "clk_qty_2kg")
    me_res = await client.get("/api/v1/auth/me", headers=headers)
    assert me_res.status_code == 200
    headers["X-Household-Id"] = me_res.json()["active_household"]["id"]

    # 1. OCR draft: 2 kg Sugar @ ₹53/kg = ₹106, 1 L Milk @ ₹40/L = ₹40, 2 pcs Bread @ ₹45 = ₹90
    ocr_payload = {
        "merchant_name": "Fresh Mart",
        "total_amount_paise": 23600,
        "items": [
            {
                "raw_text": "Sugar 2 kg 53.00 106.00",
                "display_name": "Sugar",
                "qty": 2.0,
                "unit": "kg",
                "unit_price_paise": 5300,
                "line_total_paise": 10600,
                "confidence": 0.96,
            },
            {
                "raw_text": "Whole Milk 1 l 40.00 40.00",
                "display_name": "Whole Milk",
                "qty": 1.0,
                "unit": "l",
                "unit_price_paise": 4000,
                "line_total_paise": 4000,
                "confidence": 0.95,
            },
            {
                "raw_text": "Brown Bread 2 pcs 45.00 90.00",
                "display_name": "Brown Bread",
                "qty": 2.0,
                "unit": "pcs",
                "unit_price_paise": 4500,
                "line_total_paise": 9000,
                "confidence": 0.95,
            },
        ],
    }
    with patch(
        "app.modules.purchases.service._invoke_extract_receipt",
        new=AsyncMock(return_value=ocr_payload),
    ):
        ocr_res = await client.post(
            "/api/v1/purchases/ocr",
            headers=headers,
            json={"raw_text": "Sugar 2 kg 106.00"},
        )
    assert ocr_res.status_code == 201
    draft = ocr_res.json()
    sugar, milk, bread = draft["items"][0], draft["items"][1], draft["items"][2]

    # Commercial purchase units preserved; normalized inventory units computed accurately
    assert sugar["purchase_qty"] == pytest.approx(2.0)
    assert sugar["purchase_unit"] == "kg"
    assert float(sugar["normalized_qty"]) == pytest.approx(2000.0)
    assert sugar["base_unit"] == "g"
    assert sugar["unit_price_paise"] == 5300
    assert sugar["line_total_paise"] == 10600

    assert milk["purchase_qty"] == pytest.approx(1.0)
    assert milk["purchase_unit"] == "l"
    assert float(milk["normalized_qty"]) == pytest.approx(1000.0)
    assert milk["base_unit"] == "ml"
    assert milk["line_total_paise"] == 4000

    assert bread["purchase_qty"] == pytest.approx(2.0)
    assert bread["purchase_unit"] == "pcs"
    assert float(bread["normalized_qty"]) == pytest.approx(2.0)
    assert bread["base_unit"] == "pcs"

    # 2. PATCH draft: preserve 2 kg Sugar, mark Bread as non-pantry (track_inventory=False)
    patch_res = await client.patch(
        f"/api/v1/purchases/{draft['id']}",
        headers=headers,
        json={
            "merchant_name": "Fresh Mart",
            "total_amount_paise": 23600,
            "items": [
                {
                    "id": sugar["id"],
                    "raw_text": sugar["raw_text"],
                    "display_name": "Sugar",
                    "qty": 2.0,
                    "unit": "kg",
                    "normalized_qty": 2000.0,
                    "base_unit": "g",
                    "unit_price_paise": 5300,
                    "line_total_paise": 10600,
                    "track_inventory": True,
                    "user_approved_ingredient": True,
                },
                {
                    "id": milk["id"],
                    "raw_text": milk["raw_text"],
                    "display_name": "Whole Milk",
                    "qty": 1.0,
                    "unit": "l",
                    "normalized_qty": 1000.0,
                    "base_unit": "ml",
                    "unit_price_paise": 4000,
                    "line_total_paise": 4000,
                    "track_inventory": True,
                    "user_approved_ingredient": True,
                },
                {
                    "id": bread["id"],
                    "raw_text": bread["raw_text"],
                    "display_name": "Brown Bread",
                    "qty": 2.0,
                    "unit": "pcs",
                    "normalized_qty": 2.0,
                    "base_unit": "pcs",
                    "unit_price_paise": 4500,
                    "line_total_paise": 9000,
                    "track_inventory": False,
                    "budget_treatment": "household_non_food",
                },
            ],
        },
    )
    assert patch_res.status_code == 200
    saved = patch_res.json()
    assert saved["items"][0]["purchase_qty"] == pytest.approx(2.0)
    assert saved["items"][0]["purchase_unit"] == "kg"
    assert float(saved["items"][0]["normalized_qty"]) == pytest.approx(2000.0)
    assert saved["items"][0]["line_total_paise"] == 10600

    # 3. Reopen draft (GET) — 2 kg still preserved
    get_res = await client.get(f"/api/v1/purchases/{draft['id']}", headers=headers)
    assert get_res.status_code == 200
    assert get_res.json()["items"][0]["purchase_qty"] == pytest.approx(2.0)
    assert get_res.json()["items"][0]["purchase_unit"] == "kg"

    # 4. Confirm purchase — pantry items enter inventory (2000 g, 1000 ml), non-pantry excluded
    confirm_res = await client.post(f"/api/v1/purchases/{draft['id']}/confirm", headers=headers)
    assert confirm_res.status_code == 200
    lots = confirm_res.json()["created_lots"]
    assert len(lots) == 2  # Sugar (2000 g) + Milk (1000 ml); Bread (non-pantry) excluded
    lot_qtys = sorted((float(lot["remaining_qty"]), lot["base_unit"]) for lot in lots)
    assert lot_qtys == [(1000.0, "ml"), (2000.0, "g")]

    # Confirmed purchase still preserves commercial 2 kg and retains non-pantry Bread in expenses
    final_res = await client.get(f"/api/v1/purchases/{draft['id']}", headers=headers)
    assert final_res.status_code == 200
    final_data = final_res.json()
    assert len(final_data["items"]) == 3
    assert final_data["items"][0]["purchase_qty"] == pytest.approx(2.0)
    assert final_data["items"][0]["purchase_unit"] == "kg"
    assert final_data["items"][2]["track_inventory"] is False
