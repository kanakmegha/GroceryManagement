from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.units import resolve_purchase_measurement
from app.modules.catalog.product_naming_service import standardize_product_name
from app.modules.inventory.models import CanonicalIngredient, CanonicalItem, IngredientMapping
from app.modules.purchases.models import PurchaseItem
from tests.conftest import EphemeralJWKSProvider


def test_phase5c1_measurement_normalization_and_multipack_rules() -> None:
    """Covers Part D Examples 1-6: kg, g, multipack, unknown weight, ml/L, pcs."""
    # Example 1: Chana Dal 1 kg -> 1000 g
    assert resolve_purchase_measurement(1, "kg") == (Decimal("1000.000"), "g")
    # Example 2: Chana Dal 500 g -> 500 g
    assert resolve_purchase_measurement(500, "g") == (Decimal("500.000"), "g")
    # Example 3: Chana Dal 2 packs, 500 g each -> 1000 g
    assert resolve_purchase_measurement(2, "packs", "500 g") == (Decimal("1000.000"), "g")
    # Example 4: Chana Dal 1 pc, no weight available -> preserve 1 pcs, do not invent weight
    assert resolve_purchase_measurement(1, "pc", None) == (Decimal("1.000"), "pcs")
    # Example 5: Milk 2 packets, 500 ml each -> 1000 ml
    assert resolve_purchase_measurement(2, "packets", "500 ml") == (Decimal("1000.000"), "ml")
    # Example 6: Apples 3 pcs -> 3 pcs
    assert resolve_purchase_measurement(3, "pcs", None) == (Decimal("3.000"), "pcs")


def test_phase5c1_product_naming_and_variants() -> None:
    """Covers OCR currency/special char cleanup, balanced parens, and variant preservation."""
    assert (
        standardize_product_name("1 resh Tender Coconut (Elaneer)").display_name == "Tender Coconut"
    )
    assert (
        standardize_product_name("Amul Milk Chocolate- Smooth & Creamy 150 g Carton").display_name
        == "Amul Milk Chocolate"
    )
    assert (
        standardize_product_name("Green Grapes (Thraakshi) R$").display_name
        == "Green Grapes (Thraakshi)"
    )
    assert (
        standardize_product_name("Eastern Premium Hing (Compounded Asafoetida)").display_name
        == "Eastern Premium Hing (Compounded Asafoetida)"
    )
    # Variants remain distinct
    assert standardize_product_name("Moong Dal (Split)").display_name == "Moong Dal (Split)"
    assert standardize_product_name("Moong Sabut").display_name == "Moong Sabut"


