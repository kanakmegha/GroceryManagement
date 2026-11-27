"""Regression tests ensuring user-entered ingredient names are never overwritten."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from tests.conftest import EphemeralJWKSProvider


def _auth_headers(jwks_provider: EphemeralJWKSProvider, sub: str) -> dict[str, str]:
    token = jwks_provider.make_token(sub=sub, email=f"{sub}@example.com", name="Name Preserve User")
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_user_entered_name_chini_df_preserved_across_save_reopen_and_confirm(
    client: AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    headers = _auth_headers(jwks_provider, "clk_name_preserve")
    me_res = await client.get("/api/v1/auth/me", headers=headers)
    assert me_res.status_code == 200
    headers["X-Household-Id"] = me_res.json()["active_household"]["id"]

    # 1. Create draft from OCR with "Sugar 1kg"
    ocr_payload = {
        "merchant_name": "Local Kirana",
        "total_amount_paise": 5000,
        "items": [
            {
                "raw_text": "Madhur Sugar 1kg 50.00",
                "display_name": "Sugar",
                "qty": 1.0,
                "unit": "kg",
                "unit_price_paise": 5000,
                "line_total_paise": 5000,
                "confidence": 0.95,
            }
        ],
    }
    with patch(
        "app.modules.purchases.service._invoke_extract_receipt",
        new=AsyncMock(return_value=ocr_payload),
    ):
        ocr_res = await client.post(
            "/api/v1/purchases/ocr",
            headers=headers,
            json={"raw_text": "Madhur Sugar 1kg 50.00"},
        )
    assert ocr_res.status_code == 201
    draft = ocr_res.json()
    draft_id = draft["id"]
    item = draft["items"][0]
    canonical_ing_id = item.get("canonical_ingredient_id")

    # 2. User manually changes display_name to "chini df" while keeping canonical mapping & approval
    patch_res = await client.patch(
        f"/api/v1/purchases/{draft_id}",
        headers=headers,
        json={
            "merchant_name": "Local Kirana",
            "total_amount_paise": 5000,
            "items": [
                {
                    "id": item["id"],
                    "raw_text": item["raw_text"],
                    "display_name": "chini df",
                    "normalized_qty": 1000.0,
                    "base_unit": "g",
                    "unit_price_paise": 5000,
                    "line_total_paise": 5000,
                    "track_inventory": True,
                    "canonical_ingredient_id": canonical_ing_id,
                    "user_approved_ingredient": True,
                }
            ],
        },
    )
    assert patch_res.status_code == 200
    saved = patch_res.json()
    assert saved["items"][0]["display_name"] == "chini df"
    assert saved["items"][0]["raw_text"] == "Madhur Sugar 1kg 50.00"
    if canonical_ing_id is not None:
        assert saved["items"][0]["canonical_ingredient_id"] == canonical_ing_id

    # 3. Reopen draft (GET) — must still be "chini df"
    get_res = await client.get(f"/api/v1/purchases/{draft_id}", headers=headers)
    assert get_res.status_code == 200
    reopened = get_res.json()
    assert reopened["items"][0]["display_name"] == "chini df"

    # 4. Confirm purchase — must NOT silently replace "chini df" with "Sugar"
    confirm_res = await client.post(f"/api/v1/purchases/{draft_id}/confirm", headers=headers)
    assert confirm_res.status_code == 200

    final_res = await client.get(f"/api/v1/purchases/{draft_id}", headers=headers)
    assert final_res.status_code == 200
    final_purchase = final_res.json()
    assert final_purchase["status"] == "confirmed"
    assert final_purchase["items"][0]["display_name"] == "chini df"
    assert final_purchase["items"][0]["raw_text"] == "Madhur Sugar 1kg 50.00"

    # 5. Second receipt with same original description reuses canonical mapping via learned alias
    second_ocr = {
        "merchant_name": "Local Kirana",
        "total_amount_paise": 5000,
        "items": [
            {
                "raw_text": "Madhur Sugar 1kg 50.00",
                "display_name": "Madhur Sugar 1kg",
                "qty": 1.0,
                "unit": "kg",
                "unit_price_paise": 5000,
                "line_total_paise": 5000,
                "confidence": 0.95,
            }
        ],
    }
    with patch(
        "app.modules.purchases.service._invoke_extract_receipt",
        new=AsyncMock(return_value=second_ocr),
    ):
        res2 = await client.post(
            "/api/v1/purchases/ocr",
            headers=headers,
            json={"raw_text": "Madhur Sugar 1kg 50.00"},
        )
    assert res2.status_code == 201
    draft2 = res2.json()
    item2 = draft2["items"][0]
    if canonical_ing_id is not None:
        assert item2["canonical_ingredient_id"] == canonical_ing_id
    # User display name on second draft is not silently replaced if edited
    assert item2["raw_text"] == "Madhur Sugar 1kg 50.00"


@pytest.mark.asyncio
async def test_unconfirmed_edit_does_not_create_learned_alias(
    client: AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    headers = _auth_headers(jwks_provider, "clk_unconfirmed_no_learn")
    me_res = await client.get("/api/v1/auth/me", headers=headers)
    assert me_res.status_code == 200
    headers["X-Household-Id"] = me_res.json()["active_household"]["id"]

    ocr_payload = {
        "merchant_name": "Local Kirana",
        "total_amount_paise": 4500,
        "items": [
            {
                "raw_text": "XYZ Unmapped Special Pack 500g",
                "display_name": "XYZ Unmapped Special Pack",
                "qty": 500.0,
                "unit": "g",
                "unit_price_paise": 4500,
                "line_total_paise": 4500,
                "confidence": 0.9,
            }
        ],
    }
    with patch(
        "app.modules.purchases.service._invoke_extract_receipt",
        new=AsyncMock(return_value=ocr_payload),
    ):
        ocr_res = await client.post(
            "/api/v1/purchases/ocr",
            headers=headers,
            json={"raw_text": "XYZ Unmapped Special Pack 500g"},
        )
    assert ocr_res.status_code == 201
    draft = ocr_res.json()
    item = draft["items"][0]

    with patch(
        "app.modules.catalog.ingredient_catalogue_sync.record_household_learned_alias",
        new=AsyncMock(),
    ) as mock_learn:
        patch_res = await client.patch(
            f"/api/v1/purchases/{draft['id']}",
            headers=headers,
            json={
                "merchant_name": "Local Kirana",
                "total_amount_paise": 4500,
                "items": [
                    {
                        "id": item["id"],
                        "raw_text": item["raw_text"],
                        "display_name": "custom unconfirmed name",
                        "normalized_qty": 500.0,
                        "base_unit": "g",
                        "unit_price_paise": 4500,
                        "line_total_paise": 4500,
                        "track_inventory": True,
                        "canonical_ingredient_id": None,
                        "user_approved_ingredient": False,
                    }
                ],
            },
        )
    assert patch_res.status_code == 200
    assert patch_res.json()["items"][0]["display_name"] == "custom unconfirmed name"
    mock_learn.assert_not_called()
