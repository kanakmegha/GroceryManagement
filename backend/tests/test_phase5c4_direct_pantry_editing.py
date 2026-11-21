from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.modules.inventory.models import (
    CanonicalIngredient,
    CanonicalItem,
    IngredientMapping,
    InventoryLot,
    StockLedger,
)
from app.modules.purchases.models import Purchase, PurchaseItem
from tests.conftest import EphemeralJWKSProvider


@pytest.mark.asyncio
async def test_phase5c4_direct_ingredient_edit_rename_and_stock_adjustment(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
    temp_db_url: str,
) -> None:
    """Covers Part F (1-13): rename, duplicate guard, FEFO stock increase/decrease, unit safety, isolation."""
    tok_a = jwks_provider.make_token(sub="user_5c4_a", email="5c4a@example.com", name="5C4 A")
    tok_b = jwks_provider.make_token(sub="user_5c4_b", email="5c4b@example.com", name="5C4 B")
    hdr_a = {"Authorization": f"Bearer {tok_a}"}
    hdr_b = {"Authorization": f"Bearer {tok_b}"}

    me_a = await client.get("/api/v1/auth/me", headers=hdr_a)
    me_b = await client.get("/api/v1/auth/me", headers=hdr_b)
    hh_a = UUID(me_a.json()["active_household"]["id"])
    user_a = UUID(me_a.json()["user"]["id"])
    hh_b = UUID(me_b.json()["active_household"]["id"])
    hdr_a["X-Household-Id"] = str(hh_a)
    hdr_b["X-Household-Id"] = str(hh_b)

    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        milk_ing = (
            await db.execute(select(CanonicalIngredient).where(CanonicalIngredient.normalized_name == "milk"))
        ).scalar_one()
        chana_ing = (
            await db.execute(select(CanonicalIngredient).where(CanonicalIngredient.normalized_name == "chana_dal"))
        ).scalar_one()

        aavin = CanonicalItem(
            household_id=hh_a, slug="aavin-5c4", name="Aavin Full Cream Milk",
            category="dairy", default_base_unit="ml",
        )
        amul = CanonicalItem(
            household_id=hh_a, slug="amul-5c4", name="Amul Gold Milk",
            category="dairy", default_base_unit="ml",
        )
        apples = CanonicalItem(
            household_id=hh_a, slug="apples-5c4", name="Fresh Apples",
            category="produce", default_base_unit="pcs",
        )
        db.add_all([aavin, amul, apples])
        await db.flush()

        p = Purchase(
            household_id=hh_a, created_by=user_a, merchant_name="FreshMart",
            purchased_on=date.today(), receipt_category="grocery", total_amount_paise=20000, status="confirmed",
        )
        db.add(p)
        await db.flush()
        pi = PurchaseItem(
            purchase_id=p.id, household_id=hh_a, line_index=0,
            raw_text="Aavin Full Cream Milk 1 L Pouch R$ 60",
            canonical_item_id=aavin.id, display_name="Aavin Full Cream Milk",
            normalized_name="aavin full cream milk", normalized_qty=Decimal("1000.000"),
            base_unit="ml", unit_price_paise=6000, line_total_paise=6000, track_inventory=True,
        )
        lot_1 = InventoryLot(
            household_id=hh_a, canonical_item_id=aavin.id,
            initial_qty=Decimal("1000.000"), remaining_qty=Decimal("1000.000"),
            base_unit="ml", unit_cost_paise=6000, lot_status="active",
        )
        lot_2 = InventoryLot(
            household_id=hh_a, canonical_item_id=amul.id,
            initial_qty=Decimal("1500.000"), remaining_qty=Decimal("1500.000"),
            base_unit="ml", unit_cost_paise=7000, lot_status="active",
        )
        lot_app = InventoryLot(
            household_id=hh_a, canonical_item_id=apples.id,
            initial_qty=Decimal("4.000"), remaining_qty=Decimal("4.000"),
            base_unit="pcs", unit_cost_paise=2500, lot_status="active",
        )
        db.add_all([pi, lot_1, lot_2, lot_app])
        db.add_all([
            IngredientMapping(
                household_id=hh_a, canonical_item_id=aavin.id, canonical_ingredient_id=milk_ing.id,
                matching_confidence=Decimal("1.000"), mapping_source="user_confirmed", user_confirmed=True,
            ),
            IngredientMapping(
                household_id=hh_a, canonical_item_id=amul.id, canonical_ingredient_id=milk_ing.id,
                matching_confidence=Decimal("1.000"), mapping_source="user_confirmed", user_confirmed=True,
            ),
        ])
        await db.commit()
        milk_id, chana_id, aavin_id, pi_id = milk_ing.id, chana_ing.id, aavin.id, pi.id

    # 13. Household isolation: Household B cannot edit Household A's ingredient
    iso = await client.patch(
        f"/api/v1/inventory/ingredients/{milk_id}",
        headers=hdr_b,
        json={"name": "Hacked Milk"},
    )
    assert iso.status_code == 404

    # 4. Duplicate ingredient name rejected (409)
    dup = await client.patch(
        f"/api/v1/inventory/ingredients/{milk_id}",
        headers=hdr_a,
        json={"name": "Chana Dal"},
    )
    assert dup.status_code == 409

    # 9 & 10. Incompatible unit rejected (422) with NO partial save on name
    incomp = await client.patch(
        f"/api/v1/inventory/ingredients/{milk_id}",
        headers=hdr_a,
        json={"name": "Dairy Milk", "target_quantity": 3, "unit": "pcs"},
    )
    assert incomp.status_code == 422
    check_after_err = (await client.get("/api/v1/inventory/ingredients/summary", headers=hdr_a)).json()
    assert check_after_err["ingredients"][0]["name"] == "Milk"

    # 1, 2, 5, 8. Rename Milk -> Dairy Milk and increase 2500 ml -> 3 L (3000 ml)
    inc = await client.patch(
        f"/api/v1/inventory/ingredients/{milk_id}",
        headers=hdr_a,
        json={"name": "Dairy Milk", "target_quantity": 3, "unit": "l"},
    )
    assert inc.status_code == 200
    assert inc.json()["name"] == "Dairy Milk"
    assert Decimal(inc.json()["total_remaining_qty"]) == Decimal("3000.000")

    # 6 & 7. Decrease 3000 ml -> 1.5 L (1500 ml) via FEFO StockLedger adjustments
    dec = await client.patch(
        f"/api/v1/inventory/ingredients/{milk_id}",
        headers=hdr_a,
        json={"name": "Dairy Milk", "target_quantity": 1.5, "unit": "l"},
    )
    assert dec.status_code == 200
    assert Decimal(dec.json()["total_remaining_qty"]) == Decimal("1500.000")

    ing_sum = (await client.get("/api/v1/inventory/ingredients/summary", headers=hdr_a)).json()
    assert len(ing_sum["ingredients"]) == 1
    assert ing_sum["ingredients"][0]["name"] == "Dairy Milk"
    assert ing_sum["ingredients"][0]["contributing_product_count"] == 2
    assert Decimal(ing_sum["ingredients"][0]["total_remaining_qty"]) == Decimal("1500.000")

    # 12. Product-level rename does not rename CanonicalIngredient
    prod_ren = await client.patch(
        f"/api/v1/inventory/items/{aavin_id}",
        headers=hdr_a,
        json={"name": "Aavin Green Magic Milk"},
    )
    assert prod_ren.status_code == 200
    ing_sum2 = (await client.get("/api/v1/inventory/ingredients/summary", headers=hdr_a)).json()
    assert ing_sum2["ingredients"][0]["name"] == "Dairy Milk"

    # 11. Raw OCR & purchase history remain unchanged; StockLedger records audit trail
    async with Session() as db:
        pi_row = await db.get(PurchaseItem, pi_id)
        assert pi_row is not None and pi_row.raw_text == "Aavin Full Cream Milk 1 L Pouch R$ 60"
        ledgers = (
            await db.execute(
                select(StockLedger).where(
                    StockLedger.household_id == hh_a,
                    StockLedger.notes == "User-requested pantry quantity correction",
                )
            )
        ).scalars().all()
        assert len(ledgers) >= 2

    await engine.dispose()
