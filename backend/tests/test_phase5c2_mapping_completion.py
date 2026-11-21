from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.modules.catalog.ingredient_mapping_service import (
    confirm_ingredient_mapping,
    resolve_or_suggest_mapping,
)
from app.modules.inventory.models import CanonicalIngredient, CanonicalItem, IngredientMapping, InventoryLot
from tests.conftest import EphemeralJWKSProvider


@pytest.mark.asyncio
async def test_phase5c2_mapping_suggestions_confirmation_and_aggregation(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
    temp_db_url: str,
) -> None:
    """Covers Phase 5C.2 mapping suggestions, non-overwrite, non-food exclusion, and multi-brand aggregation."""
    token = jwks_provider.make_token(sub="user_5c2", email="5c2@example.com", name="5C2 User")
    hdr = {"Authorization": f"Bearer {token}"}
    me = await client.get("/api/v1/auth/me", headers=hdr)
    assert me.status_code == 200
    hh_id = UUID(me.json()["active_household"]["id"])
    hdr["X-Household-Id"] = str(hh_id)

    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        ing_by_norm = {
            i.normalized_name: i
            for i in (await db.execute(select(CanonicalIngredient))).scalars().all()
        }

        apple_item = CanonicalItem(
            household_id=hh_id, slug="imp-apple", name="Imported Daily Apple (Sebu)",
            category="produce", default_base_unit="pcs",
        )
        orid_item = CanonicalItem(
            household_id=hh_id, slug="udhaiyam-orid", name="Udhaiyam Dhall - Orid",
            category="pantry", default_base_unit="g",
        )
        urad_dhuli = CanonicalItem(
            household_id=hh_id, slug="urad-dhuli", name="Urad Dhuli",
            category="pantry", default_base_unit="g",
        )
        snack_item = CanonicalItem(
            household_id=hh_id, slug="prabhat-katori", name="Prabhat Namkeen Masala Soya Katori",
            category="snacks", default_base_unit="pcs",
        )
        nonfood_item = CanonicalItem(
            household_id=hh_id, slug="soap-bar", name="Dishwash Soap Bar",
            category="non_food", default_base_unit="pcs", pantry_eligible=False,
        )
        confirmed_toor = CanonicalItem(
            household_id=hh_id, slug="arhar-dal", name="Arhar Dal",
            category="pantry", default_base_unit="g",
        )
        db.add_all([apple_item, orid_item, urad_dhuli, snack_item, nonfood_item, confirmed_toor])
        await db.flush()

        db.add_all([
            InventoryLot(
                household_id=hh_id, canonical_item_id=apple_item.id,
                initial_qty=Decimal("3"), remaining_qty=Decimal("3"), base_unit="pcs",
                unit_cost_paise=3000, lot_status="active",
            ),
            InventoryLot(
                household_id=hh_id, canonical_item_id=orid_item.id,
                initial_qty=Decimal("2000"), remaining_qty=Decimal("2000"), base_unit="g",
                unit_cost_paise=20000, lot_status="active",
            ),
            InventoryLot(
                household_id=hh_id, canonical_item_id=urad_dhuli.id,
                initial_qty=Decimal("1000"), remaining_qty=Decimal("1000"), base_unit="g",
                unit_cost_paise=12000, lot_status="active",
            ),
            InventoryLot(
                household_id=hh_id, canonical_item_id=confirmed_toor.id,
                initial_qty=Decimal("500"), remaining_qty=Decimal("500"), base_unit="g",
                unit_cost_paise=8000, lot_status="active",
            ),
        ])
        db.add(
            IngredientMapping(
                household_id=hh_id,
                canonical_item_id=confirmed_toor.id,
                canonical_ingredient_id=ing_by_norm["toor_dal"].id,
                matching_confidence=Decimal("1.000"),
                mapping_source="user_confirmed",
                user_confirmed=True,
            )
        )
        await db.flush()

        # Suggestions only (no auto-confirmation)
        r_apple = await resolve_or_suggest_mapping(db, household_id=hh_id, canonical_item=apple_item, auto_map_high_confidence=False)
        r_orid = await resolve_or_suggest_mapping(db, household_id=hh_id, canonical_item=orid_item, auto_map_high_confidence=False)
        r_urad = await resolve_or_suggest_mapping(db, household_id=hh_id, canonical_item=urad_dhuli, auto_map_high_confidence=False)
        r_snack = await resolve_or_suggest_mapping(db, household_id=hh_id, canonical_item=snack_item, auto_map_high_confidence=False)
        r_nonfood = await resolve_or_suggest_mapping(db, household_id=hh_id, canonical_item=nonfood_item, auto_map_high_confidence=False)
        r_toor = await resolve_or_suggest_mapping(db, household_id=hh_id, canonical_item=confirmed_toor, auto_map_high_confidence=False)

        assert r_apple.status == "suggested" and r_apple.suggested_ingredient and r_apple.suggested_ingredient.name == "Apple"
        assert r_apple.user_confirmed is False
        assert r_orid.status == "suggested" and r_orid.suggested_ingredient and r_orid.suggested_ingredient.name == "Urad Dal"
        assert r_urad.status == "suggested" and r_urad.suggested_ingredient and r_urad.suggested_ingredient.name == "White Urad Dal"
        assert r_snack.status == "unmapped"
        assert r_nonfood.status == "unmapped"
        assert r_toor.status == "reused_confirmed" and r_toor.user_confirmed is True
        await db.commit()

    # Before confirmation: Apple, Udhaiyam Dhall - Orid, and Urad Dhuli stay in unmapped_products
    ing_before = (await client.get("/api/v1/inventory/ingredients/summary", headers=hdr)).json()
    assert [x["name"] for x in ing_before["ingredients"]] == ["Toor Dal"]
    unmapped_names_before = {x["name"] for x in ing_before["unmapped_products"]}
    assert {"Imported Daily Apple (Sebu)", "Udhaiyam Dhall - Orid", "Urad Dhuli"}.issubset(unmapped_names_before)

    # Confirm Udhaiyam Dhall - Orid and Urad Dhuli -> Urad Dal via review workflow
    async with Session() as db:
        urad_ing_id = (
            await db.execute(select(CanonicalIngredient.id).where(CanonicalIngredient.normalized_name == "urad_dal"))
        ).scalar_one()
        await confirm_ingredient_mapping(
            db, household_id=hh_id, canonical_item_id=orid_item.id, canonical_ingredient_id=urad_ing_id,
        )
        await confirm_ingredient_mapping(
            db, household_id=hh_id, canonical_item_id=urad_dhuli.id, canonical_ingredient_id=urad_ing_id,
        )
        await db.commit()

    ing_after = (await client.get("/api/v1/inventory/ingredients/summary", headers=hdr)).json()
    by_name = {x["name"]: x for x in ing_after["ingredients"]}
    assert "Urad Dal" in by_name
    assert Decimal(by_name["Urad Dal"]["total_remaining_qty"]) == Decimal("3000.000")
    assert by_name["Urad Dal"]["contributing_product_count"] == 2
    # Apple still unconfirmed -> stays in unmapped_products
    assert "Apple" not in by_name
    assert "Imported Daily Apple (Sebu)" in {x["name"] for x in ing_after["unmapped_products"]}
    await engine.dispose()
