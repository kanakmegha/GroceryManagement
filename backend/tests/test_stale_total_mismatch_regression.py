"""Regression test reproducing stale ₹2,580 vs ₹2,260 total-mismatch blocker."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from tests.conftest import EphemeralJWKSProvider


def _auth_headers(jwks_provider: EphemeralJWKSProvider, sub: str) -> dict[str, str]:
    token = jwks_provider.make_token(sub=sub, email=f"{sub}@example.com", name="Reconcile User")
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_correcting_line_items_clears_stale_mismatch_and_enables_confirm(
    client: AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    headers = _auth_headers(jwks_provider, "clk_stale_mismatch")
    me_res = await client.get("/api/v1/auth/me", headers=headers)
    assert me_res.status_code == 200
    headers["X-Household-Id"] = me_res.json()["active_household"]["id"]

    # 1. Initial OCR draft: items sum to ₹2,580.00 (258000) vs printed total ₹2,260.00 (226000)
    ocr_payload = {
        "merchant_name": "Super Mart",
        "total_amount_paise": 226000,
        "items": [
            {
                "raw_text": "Rice 10kg 2580.00",
                "display_name": "Basmati Rice",
                "qty": 10.0,
                "unit": "kg",
                "unit_price_paise": 25800,
                "line_total_paise": 258000,
                "confidence": 0.92,
            }
        ],
        "warnings": ["Receipt total mismatch: computed ₹2,580.00 differs from printed ₹2,260.00"],
    }
    with patch(
        "app.modules.purchases.service._invoke_extract_receipt",
        new=AsyncMock(return_value=ocr_payload),
    ):
        ocr_res = await client.post(
            "/api/v1/purchases/ocr",
            headers=headers,
            json={"raw_text": "Rice 10kg 2580.00 TOTAL 2260.00"},
        )
    assert ocr_res.status_code == 201
    draft = ocr_res.json()
    draft_id = draft["id"]
    item = draft["items"][0]

    # Genuine mismatch must block confirmation initially
    assert "total_amount" in draft["missing_fields"]
    blocked_confirm = await client.post(f"/api/v1/purchases/{draft_id}/confirm", headers=headers)
    assert blocked_confirm.status_code == 422
    assert blocked_confirm.json()["error"]["code"] == "TOTAL_REVIEW_REQUIRED"

    # 2. User corrects line item to ₹2,260.00 without re-typing printed total
    patch_res = await client.patch(
        f"/api/v1/purchases/{draft_id}",
        headers=headers,
        json={
            "merchant_name": "Super Mart",
            "items": [
                {
                    "id": item["id"],
                    "raw_text": item["raw_text"],
                    "display_name": "Basmati Rice",
                    "qty": 10.0,
                    "unit": "kg",
                    "normalized_qty": 10000.0,
                    "base_unit": "g",
                    "unit_price_paise": 22600,
                    "line_total_paise": 226000,
                    "track_inventory": True,
                    "user_approved_ingredient": True,
                }
            ],
        },
    )
    assert patch_res.status_code == 200
    saved = patch_res.json()
    assert saved["total_amount_paise"] == 226000
    assert "total_amount" not in saved["missing_fields"]
    assert not any("2,580" in w for w in (saved.get("ocr_warnings") or []))

    # 3. Reopen draft (GET) — stale mismatch warning remains cleared
    reopen_res = await client.get(f"/api/v1/purchases/{draft_id}", headers=headers)
    assert reopen_res.status_code == 200
    assert "total_amount" not in reopen_res.json()["missing_fields"]

    # 4. Scenario C: Editing item to create a new discrepancy (₹2,400 != ₹2,260) re-blocks confirm
    mismatch_patch = await client.patch(
        f"/api/v1/purchases/{draft_id}",
        headers=headers,
        json={
            "merchant_name": "Super Mart",
            "items": [
                {
                    "id": saved["items"][0]["id"],
                    "raw_text": item["raw_text"],
                    "display_name": "Basmati Rice",
                    "qty": 10.0,
                    "unit": "kg",
                    "normalized_qty": 10000.0,
                    "base_unit": "g",
                    "unit_price_paise": 24000,
                    "line_total_paise": 240000,
                    "track_inventory": True,
                    "user_approved_ingredient": True,
                }
            ],
        },
    )
    assert mismatch_patch.status_code == 200
    assert "total_amount" in mismatch_patch.json()["missing_fields"]
    reblocked = await client.post(f"/api/v1/purchases/{draft_id}/confirm", headers=headers)
    assert reblocked.status_code == 422

    # 5. Fix back to ₹2,260.00 -> confirm succeeds!
    fix_patch = await client.patch(
        f"/api/v1/purchases/{draft_id}",
        headers=headers,
        json={
            "merchant_name": "Super Mart",
            "items": [
                {
                    "id": mismatch_patch.json()["items"][0]["id"],
                    "raw_text": item["raw_text"],
                    "display_name": "Basmati Rice",
                    "qty": 10.0,
                    "unit": "kg",
                    "normalized_qty": 10000.0,
                    "base_unit": "g",
                    "unit_price_paise": 22600,
                    "line_total_paise": 226000,
                    "track_inventory": True,
                    "user_approved_ingredient": True,
                }
            ],
        },
    )
    assert fix_patch.status_code == 200
    assert "total_amount" not in fix_patch.json()["missing_fields"]

    confirm_res = await client.post(f"/api/v1/purchases/{draft_id}/confirm", headers=headers)
    assert confirm_res.status_code == 200
    assert confirm_res.json()["status"] == "confirmed"
