from decimal import Decimal

import httpx

from tests.conftest import EphemeralJWKSProvider


async def test_future_repeat_purchase_alias_reuse_price_history_and_retry_idempotency(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    token_a = jwks_provider.make_token(sub="user_rep_a", email="repa@example.com", name="RepA")
    token_b = jwks_provider.make_token(sub="user_rep_b", email="repb@example.com", name="RepB")
    h_a = {"Authorization": f"Bearer {token_a}"}
    h_b = {"Authorization": f"Bearer {token_b}"}

    assert (await client.get("/api/v1/auth/me", headers=h_a)).status_code == 200
    assert (await client.get("/api/v1/auth/me", headers=h_b)).status_code == 200

    # 1. Draft creation does NOT mutate pantry
    p1 = await client.post(
        "/api/v1/purchases",
        headers=h_a,
        json={
            "merchant_name": "Aggarwal Traders",
            "purchased_on": "2026-11-18",
            "items": [
                {
                    "raw_text": "1 Turmeric Powder 1 KG 180 180.00",
                    "display_name": "Turmeric Powder",
                    "qty": 1,
                    "unit": "kg",
                    "unit_price_paise": 18000,
                    "line_total_paise": 18000,
                    "category": "spice",
                }
            ],
        },
    )
    assert p1.status_code == 201
    p1_id = p1.json()["id"]
    assert (await client.get("/api/v1/inventory/summary", headers=h_a)).json()["items"] == []

    # 2. Confirm purchase 1 -> creates 1 lot (1000g) & learns approved alias
    c1 = await client.post(f"/api/v1/purchases/{p1_id}/confirm", headers=h_a, json={})
    assert c1.status_code == 200
    ci_id = c1.json()["created_lots"][0]["canonical_item_id"]

    # Retry confirmation -> 409, no duplicate stock
    c1_retry = await client.post(f"/api/v1/purchases/{p1_id}/confirm", headers=h_a, json={})
    assert c1_retry.status_code == 409

    # 3. Repeat purchase with punctuation/OCR variation -> reuses SAME CanonicalItem ID
    p2 = await client.post(
        "/api/v1/purchases",
        headers=h_a,
        json={
            "merchant_name": "Aggarwal Traders",
            "purchased_on": "2026-11-20",
            "items": [
                {
                    "raw_text": '" Turmeric Powder. 2 KG 190 380.00',
                    "display_name": '" Turmeric Powder.',
                    "qty": 2,
                    "unit": "kg",
                    "unit_price_paise": 19000,
                    "line_total_paise": 38000,
                    "category": "spice",
                }
            ],
        },
    )
    assert p2.status_code == 201
    c2 = await client.post(f"/api/v1/purchases/{p2.json()['id']}/confirm", headers=h_a, json={})
    assert c2.status_code == 200
    assert c2.json()["created_lots"][0]["canonical_item_id"] == ci_id

    # 4. Consolidated pantry summary shows 1 product, 3000g total, 2 active lots
    inv_a = (await client.get("/api/v1/inventory/summary", headers=h_a)).json()["items"]
    assert len(inv_a) == 1
    assert inv_a[0]["canonical_item_id"] == ci_id
    assert Decimal(inv_a[0]["total_remaining_qty"]) == Decimal("3000.000")
    assert inv_a[0]["active_lot_count"] == 2

    # 5. Price history retains both purchases separately
    ph = (await client.get(f"/api/v1/inventory/items/{ci_id}/price-history", headers=h_a)).json()
    assert len(ph["entries"]) == 2
    assert {e["line_total_paise"] for e in ph["entries"]} == {18000, 38000}

    # 6. Household B remains isolated
    assert (await client.get("/api/v1/inventory/summary", headers=h_b)).json()["items"] == []
