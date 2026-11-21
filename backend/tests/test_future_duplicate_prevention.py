"""Phase 5: Comprehensive tests for future product duplicate prevention (20 scenarios)."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.modules.catalog.product_identity_service import (
    get_or_create_canonical_item,
    learn_confirmed_product_alias,
    resolve_product_identity,
)
from app.modules.catalog.product_merge_service import merge_canonical_products
from app.modules.inventory.models import CanonicalItem, InventoryLot, ItemAlias, StockLedger
from tests.conftest import EphemeralJWKSProvider


@pytest.mark.asyncio
async def test_future_duplicate_prevention_20_scenarios(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
    temp_db_url: str,
) -> None:
    """Covers all 20 Phase 5 duplicate-prevention scenarios."""
    token_a = jwks_provider.make_token(sub="user_5_a", email="5a@example.com", name="User 5A")
    token_b = jwks_provider.make_token(sub="user_5_b", email="5b@example.com", name="User 5B")
    hdr_a = {"Authorization": f"Bearer {token_a}"}
    hdr_b = {"Authorization": f"Bearer {token_b}"}

    me_a = await client.get("/api/v1/auth/me", headers=hdr_a)
    me_b = await client.get("/api/v1/auth/me", headers=hdr_b)
    assert me_a.status_code == 200 and me_b.status_code == 200
    hh_a = UUID(me_a.json()["active_household"]["id"])
    hh_b = UUID(me_b.json()["active_household"]["id"])
    hdr_a["X-Household-Id"] = str(hh_a)
    hdr_b["X-Household-Id"] = str(hh_b)

    # 6. Manual pantry creation -> receipt confirmation reuses same CanonicalItem
    cat_res = await client.post(
        "/api/v1/catalog/items",
        headers=hdr_a,
        json={"name": "Chana Dal", "category": "pantry", "default_base_unit": "g"},
    )
    assert cat_res.status_code == 201
    chana_id = cat_res.json()["id"]

    # 1 & 2. Same product with different capitalization & punctuation via manual catalog endpoint
    cat_cap = await client.post(
        "/api/v1/catalog/items",
        headers=hdr_a,
        json={"name": "CHANA-DAL!!", "category": "pantry", "default_base_unit": "g"},
    )
    assert cat_cap.status_code == 201
    assert cat_cap.json()["id"] == chana_id

    # 5 & 20. Same product across multiple receipts + repeated confirm idempotency (409)
    async def confirm_receipt(name: str, qty: float, unit: str, cat: str = "pantry") -> dict:
        p = await client.post(
            "/api/v1/purchases",
            headers=hdr_a,
            json={
                "merchant_name": "FreshMart",
                "purchased_on": "2026-11-20",
                "items": [
                    {
                        "display_name": name,
                        "category": cat,
                        "qty": qty,
                        "unit": unit,
                        "unit_price_paise": 10000,
                        "line_total_paise": 10000,
                        "track_inventory": True,
                    }
                ],
                "total_amount_paise": 10000,
            },
        )
        assert p.status_code == 201
        pid = p.json()["id"]
        c1 = await client.post(f"/api/v1/purchases/{pid}/confirm", headers=hdr_a, json={})
        assert c1.status_code == 200
        c2 = await client.post(f"/api/v1/purchases/{pid}/confirm", headers=hdr_a, json={})
        assert c2.status_code == 409  # 20. No duplicate confirm
        return c1.json()

    c_r1 = await confirm_receipt("Chana Dal 1kg", 1000, "g")
    c_r2 = await confirm_receipt("CHANER DAL 2", 2000, "g")  # 3. OCR spelling variation -> resolves to Chana Dal!
    assert c_r1["created_lots"][0]["canonical_item_id"] == chana_id
    assert c_r2["created_lots"][0]["canonical_item_id"] == chana_id

    # Verify DB resolution & guard rules directly
    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        # Seeded canonical items for product-level distinction checks
        amul_fc = CanonicalItem(
            household_id=hh_a,
            slug="amul-full-cream-milk-500ml",
            name="Amul Full Cream Milk 500ml",
            category="dairy",
            default_base_unit="ml",
        )
        akhrot = CanonicalItem(
            household_id=hh_a,
            slug="akhrot",
            name="AKHROT",
            category="pantry",
            default_base_unit="g",
        )
        moong_dhuli = CanonicalItem(
            household_id=hh_a,
            slug="moong-dhuli",
            name="Moong Dhuli",
            category="pantry",
            default_base_unit="g",
        )
        basmati = CanonicalItem(
            household_id=hh_a,
            slug="basmati-rice",
            name="Basmati Rice",
            category="grain",
            default_base_unit="g",
        )
        dup_akhrot = CanonicalItem(
            household_id=hh_a,
            slug="akrot-dup",
            name="Akrot Old",
            category="pantry",
            default_base_unit="g",
        )
        db.add_all([amul_fc, akhrot, moong_dhuli, basmati, dup_akhrot])
        await db.flush()

        # 3. OCR spelling variations
        res_akrot = await resolve_product_identity(
            db, household_id=hh_a, raw_description="Akrot 500g", base_unit="g"
        )
        assert res_akrot.outcome == "match_existing"
        assert res_akrot.resolved_canonical_item_id == akhrot.id

        res_dhyli = await resolve_product_identity(
            db, household_id=hh_a, raw_description="10 Moong Dhyli 1kg", base_unit="g"
        )
        assert res_dhyli.outcome == "match_existing"
        assert res_dhyli.resolved_canonical_item_id == moong_dhuli.id

        # 4 & 13. Confirmed alias reuse + conflicting alias protection
        await learn_confirmed_product_alias(
            db, household_id=hh_a, canonical_item_id=akhrot.id, raw_description="Kashmiri Walnut"
        )
        # Attempt conflicting alias for same normalized_alias on a different CanonicalItem -> preserved for akhrot
        alias_conflict = await learn_confirmed_product_alias(
            db, household_id=hh_a, canonical_item_id=moong_dhuli.id, raw_description="Kashmiri Walnut"
        )
        assert alias_conflict is not None
        assert alias_conflict.canonical_item_id == akhrot.id

        res_alias = await resolve_product_identity(
            db, household_id=hh_a, raw_description="Kashmiri Walnut", base_unit="g"
        )
        assert res_alias.resolved_canonical_item_id == akhrot.id

        # 7. Same ingredient, different brands -> separate
        res_brand = await resolve_product_identity(
            db, household_id=hh_a, raw_description="Aavin Full Cream Milk 500ml", base_unit="ml"
        )
        assert res_brand.outcome != "match_existing"

        # 8. Same brand, different variants -> separate
        res_var = await resolve_product_identity(
            db, household_id=hh_a, raw_description="Amul Toned Milk 500ml", base_unit="ml"
        )
        assert res_var.outcome != "match_existing"

        # 9 & 16. Different package sizes -> medium review_required, not silently merged
        res_pack = await resolve_product_identity(
            db, household_id=hh_a, raw_description="Amul Full Cream Milk 1L", base_unit="ml"
        )
        assert res_pack.outcome == "review_required"
        assert res_pack.tier == "medium"

        # 10. Rice vs Rice Flour -> separate
        res_rf = await resolve_product_identity(
            db, household_id=hh_a, raw_description="Rice Flour 500g", base_unit="g"
        )
        assert res_rf.outcome == "create_new"

        # 11. Basmati Rice vs Idli Rice -> separate
        res_idli = await resolve_product_identity(
            db, household_id=hh_a, raw_description="Idli Rice 5kg", base_unit="g"
        )
        assert res_idli.outcome == "create_new"

        # 12. Whole Dal vs Split Dal -> separate
        res_sabut = await resolve_product_identity(
            db, household_id=hh_a, raw_description="Moong Sabut 1kg", base_unit="g"
        )
        assert res_sabut.outcome == "create_new"

        # 14. Household isolation
        res_hhb = await resolve_product_identity(
            db, household_id=hh_b, raw_description="Kashmiri Walnut", base_unit="g"
        )
        assert res_hhb.outcome == "create_new"

        # 17. Low-confidence candidate -> create_new
        res_low = await resolve_product_identity(
            db, household_id=hh_a, raw_description="Artisan Truffle Paste 250g", base_unit="g"
        )
        assert res_low.outcome == "create_new"
        assert res_low.tier == "low"

        # 18. Soft-merged product alias resolves to survivor
        await learn_confirmed_product_alias(
            db, household_id=hh_a, canonical_item_id=dup_akhrot.id, raw_description="Akrot Legacy Alias"
        )
        await merge_canonical_products(
            db, household_id=hh_a, survivor_id=akhrot.id, duplicate_ids=[dup_akhrot.id]
        )
        res_merged = await resolve_product_identity(
            db, household_id=hh_a, raw_description="Akrot Legacy Alias", base_unit="g"
        )
        assert res_merged.outcome == "match_existing"
        assert res_merged.resolved_canonical_item_id == akhrot.id
        await db.commit()

    # 15. Concurrent creation of the same product -> only 1 CanonicalItem created
    async def create_worker(name: str):
        async with Session() as db:
            item, _res, _created = await get_or_create_canonical_item(
                db,
                household_id=hh_a,
                raw_name=name,
                category="pantry",
                default_base_unit="g",
            )
            await db.commit()
            return item.id

    id1, id2 = await asyncio.gather(
        create_worker("Organic Quinoa"),
        create_worker("organic quinoa"),
    )
    assert id1 == id2

    async with Session() as db:
        q_cnt = (
            await db.execute(
                select(func.count())
                .select_from(CanonicalItem)
                .where(CanonicalItem.household_id == hh_a, CanonicalItem.slug == "organic-quinoa")
            )
        ).scalar_one()
        assert q_cnt == 1

    # 19. Historical purchase records & consolidated summary intact
    sum_a = await client.get("/api/v1/inventory/summary", headers=hdr_a)
    assert sum_a.status_code == 200
    chana_summary = [x for x in sum_a.json()["items"] if x["canonical_item_id"] == chana_id][0]
    assert Decimal(chana_summary["total_remaining_qty"]) == Decimal("3000.000")
    assert chana_summary["active_lot_count"] == 2

    await engine.dispose()
