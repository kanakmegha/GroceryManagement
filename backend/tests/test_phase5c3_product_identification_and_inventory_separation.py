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
async def test_phase5c3_product_identification_vs_inventory_actions_separation(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
    temp_db_url: str,
) -> None:
    """Covers Part G: name edit, mapping confirm/reject, consumption/wastage/restock separation,
    isolation.
    """
    tok_a = jwks_provider.make_token(sub="user_5c3_a", email="5c3a@example.com", name="5C3 A")
    tok_b = jwks_provider.make_token(sub="user_5c3_b", email="5c3b@example.com", name="5C3 B")
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
        apple_ing = (
            await db.execute(
                select(CanonicalIngredient).where(CanonicalIngredient.normalized_name == "apple")
            )
        ).scalar_one()

        item = CanonicalItem(
            household_id=hh_a,
            slug="raw-apple-5c3",
            name="mported Daily Apple (Sebu) R$",
            category="produce",
            default_base_unit="pcs",
        )
        db.add(item)
        await db.flush()

        p = Purchase(
            household_id=hh_a,
            created_by=user_a,
            merchant_name="FreshMart",
            purchased_on=date.today(),
            receipt_category="grocery",
            total_amount_paise=15000,
            status="confirmed",
        )
        db.add(p)
        await db.flush()
        pi = PurchaseItem(
            purchase_id=p.id,
            household_id=hh_a,
            line_index=0,
            raw_text="mported Daily Apple (Sebu) R$ 150.00",
            canonical_item_id=item.id,
            display_name="mported Daily Apple (Sebu) R$",
            normalized_name="mported daily apple sebu",
            normalized_qty=Decimal("5.000"),
            base_unit="pcs",
            unit_price_paise=3000,
            line_total_paise=15000,
            track_inventory=True,
        )
        lot = InventoryLot(
            household_id=hh_a,
            canonical_item_id=item.id,
            initial_qty=Decimal("5.000"),
            remaining_qty=Decimal("5.000"),
            base_unit="pcs",
            unit_cost_paise=3000,
            lot_status="active",
        )
        db.add_all([pi, lot])
        db.add(
            IngredientMapping(
                household_id=hh_a,
                canonical_item_id=item.id,
                canonical_ingredient_id=apple_ing.id,
                matching_confidence=Decimal("0.850"),
                mapping_source="catalogue",
                user_confirmed=False,
            )
        )
        await db.commit()
        item_id, lot_id, pi_id, apple_ing_id = item.id, lot.id, pi.id, apple_ing.id

    # 1. Household isolation: Household B cannot rename Household A's product
    iso_res = await client.patch(
        f"/api/v1/inventory/items/{item_id}",
        headers=hdr_b,
        json={"name": "Hacked Apple"},
    )
    assert iso_res.status_code == 404

    # 2. Edit product name preserves raw OCR and does not change stock
    ren_res = await client.patch(
        f"/api/v1/inventory/items/{item_id}",
        headers=hdr_a,
        json={"name": "Imported Daily Apple (Sebu)"},
    )
    assert ren_res.status_code == 200
    assert ren_res.json()["name"] == "Imported Daily Apple (Sebu)"

    # 3. Reject mapping does not alter stock
    rej_res = await client.post(
        "/api/v1/inventory/ingredients/mappings/reject",
        headers=hdr_a,
        json={"canonical_item_id": str(item_id)},
    )
    assert rej_res.status_code == 200

    # 4. Confirm mapping removes item from unmapped_products without altering stock
    conf_res = await client.post(
        "/api/v1/inventory/ingredients/mappings/confirm",
        headers=hdr_a,
        json={"canonical_item_id": str(item_id), "canonical_ingredient_id": str(apple_ing_id)},
    )
    assert conf_res.status_code == 200
    ing_sum = (await client.get("/api/v1/inventory/ingredients/summary", headers=hdr_a)).json()
    assert [x["name"] for x in ing_sum["ingredients"]] == ["Apple"]
    assert ing_sum["ingredients"][0]["unit_completeness_status"] == "incomplete"
    assert ing_sum["ingredients"][0]["total_remaining_qty"] is None
    assert Decimal(ing_sum["ingredients"][0]["unit_groups"][0]["total_qty"]) == Decimal("5.000")
    assert ing_sum["unmapped_products"] == []

    # 5. Inventory actions (consume/adjust -1, waste -1, restock +2) change stock with StockLedger
    # audit trail, not mapping
    c_res = await client.post(
        f"/api/v1/inventory/lots/{lot_id}/adjust",
        headers=hdr_a,
        json={"event_type": "ADJUSTMENT_OUT", "quantity_delta": -1, "notes": "Snack"},
    )
    assert c_res.status_code == 200
    w_res = await client.post(
        f"/api/v1/inventory/lots/{lot_id}/adjust",
        headers=hdr_a,
        json={"event_type": "WASTE_OUT", "quantity_delta": -1, "notes": "Spoiled"},
    )
    assert w_res.status_code == 200
    r_res = await client.post(
        f"/api/v1/inventory/lots/{lot_id}/adjust",
        headers=hdr_a,
        json={"event_type": "ADJUSTMENT_IN", "quantity_delta": 2, "notes": "Restock"},
    )
    assert r_res.status_code == 200

    ing_after = (await client.get("/api/v1/inventory/ingredients/summary", headers=hdr_a)).json()
    assert [x["name"] for x in ing_after["ingredients"]] == ["Apple"]
    assert Decimal(ing_after["ingredients"][0]["unit_groups"][0]["total_qty"]) == Decimal(
        "5.000"
    )  # 5 - 1 - 1 + 2 = 5

    # Raw OCR evidence untouched & ledger entries recorded
    async with Session() as db:
        pi_row = await db.get(PurchaseItem, pi_id)
        assert pi_row is not None and pi_row.raw_text == "mported Daily Apple (Sebu) R$ 150.00"
        ledgers = (
            (await db.execute(select(StockLedger).where(StockLedger.lot_id == lot_id)))
            .scalars()
            .all()
        )
        assert len(ledgers) == 3

    await engine.dispose()
