"""Stage 1 tests: canonical ingredient identity resolution and read-only dry-run duplicate audit."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.modules.catalog.canonical_resolver import clean_canonical_candidate_name
from tests.conftest import EphemeralJWKSProvider


def test_clean_canonical_candidate_name_strips_rate_noise_preserves_grades() -> None:
    assert clean_canonical_candidate_name("120 Chana Dal", unit_price_paise=12000) == "Chana Dal"
    assert clean_canonical_candidate_name("130 Chana Dal", unit_price_paise=13000) == "Chana Dal"
    assert clean_canonical_candidate_name("Chana Dal 1120 123 234") == "Chana Dal"
    assert clean_canonical_candidate_name("Badam 1180", unit_price_paise=118000) == "Badam"
    assert clean_canonical_candidate_name("Akhrot 1600", unit_price_paise=160000) == "Akhrot"
    assert clean_canonical_candidate_name("10 CHOLE Aêm Qty: 31") == "CHOLE"
    assert clean_canonical_candidate_name("DAL =====> S KALI Malka 94") == "KALI Malka"
    # Genuine grade/variant preserved
    assert clean_canonical_candidate_name("40 Chawal", unit_price_paise=4000) == "40 Chawal"
    assert clean_canonical_candidate_name("2 40 CHAWAL 40", unit_price_paise=4000) == "40 Chawal"


@pytest.mark.asyncio
async def test_confirm_deduplicates_rate_contaminated_names_and_preserves_raw_text(
    client: AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    token = jwks_provider.make_token(sub="stage1_user_1", email="s1@example.com", name="Stage1")
    headers = {"Authorization": f"Bearer {token}"}
    me_resp = await client.get("/api/v1/auth/me", headers=headers)
    assert me_resp.status_code == 200
    headers["X-Household-Id"] = me_resp.json()["active_household"]["id"]

    # Confirm 3 rate-contaminated Chana Dal variants
    for raw_name, price_paise in [
        ("120 Chana Dal", 12000),
        ("130 Chana Dal", 13000),
        ("Chana Dal 1120 123 234", 11200),
    ]:
        draft_resp = await client.post(
            "/api/v1/purchases/drafts",
            headers=headers,
            json={
                "merchant_name": "AGGARWAL TRADERS",
                "purchased_on": "2026-11-19",
                "receipt_category": "grocery",
                "total_amount_paise": price_paise,
                "items": [
                    {
                        "raw_text": f"{raw_name} 1 KG {price_paise // 100} {price_paise // 100}.00",
                        "display_name": raw_name,
                        "normalized_qty": 1000,
                        "base_unit": "g",
                        "unit_price_paise": price_paise,
                        "line_total_paise": price_paise,
                        "track_inventory": True,
                        "budget_treatment": "grocery_inventory",
                    }
                ],
            },
        )
        assert draft_resp.status_code == 201, draft_resp.text
        pid = draft_resp.json()["id"]
        conf_resp = await client.post(
            f"/api/v1/purchases/drafts/{pid}/confirm", headers=headers, json={}
        )
        assert conf_resp.status_code == 200, conf_resp.text

    # Confirm distinct Chana Kala
    kala_resp = await client.post(
        "/api/v1/purchases/drafts",
        headers=headers,
        json={
            "merchant_name": "AGGARWAL TRADERS",
            "purchased_on": "2026-11-19",
            "receipt_category": "grocery",
            "total_amount_paise": 11000,
            "items": [
                {
                    "raw_text": "11 CHANA KALA 1 KG 110 110.00",
                    "display_name": "11 CHANA KALA",
                    "normalized_qty": 1000,
                    "base_unit": "g",
                    "unit_price_paise": 11000,
                    "line_total_paise": 11000,
                    "track_inventory": True,
                    "budget_treatment": "grocery_inventory",
                }
            ],
        },
    )
    assert kala_resp.status_code == 201, kala_resp.text
    assert (
        await client.post(
            f"/api/v1/purchases/drafts/{kala_resp.json()['id']}/confirm",
            headers=headers,
            json={},
        )
    ).status_code == 200

    # Summary should aggregate all 3 Chana Dal lots into ONE Chana Dal entry (3000 g) and keep Chana Kala separate (1000 g)
    summary_resp = await client.get("/api/v1/inventory/summary", headers=headers)
    assert summary_resp.status_code == 200
    items = summary_resp.json()["items"]
    chana_dal_entries = [it for it in items if "chana dal" in it["name"].lower()]
    chana_kala_entries = [it for it in items if "black chickpeas" in it["name"].lower() or "chana kala" in it["name"].lower()]
    assert len(chana_dal_entries) == 1
    assert float(chana_dal_entries[0]["total_remaining_qty"]) == 3000.0
    assert chana_dal_entries[0]["active_lot_count"] == 3
    assert len(chana_kala_entries) == 1
    assert float(chana_kala_entries[0]["total_remaining_qty"]) == 1000.0


@pytest.mark.asyncio
async def test_dry_run_duplicate_audit_is_readonly_and_detects_clusters(
    client: AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    token = jwks_provider.make_token(sub="stage1_user_2", email="s2@example.com", name="Stage1Audit")
    headers = {"Authorization": f"Bearer {token}"}
    me_resp = await client.get("/api/v1/auth/me", headers=headers)
    assert me_resp.status_code == 200
    headers["X-Household-Id"] = me_resp.json()["active_household"]["id"]

    # Seed duplicate and distinct catalog items
    for name in ("BADAM", "BADAM 1180", "40 CHAWAL", "LAL CHAWAL"):
        r = await client.post(
            "/api/v1/catalog/items",
            headers=headers,
            json={"name": name, "default_base_unit": "g"},
        )
        assert r.status_code == 201, r.text

    resp = await client.get("/api/v1/catalog/duplicates/dry-run", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["duplicate_clusters_count"] == 1
    badam_props = [p for p in data["proposals"] if p["duplicate_name"] == "BADAM 1180"]
    assert len(badam_props) == 1
    assert badam_props[0]["survivor_name"] == "BADAM"
    # 40 CHAWAL and LAL CHAWAL must NOT be proposed for merge
    assert not any(p["duplicate_name"] in {"40 CHAWAL", "LAL CHAWAL"} for p in data["proposals"])

    # Verify strictly read-only: all 4 items remain in catalog
    cat_resp = await client.get("/api/v1/catalog/items", headers=headers)
    assert cat_resp.status_code == 200
    assert cat_resp.json()["total"] == 4