@pytest.mark.asyncio
async def test_phase5c1_end_to_end_receipt_to_ingredient_summary(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
    temp_db_url: str,
) -> None:
    """Covers receipt -> purchase item -> canonical item -> mapping -> lot -> ingredient summary &
    price history.
    """
    token = jwks_provider.make_token(sub="user_5c1", email="5c1@example.com", name="5C1 User")
    hdr = {"Authorization": f"Bearer {token}"}
    me = await client.get("/api/v1/auth/me", headers=hdr)
    assert me.status_code == 200
    hh_id = UUID(me.json()["active_household"]["id"])
    hdr["X-Household-Id"] = str(hh_id)

    # Create a receipt draft with multipack Milk (2 packets x 500 ml), Chana Dal 1 kg, and
    # unmapped Apple 3 pcs
    draft_res = await client.post(
        "/api/v1/purchases/drafts",
        headers=hdr,
        json={
            "merchant_name": "FreshMart",
            "purchased_on": "2026-11-20",
            "receipt_category": "grocery",
            "total_amount_paise": 35000,
            "items": [
                {
                    "raw_text": "Aavin Full Cream Milk 500 ml R$ 60.00",
                    "display_name": "Aavin Full Cream Milk 500 ml",
                    "qty": 2,
                    "unit": "packets",
                    "pack_size": "500 ml",
                    "line_total_paise": 6000,
                },
                {
                    "raw_text": "Amul Gold Milk 1 L",
                    "display_name": "Amul Gold Milk 1 L",
                    "qty": 1,
                    "unit": "l",
                    "line_total_paise": 7000,
                },
                {
                    "raw_text": "8 Chana-Đal 1 KG 110: 110.00",
                    "display_name": "8 Chana-Đal 1 KG",
                    "qty": 1,
                    "unit": "kg",
                    "line_total_paise": 11000,
                },
                {
                    "raw_text": "mported Daily Apple (Sebu) R$ 110.00",
                    "display_name": "mported Daily Apple (Sebu) R$",
                    "qty": 3,
                    "unit": "pcs",
                    "line_total_paise": 11000,
                },
            ],
        },
    )
    assert draft_res.status_code == 201
    draft_id = draft_res.json()["id"]

    # Confirm the receipt
    conf_res = await client.post(
        f"/api/v1/purchases/{draft_id}/confirm",
        headers=hdr,
        json={},
    )
    assert conf_res.status_code == 200

    # Verify raw OCR evidence preserved, normalized quantities accurate, and standardized product
    # names created
    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        pis = (
            (await db.execute(select(PurchaseItem).where(PurchaseItem.household_id == hh_id)))
            .scalars()
            .all()
        )
        pi_by_raw = {p.raw_text: p for p in pis}
        assert pi_by_raw["Aavin Full Cream Milk 500 ml R$ 60.00"].normalized_qty == Decimal(
            "1000.000"
        )
        assert pi_by_raw["Aavin Full Cream Milk 500 ml R$ 60.00"].base_unit == "ml"
        assert pi_by_raw["Amul Gold Milk 1 L"].normalized_qty == Decimal("1000.000")
        assert pi_by_raw["8 Chana-Đal 1 KG 110: 110.00"].normalized_qty == Decimal("1000.000")
        assert pi_by_raw["mported Daily Apple (Sebu) R$ 110.00"].normalized_qty == Decimal("3.000")
        assert pi_by_raw["mported Daily Apple (Sebu) R$ 110.00"].base_unit == "pcs"

        # Confirm Milk mappings for both brands (without leaking brand into ingredient heading)
        milk_ing = (
            await db.execute(
                select(CanonicalIngredient).where(CanonicalIngredient.normalized_name == "milk")
            )
        ).scalar_one()
        aavin_cid = pi_by_raw["Aavin Full Cream Milk 500 ml R$ 60.00"].canonical_item_id
        amul_cid = pi_by_raw["Amul Gold Milk 1 L"].canonical_item_id
        apple_cid = pi_by_raw["mported Daily Apple (Sebu) R$ 110.00"].canonical_item_id
        apple_ci = await db.get(CanonicalItem, apple_cid)
        assert apple_ci is not None and apple_ci.name == "Imported Daily Apple (Sebu)"

        for cid in (aavin_cid, amul_cid):
            existing_map = (
                await db.execute(
                    select(IngredientMapping).where(
                        IngredientMapping.household_id == hh_id,
                        IngredientMapping.canonical_item_id == cid,
                    )
                )
            ).scalar_one_or_none()
            if existing_map is None:
                db.add(
                    IngredientMapping(
                        household_id=hh_id,
                        canonical_item_id=cid,
                        canonical_ingredient_id=milk_ing.id,
                        matching_confidence=Decimal("1.000"),
                        mapping_source="user_confirmed",
                        user_confirmed=True,
                    )
                )
            else:
                existing_map.canonical_ingredient_id = milk_ing.id
                existing_map.user_confirmed = True
        await db.commit()

    # Check ingredient summary API: Milk aggregates 1000 + 1000 = 2000 ml under canonical name
    # "Milk"
    ing_res = await client.get("/api/v1/inventory/ingredients/summary", headers=hdr)
    assert ing_res.status_code == 200
    ing_data = ing_res.json()
    milk_rows = [x for x in ing_data["ingredients"] if x["name"] == "Milk"]
    assert len(milk_rows) == 1
    assert Decimal(milk_rows[0]["total_remaining_qty"]) == Decimal("2000.000")
    assert milk_rows[0]["standard_unit"] == "ml"

    # Unmapped Apple stays in unmapped_products with 3 pcs and cleaned name, NOT in confirmed
    # ingredients
    unmapped_names = [u["name"] for u in ing_data["unmapped_products"]]
    assert "Imported Daily Apple (Sebu)" in unmapped_names
    assert all(x["name"] != "Imported Daily Apple (Sebu)" for x in ing_data["ingredients"])

    # Price history remains intact
    ph = await client.get(f"/api/v1/inventory/items/{aavin_cid}/price-history", headers=hdr)
    assert ph.status_code == 200 and len(ph.json()["entries"]) >= 1
    await engine.dispose()
