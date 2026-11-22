"""Phase 5A: Grocery measurement profiles, unit normalization, price validation,
and soft-merged Chana Bal visibility regression tests (18 scenarios).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.modules.catalog.measurement_service import (
    get_measurement_profile,
    normalize_grocery_quantity,
    validate_receipt_quantity_against_price,
)
from app.modules.catalog.product_identity_service import resolve_product_identity
from app.modules.catalog.product_merge_service import merge_canonical_products
from app.modules.inventory.models import CanonicalItem, InventoryLot
from tests.conftest import EphemeralJWKSProvider


def test_phase5a_measurement_profiles_and_unit_normalization() -> None:
    """Covers scenarios 1-11: explicit conversions, packets, unknown units, and price mismatch."""
    # 1. Chana Dal 1 kg + 500 g = 1500 g
    r1a = normalize_grocery_quantity(1, "kg")
    r1b = normalize_grocery_quantity(500, "g")
    assert r1a.normalized_qty == Decimal("1000.000") and r1a.normalized_unit == "g"
    assert r1b.normalized_qty == Decimal("500.000") and r1b.normalized_unit == "g"
    assert r1a.normalized_qty + r1b.normalized_qty == Decimal("1500.000")

    # 2. Rice 2 kg + 500 g = 2500 g
    r2a = normalize_grocery_quantity(2, "kg")
    r2b = normalize_grocery_quantity(500, "g")
    assert r2a.normalized_qty + r2b.normalized_qty == Decimal("2500.000")

    # 3. Milk 1 L + 500 ml = 1500 ml
    r3a = normalize_grocery_quantity(1, "L")
    r3b = normalize_grocery_quantity(500, "ml")
    assert r3a.normalized_qty == Decimal("1000.000") and r3a.normalized_unit == "ml"
    assert r3a.normalized_qty + r3b.normalized_qty == Decimal("1500.000")

    # 4. Eggs 1 dozen + 3 pcs = 15 pcs
    r4a = normalize_grocery_quantity(1, "dozen")
    r4b = normalize_grocery_quantity(3, "pcs")
    assert r4a.normalized_qty == Decimal("12.000") and r4a.normalized_unit == "pcs"
    assert r4a.normalized_qty + r4b.normalized_qty == Decimal("15.000")

    # 5. Packets with explicit net weight/volume
    p1 = normalize_grocery_quantity(1, "packet", raw_description="1 packet Chana Dal 500 g")
    assert p1.normalized_qty == Decimal("500.000") and p1.normalized_unit == "g"
    assert p1.needs_review is False

    p2 = normalize_grocery_quantity(2, "packets", raw_description="2 packets Milk 1 L each")
    assert p2.normalized_qty == Decimal("2000.000") and p2.normalized_unit == "ml"
    assert p2.package_count == Decimal("2")

    # 6. Packets without net weight -> unknown quantity, needs_review=True
    p3 = normalize_grocery_quantity(1, "packet", raw_description="1 packet Chana Dal")
    assert p3.normalized_qty is None and p3.needs_review is True
    assert p3.provenance == "unknown_package_net_qty"

    # 9. Unknown units are flagged
    u1 = normalize_grocery_quantity(2, "bunch")
    assert u1.normalized_qty is None and u1.needs_review is True
    assert u1.provenance == "unknown_unit"

    # Measurement profiles
    prof_pot = get_measurement_profile("potato")
    assert prof_pot.allows_multi_dimension is True and "pcs" in prof_pot.accepted_units
    prof_milk = get_measurement_profile("milk")
    assert prof_milk.dimension == "VOLUME" and prof_milk.canonical_unit == "ml"

    # 10 & 11. Price-based quantity mismatch & CHAWAL 1,000x regression
    chawal_check = validate_receipt_quantity_against_price(
        normalized_qty=Decimal("5000000.000"),
        base_unit="g",
        raw_unit="kg",
        unit_price_paise=4000,
        line_total_paise=20000,
    )
    assert chawal_check.is_valid is False
    assert chawal_check.needs_review is True
    assert chawal_check.code == "QUANTITY_PRICE_MISMATCH"

    valid_chawal = validate_receipt_quantity_against_price(
        normalized_qty=Decimal("5000.000"),
        base_unit="g",
        raw_unit="kg",
        unit_price_paise=4000,
        line_total_paise=20000,
    )
    assert valid_chawal.is_valid is True


@pytest.mark.asyncio
async def test_phase5a_soft_merge_chana_bal_potato_groups_and_isolation(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
    temp_db_url: str,
) -> None:
    """Covers scenarios 7, 8, 12-18: Chana Bal merge visibility, potatoes kg+pcs,
    repeated confirm, distinct brands/variants, household isolation, and price history.
    """
    token_a = jwks_provider.make_token(sub="user_5a_a", email="5aa@example.com", name="5A A")
    token_b = jwks_provider.make_token(sub="user_5a_b", email="5ab@example.com", name="5A B")
    hdr_a = {"Authorization": f"Bearer {token_a}"}
    hdr_b = {"Authorization": f"Bearer {token_b}"}

    me_a = await client.get("/api/v1/auth/me", headers=hdr_a)
    me_b = await client.get("/api/v1/auth/me", headers=hdr_b)
    hh_a = UUID(me_a.json()["active_household"]["id"])
    hdr_a["X-Household-Id"] = str(hh_a)
    hdr_b["X-Household-Id"] = me_b.json()["active_household"]["id"]

    # 11. CHAWAL 1,000x mismatch via purchase creation API -> blocked from auto-tracking inventory
    bad_p = await client.post(
        "/api/v1/purchases",
        headers=hdr_a,
        json={
            "merchant_name": "AGGARWAL TRADERS",
            "purchased_on": "2026-11-18",
            "items": [
                {
                    "display_name": "CHAWAL",
                    "category": "grain",
                    "qty": 5000000,
                    "unit": "g",
                    "unit_price_paise": 4000,
                    "line_total_paise": 20000,
                    "track_inventory": True,
                }
            ],
            "total_amount_paise": 20000,
        },
    )
    assert bad_p.status_code == 201
    assert bad_p.json()["items"][0]["track_inventory"] is False

    # 12. Valid purchase confirm + repeated confirm -> 409
    good_p = await client.post(
        "/api/v1/purchases",
        headers=hdr_a,
        json={
            "merchant_name": "AGGARWAL TRADERS",
            "purchased_on": "2026-11-18",
            "items": [
                {
                    "display_name": "Chana Dal",
                    "category": "pantry",
                    "qty": 2,
                    "unit": "kg",
                    "unit_price_paise": 9600,
                    "line_total_paise": 19200,
                    "track_inventory": True,
                }
            ],
            "total_amount_paise": 19200,
        },
    )
    assert good_p.status_code == 201
    good_pid = good_p.json()["id"]
    c1 = await client.post(f"/api/v1/purchases/{good_pid}/confirm", headers=hdr_a, json={})
    assert c1.status_code == 200
    c2 = await client.post(f"/api/v1/purchases/{good_pid}/confirm", headers=hdr_a, json={})
    assert c2.status_code == 409

    chana_surv_id = UUID(c1.json()["created_lots"][0]["canonical_item_id"])

    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        # 13 & 14. Create "• Chana Bal" with 1000g lot and merge into Chana Dal
        chana_bal = CanonicalItem(
            household_id=hh_a,
            slug="chana-bal",
            name="• Chana Bal",
            category="pantry",
            default_base_unit="g",
        )
        pot_g = CanonicalItem(
            household_id=hh_a,
            slug="potatoes-kg",
            name="Potatoes 2kg",
            category="vegetable",
            default_base_unit="g",
        )
        pot_pcs = CanonicalItem(
            household_id=hh_a,
            slug="potatoes-pcs",
            name="Potatoes 5pcs",
            category="vegetable",
            default_base_unit="pcs",
        )
        db.add_all([chana_bal, pot_g, pot_pcs])
        await db.flush()
        now = datetime.now(UTC)
        db.add_all(
            [
                InventoryLot(
                    household_id=hh_a,
                    canonical_item_id=chana_bal.id,
                    initial_qty=Decimal("1000.000"),
                    remaining_qty=Decimal("1000.000"),
                    base_unit="g",
                    unit_cost_paise=9000,
                    purchased_at=now,
                    lot_status="active",
                ),
                InventoryLot(
                    household_id=hh_a,
                    canonical_item_id=pot_g.id,
                    initial_qty=Decimal("2000.000"),
                    remaining_qty=Decimal("2000.000"),
                    base_unit="g",
                    unit_cost_paise=6000,
                    purchased_at=now,
                    lot_status="active",
                ),
                InventoryLot(
                    household_id=hh_a,
                    canonical_item_id=pot_pcs.id,
                    initial_qty=Decimal("5.000"),
                    remaining_qty=Decimal("5.000"),
                    base_unit="pcs",
                    unit_cost_paise=2500,
                    purchased_at=now,
                    lot_status="active",
                ),
            ]
        )
        await merge_canonical_products(
            db, household_id=hh_a, survivor_id=chana_surv_id, duplicate_ids=[chana_bal.id]
        )
        await db.commit()
        pot_g_id, pot_pcs_id = str(pot_g.id), str(pot_pcs.id)

        # 15 & 16. Distinct brands & variants remain separate
        r_var = await resolve_product_identity(
            db, household_id=hh_a, raw_description="Moong Sabut", base_unit="g"
        )
        assert r_var.outcome == "create_new"

    # 13 & 14. Chana Bal must NOT appear in /summary or /catalog/items; Chana Dal has 3000g once
    sum_res = (await client.get("/api/v1/inventory/summary", headers=hdr_a)).json()["items"]
    names = [x["name"] for x in sum_res]
    assert "• Chana Bal" not in names
    chana_entry = [x for x in sum_res if x["canonical_item_id"] == str(chana_surv_id)][0]
    assert Decimal(chana_entry["total_remaining_qty"]) == Decimal("3000.000")

    cat_res = (await client.get("/api/v1/catalog/items?limit=100", headers=hdr_a)).json()["items"]
    assert all(x["name"] != "• Chana Bal" for x in cat_res)

    # 7 & 8. Potatoes in g and pcs remain separate unit groups under Potato ingredient
    ing_cat = (await client.get("/api/v1/inventory/ingredients/catalog", headers=hdr_a)).json()[
        "items"
    ]
    pot_ing_id = [x["id"] for x in ing_cat if x["normalized_name"] == "potato"][0]
    for pid in (pot_g_id, pot_pcs_id):
        assert (
            await client.post(
                "/api/v1/inventory/ingredients/mappings/confirm",
                headers=hdr_a,
                json={"canonical_item_id": pid, "canonical_ingredient_id": pot_ing_id},
            )
        ).status_code == 200

    ing_sum = (await client.get("/api/v1/inventory/ingredients/summary", headers=hdr_a)).json()[
        "ingredients"
    ]
    pot_sum = [x for x in ing_sum if x["normalized_name"] == "potato"][0]
    assert pot_sum["unit_completeness_status"] == "incomplete"
    assert pot_sum["total_remaining_qty"] is None
    assert {g["unit"]: g["total_qty"] for g in pot_sum["unit_groups"]} == {
        "g": "2000.000",
        "pcs": "5.000",
    }

    # 17. Household B isolation
    sum_b = (await client.get("/api/v1/inventory/summary", headers=hdr_b)).json()["items"]
    assert sum_b == []

    # 18. Price history preserved
    ph = await client.get(f"/api/v1/inventory/items/{chana_surv_id}/price-history", headers=hdr_a)
    assert ph.status_code == 200

    await engine.dispose()


@pytest.mark.asyncio
async def test_phase1_qty_price_safeguards_and_fresh_tom_pkt_regression(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    """Regression coverage for Failure #9 (HIGH confidence must not bypass qty/price check)
    and Failure #2 (Fresh Tom Pkt 500g must remain pantry_eligible without false mismatch).
    """
    token = jwks_provider.make_token(sub="user_p1_reg", email="p1reg@example.com", name="P1Reg")
    hdr = {"Authorization": f"Bearer {token}"}
    me = await client.get("/api/v1/auth/me", headers=hdr)
    assert me.status_code == 200
    hdr["X-Household-Id"] = me.json()["active_household"]["id"]

    # 1. Manual draft: valid HIGH-confidence, invalid HIGH-confidence, valid MEDIUM-confidence
    m_res = await client.post(
        "/api/v1/purchases",
        headers=hdr,
        json={
            "merchant_name": "Test Kirana",
            "purchased_on": "2026-11-22",
            "items": [
                {
                    "display_name": "Basmati Rice",
                    "category": "grain",
                    "qty": 1,
                    "unit": "kg",
                    "unit_price_paise": 12000,
                    "line_total_paise": 12000,
                    "track_inventory": True,
                },
                {
                    "display_name": "Chana Dal",
                    "category": "pulse",
                    "qty": 5000000,
                    "unit": "g",
                    "unit_price_paise": 4000,
                    "line_total_paise": 20000,
                    "track_inventory": True,
                },
                {
                    "display_name": "Whole Wheat",
                    "category": "grain",
                    "qty": 500,
                    "unit": "g",
                    "unit_price_paise": 5000,
                    "line_total_paise": 2500,
                    "track_inventory": True,
                },
            ],
            "total_amount_paise": 34500,
        },
    )
    assert m_res.status_code == 201
    m_items = m_res.json()["items"]
    assert m_items[0]["track_inventory"] is True
    assert m_items[1]["track_inventory"] is False
    assert m_items[2]["ingredient_confidence_tier"] != "HIGH"
    assert m_items[2]["track_inventory"] is True

    m_conf = await client.post(
        f"/api/v1/purchases/{m_res.json()['id']}/confirm", headers=hdr, json={}
    )
    assert m_conf.status_code == 200
    # Invalid Chana Dal (1,000x mismatch) must NOT enter confirmed inventory
    inv_sum = (await client.get("/api/v1/inventory/summary", headers=hdr)).json()["items"]
    lot_names = {it["name"] for it in inv_sum}
    assert "Basmati Rice" in lot_names
    assert "Whole Wheat" in lot_names
    assert "Chana Dal" not in lot_names

    # 2. OCR draft: invalid qty/price on HIGH-confidence item + exact Fresh Tom Pkt 500g line
    ocr_res = await client.post(
        "/api/v1/purchases/ocr",
        headers=hdr,
        json={
            "raw_text": (
                "SUPER MART\n"
                "Fresh Tom Pkt 500g 1 x 30.00 30.00\n"
                "Total: 30.00\n"
            )
        },
    )
    assert ocr_res.status_code == 201
    tom_item = ocr_res.json()["items"][0]
    assert tom_item["classification_status"] == "pantry_eligible"
    assert tom_item["track_inventory"] is True

    ocr_bad = await client.post(
        "/api/v1/purchases/ocr",
        headers=hdr,
        json={
            "raw_text": (
                "SUPER MART\n"
                "Turmeric Powder 5000 kg 40.00 200.00\n"
                "Total: 200.00\n"
            )
        },
    )
    assert ocr_bad.status_code == 201
    assert ocr_bad.json()["items"][0]["track_inventory"] is False
    ocr_bad_conf = await client.post(
        f"/api/v1/purchases/drafts/{ocr_bad.json()['id']}/confirm",
        headers={**hdr, "Idempotency-Key": "k-ocr-bad-1"},
        json={},
    )
    assert ocr_bad_conf.status_code == 200
    assert ocr_bad_conf.json()["created_lots"] == []
