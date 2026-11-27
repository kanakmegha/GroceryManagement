"""End-to-end HTTP lifecycle tests for receipt OCR -> review -> correction -> confirm -> inventory."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from tests.conftest import EphemeralJWKSProvider


def _auth_headers(jwks_provider: EphemeralJWKSProvider, sub: str) -> dict[str, str]:
    token = jwks_provider.make_token(sub=sub, email=f"{sub}@example.com", name="E2E User")
    return {"Authorization": f"Bearer {token}"}


def _item(name: str, total_paise: int, qty: float = 1.0) -> dict:
    return {
        "raw_text": f"{name} {total_paise / 100:.2f}",
        "display_name": name,
        "qty": qty,
        "unit": "pcs",
        "unit_price_paise": int(round(total_paise / max(qty, 1.0))),
        "line_total_paise": total_paise,
        "confidence": 0.92,
    }


@pytest.mark.asyncio
async def test_e2e_negative_total_review_correction_confirm_and_idempotent_inventory(
    client: AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    headers = _auth_headers(jwks_provider, "clk_e2e_neg")
    me_res = await client.get("/api/v1/auth/me", headers=headers)
    assert me_res.status_code == 200
    hh_id = me_res.json()["active_household"]["id"]
    headers["X-Household-Id"] = hh_id

    # 1. Upload receipt with negative calculated total (-21801)
    malformed_ocr = {
        "merchant_name": "Fresh Mart",
        "discount_paise": 31801,
        "items": [_item("Whole Milk", 10000, qty=2)],
    }
    with patch(
        "app.modules.purchases.service._invoke_extract_receipt",
        new=AsyncMock(return_value=malformed_ocr),
    ):
        ocr_res = await client.post(
            "/api/v1/purchases/ocr",
            headers=headers,
            json={"raw_text": "Whole Milk 100.00 @ 85 saved"},
        )
    assert ocr_res.status_code == 201
    draft = ocr_res.json()
    draft_id = draft["id"]
    assert draft["total_amount_paise"] == 0
    assert "total_amount" in draft["missing_fields"]
    assert len(draft["items"]) == 1

    # 2. Unrelated PATCH (merchant_name only, omitting total_amount_paise) preserves review state
    patch_unrelated = await client.patch(
        f"/api/v1/purchases/drafts/{draft_id}",
        headers=headers,
        json={"merchant_name": "Fresh Mart Express"},
    )
    assert patch_unrelated.status_code == 200
    assert "total_amount" in patch_unrelated.json()["missing_fields"]

    # 3. Reopen unresolved draft via GET
    reopened = await client.get(f"/api/v1/purchases/drafts/{draft_id}", headers=headers)
    assert reopened.status_code == 200
    assert reopened.json()["merchant_name"] == "Fresh Mart Express"
    assert "total_amount" in reopened.json()["missing_fields"]

    # 4. Confirm while unresolved -> blocked with 422 TOTAL_REVIEW_REQUIRED
    blocked_confirm = await client.post(
        f"/api/v1/purchases/drafts/{draft_id}/confirm",
        headers=headers,
    )
    assert blocked_confirm.status_code == 422
    assert blocked_confirm.json()["error"]["code"] == "TOTAL_REVIEW_REQUIRED"

    # 5. Explicitly submit corrected total via PATCH -> clears review state
    patch_total = await client.patch(
        f"/api/v1/purchases/drafts/{draft_id}",
        headers=headers,
        json={"total_amount_paise": 42500},
    )
    assert patch_total.status_code == 200
    assert patch_total.json()["total_amount_paise"] == 42500
    assert "total_amount" not in patch_total.json()["missing_fields"]

    # 6. Reopen corrected draft via GET
    reopened_ok = await client.get(f"/api/v1/purchases/drafts/{draft_id}", headers=headers)
    assert reopened_ok.status_code == 200
    assert "total_amount" not in reopened_ok.json()["missing_fields"]

    # 7. Confirm purchase -> 200 and inventory updated once
    confirm_1 = await client.post(
        f"/api/v1/purchases/drafts/{draft_id}/confirm",
        headers=headers,
    )
    assert confirm_1.status_code == 200
    assert confirm_1.json()["status"] == "confirmed"

    inv_after_1 = await client.get("/api/v1/inventory/lots", headers=headers)
    assert inv_after_1.status_code == 200
    lots_after_1 = inv_after_1.json()["items"]
    assert len(lots_after_1) == 1
    qty_after_1 = float(lots_after_1[0]["remaining_qty"])

    # 8. Repeated confirm -> 409 DRAFT_ALREADY_CONFIRMED, inventory NOT updated twice
    confirm_2 = await client.post(
        f"/api/v1/purchases/drafts/{draft_id}/confirm",
        headers=headers,
    )
    assert confirm_2.status_code == 409
    assert confirm_2.json()["error"]["code"] == "DRAFT_ALREADY_CONFIRMED"

    inv_after_2 = await client.get("/api/v1/inventory/lots", headers=headers)
    assert inv_after_2.status_code == 200
    assert len(inv_after_2.json()["items"]) == 1
    assert float(inv_after_2.json()["items"][0]["remaining_qty"]) == qty_after_1


@pytest.mark.asyncio
async def test_e2e_explicit_zero_total_clears_review_and_confirms(
    client: AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    headers = _auth_headers(jwks_provider, "clk_e2e_zero")
    me_res = await client.get("/api/v1/auth/me", headers=headers)
    headers["X-Household-Id"] = me_res.json()["active_household"]["id"]

    with patch(
        "app.modules.purchases.service._invoke_extract_receipt",
        new=AsyncMock(
            return_value={
                "merchant_name": "Voucher Mart",
                "discount_paise": 20000,
                "items": [_item("Bread", 5000)],
            }
        ),
    ):
        ocr_res = await client.post(
            "/api/v1/purchases/ocr",
            headers=headers,
            json={"raw_text": "Bread 50.00 Voucher 200.00"},
        )
    assert ocr_res.status_code == 201
    draft_id = ocr_res.json()["id"]
    assert "total_amount" in ocr_res.json()["missing_fields"]

    # Explicitly entering ₹0 clears review state
    patch_zero = await client.patch(
        f"/api/v1/purchases/drafts/{draft_id}",
        headers=headers,
        json={"total_amount_paise": 0},
    )
    assert patch_zero.status_code == 200
    assert patch_zero.json()["total_amount_paise"] == 0
    assert "total_amount" not in patch_zero.json()["missing_fields"]

    confirm_res = await client.post(
        f"/api/v1/purchases/drafts/{draft_id}/confirm",
        headers=headers,
    )
    assert confirm_res.status_code == 200
    assert confirm_res.json()["status"] == "confirmed"
