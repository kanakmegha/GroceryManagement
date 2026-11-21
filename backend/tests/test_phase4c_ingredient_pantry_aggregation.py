"""Phase 4C: Comprehensive tests for read-only ingredient-level pantry aggregation."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.modules.catalog.ingredient_mapping_service import confirm_ingredient_mapping
from app.modules.inventory.ingredient_aggregation_service import (
    convert_to_target_unit,
    normalize_pantry_unit,
)
from app.modules.inventory.models import (
    CanonicalIngredient,
    CanonicalItem,
    IngredientMapping,
    InventoryLot,
    StockLedger,
)
from app.modules.inventory.schemas import AdjustLotRequest
from app.modules.inventory.service import adjust_lot, allocate_fefo
from tests.conftest import EphemeralJWKSProvider


async def _snapshot_inventory(db) -> tuple[list[tuple], list[tuple]]:
    lots = (
        await db.execute(select(InventoryLot).order_by(InventoryLot.id.asc()))
    ).scalars().all()
    ledger = (
        await db.execute(select(StockLedger).order_by(StockLedger.id.asc()))
    ).scalars().all()
    lot_snap = [
        (l.id, l.canonical_item_id, l.initial_qty, l.remaining_qty, l.base_unit, l.lot_status)
        for l in lots
    ]
    ledger_snap = [
        (e.id, e.lot_id, e.canonical_item_id, e.event_type, e.quantity_delta, e.balance_after_lot)
        for e in ledger
    ]
    return lot_snap, ledger_snap


@pytest.mark.asyncio
async def test_phase4c_ingredient_pantry_aggregation_all_scenarios(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
    temp_db_url: str,
) -> None:
    """Covers all 18 Phase 4C scenarios and asserts read-only invariance of lots and stock ledger."""
    token_a = jwks_provider.make_token(sub="user_4c_a", email="4ca@example.com", name="4C User A")
    token_b = jwks_provider.make_token(sub="user_4c_b", email="4cb@example.com", name="4C User B")
    hdr_a = {"Authorization": f"Bearer {token_a}"}
    hdr_b = {"Authorization": f"Bearer {token_b}"}

    me_a = await client.get("/api/v1/auth/me", headers=hdr_a)
    me_b = await client.get("/api/v1/auth/me", headers=hdr_b)
    assert me_a.status_code == 200 and me_b.status_code == 200
    hh_a = UUID(me_a.json()["active_household"]["id"])
    user_a = UUID(me_a.json()["user"]["id"])
    hdr_a["X-Household-Id"] = str(hh_a)
    hdr_b["X-Household-Id"] = me_b.json()["active_household"]["id"]

    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        ing_by_norm = {
            ing.normalized_name: ing
            for ing in (await db.execute(select(CanonicalIngredient))).scalars().all()
        }
        milk_ing = ing_by_norm["milk"]
        rice_ing = ing_by_norm["basmati_rice"]
        tomato_ing = ing_by_norm["tomato"]
        onion_ing = ing_by_norm["onion"]
        spinach_ing = ing_by_norm["spinach"]
        garlic_ing = ing_by_norm["garlic"]

        # 1 & 8: Two milk products in ml and L -> 1000 ml + 1500 ml (after partial depletion) = 2500 ml
        milk_p1 = CanonicalItem(
            household_id=hh_a,
            slug="amul-gold-1l",
            name="Amul Gold Milk 1L",
            category="dairy",
            default_base_unit="ml",
        )
        milk_p2 = CanonicalItem(
            household_id=hh_a,
            slug="aavin-toned-500ml",
            name="Aavin Toned Milk 500ml",
            category="dairy",
            default_base_unit="ml",
        )
        # 2 & 7: Basmati Rice with 2 lots (1 kg + 500 g) -> 1500 g
        rice_p = CanonicalItem(
            household_id=hh_a,
            slug="india-gate-basmati",
            name="India Gate Basmati Rice",
            category="grain",
            default_base_unit="g",
        )
        # 9: Tomato with incompatible units (500 g + 2 pcs)
        tom_p1 = CanonicalItem(
            household_id=hh_a,
            slug="fresh-tomatoes-g",
            name="Fresh Tomatoes Weight",
            category="vegetable",
            default_base_unit="g",
        )
        tom_p2 = CanonicalItem(
            household_id=hh_a,
            slug="fresh-tomatoes-pcs",
            name="Fresh Tomatoes Pack",
            category="vegetable",
            default_base_unit="pcs",
        )
        # 10: Spinach with unknown unit ("bunch")
        spinach_p = CanonicalItem(
            household_id=hh_a,
            slug="palak-bunch",
            name="Fresh Palak Bunch",
            category="vegetable",
            default_base_unit="pcs",
        )
        # 11: Unmapped custom product
        unmapped_p = CanonicalItem(
            household_id=hh_a,
            slug="artisan-spelt-flour",
            name="Artisan Spelt Flour 250g",
            category="grain",
            default_base_unit="g",
        )
        # 12: Unconfirmed suggestion product (mapped with user_confirmed=False)
        unconf_p = CanonicalItem(
            household_id=hh_a,
            slug="garlic-pods-200g",
            name="Garlic Pods 200g",
            category="vegetable",
            default_base_unit="g",
        )
        # 15: Soft-merged product pointing to rice_p
        merged_rice_p = CanonicalItem(
            household_id=hh_a,
            slug="india-gate-basmati-old",
            name="India Gate Basmati Old",
            category="grain",
            default_base_unit="g",
            is_active=False,
        )
        # 16: Zero-stock mapped ingredient (Onion)
        onion_p = CanonicalItem(
            household_id=hh_a,
            slug="red-onion-1kg",
            name="Red Onion 1kg",
            category="vegetable",
            default_base_unit="g",
        )
        db.add_all(
            [
                milk_p1,
                milk_p2,
                rice_p,
                tom_p1,
                tom_p2,
                spinach_p,
                unmapped_p,
                unconf_p,
                merged_rice_p,
                onion_p,
            ]
        )
        await db.flush()
        merged_rice_p.merged_into_id = rice_p.id

        # Confirmed mappings
        await confirm_ingredient_mapping(
            db, household_id=hh_a, canonical_item_id=milk_p1.id, canonical_ingredient_id=milk_ing.id
        )
        await confirm_ingredient_mapping(
            db, household_id=hh_a, canonical_item_id=milk_p2.id, canonical_ingredient_id=milk_ing.id
        )
        await confirm_ingredient_mapping(
            db, household_id=hh_a, canonical_item_id=rice_p.id, canonical_ingredient_id=rice_ing.id
        )
        # 14: Add a global mapping for rice_p as well -> must not double-count
        db.add(
            IngredientMapping(
                household_id=None,
                canonical_item_id=rice_p.id,
                canonical_ingredient_id=rice_ing.id,
                matching_confidence=Decimal("0.950"),
                mapping_source="catalogue",
                user_confirmed=True,
            )
        )
        await confirm_ingredient_mapping(
            db, household_id=hh_a, canonical_item_id=tom_p1.id, canonical_ingredient_id=tomato_ing.id
        )
        await confirm_ingredient_mapping(
            db, household_id=hh_a, canonical_item_id=tom_p2.id, canonical_ingredient_id=tomato_ing.id
        )
        await confirm_ingredient_mapping(
            db, household_id=hh_a, canonical_item_id=spinach_p.id, canonical_ingredient_id=spinach_ing.id
        )
        await confirm_ingredient_mapping(
            db, household_id=hh_a, canonical_item_id=onion_p.id, canonical_ingredient_id=onion_ing.id
        )
        # 12: Unconfirmed suggestion (user_confirmed=False)
        db.add(
            IngredientMapping(
                household_id=hh_a,
                canonical_item_id=unconf_p.id,
                canonical_ingredient_id=garlic_ing.id,
                matching_confidence=Decimal("0.920"),
                mapping_source="catalogue",
                user_confirmed=False,
            )
        )

        now = datetime.now(UTC)
        # Lots
        lot_m1 = InventoryLot(
            household_id=hh_a,
            canonical_item_id=milk_p1.id,
            initial_qty=Decimal("2000.000"),
            remaining_qty=Decimal("2000.000"),
            base_unit="ml",
            unit_cost_paise=12000,
            purchased_at=now,
            lot_status="active",
        )
        lot_m2 = InventoryLot(
            household_id=hh_a,
            canonical_item_id=milk_p2.id,
            initial_qty=Decimal("500.000"),
            remaining_qty=Decimal("500.000"),
            base_unit="ml",
            unit_cost_paise=3000,
            purchased_at=now,
            lot_status="active",
        )
        # 4: Fully depleted milk lot (must be excluded)
        lot_m_dep = InventoryLot(
            household_id=hh_a,
            canonical_item_id=milk_p2.id,
            initial_qty=Decimal("500.000"),
            remaining_qty=Decimal("0.000"),
            base_unit="ml",
            unit_cost_paise=3000,
            purchased_at=now,
            lot_status="depleted",
        )
        # 2 & 7: Multiple lots for rice_p (1000 g + 500 g)
        lot_r1 = InventoryLot(
            household_id=hh_a,
            canonical_item_id=rice_p.id,
            initial_qty=Decimal("1000.000"),
            remaining_qty=Decimal("1000.000"),
            base_unit="g",
            unit_cost_paise=15000,
            purchased_at=now,
            lot_status="active",
        )
        lot_r2 = InventoryLot(
            household_id=hh_a,
            canonical_item_id=rice_p.id,
            initial_qty=Decimal("500.000"),
            remaining_qty=Decimal("500.000"),
            base_unit="g",
            unit_cost_paise=8000,
            purchased_at=now,
            lot_status="active",
        )
        # 9: Incompatible tomato lots
        lot_t1 = InventoryLot(
            household_id=hh_a,
            canonical_item_id=tom_p1.id,
            initial_qty=Decimal("500.000"),
            remaining_qty=Decimal("500.000"),
            base_unit="g",
            unit_cost_paise=2500,
            purchased_at=now,
            lot_status="active",
        )
        lot_t2 = InventoryLot(
            household_id=hh_a,
            canonical_item_id=tom_p2.id,
            initial_qty=Decimal("2.000"),
            remaining_qty=Decimal("2.000"),
            base_unit="pcs",
            unit_cost_paise=2000,
            purchased_at=now,
            lot_status="active",
        )
        # 10: Incompatible unit on Spinach (2 pcs vs standard_unit g)
        lot_sp = InventoryLot(
            household_id=hh_a,
            canonical_item_id=spinach_p.id,
            initial_qty=Decimal("2.000"),
            remaining_qty=Decimal("2.000"),
            base_unit="pcs",
            unit_cost_paise=1500,
            purchased_at=now,
            lot_status="active",
        )
        # 11 & 12: Unmapped & unconfirmed lots
        lot_unmapped = InventoryLot(
            household_id=hh_a,
            canonical_item_id=unmapped_p.id,
            initial_qty=Decimal("250.000"),
            remaining_qty=Decimal("250.000"),
            base_unit="g",
            unit_cost_paise=9900,
            purchased_at=now,
            lot_status="active",
        )
        lot_unconf = InventoryLot(
            household_id=hh_a,
            canonical_item_id=unconf_p.id,
            initial_qty=Decimal("200.000"),
            remaining_qty=Decimal("200.000"),
            base_unit="g",
            unit_cost_paise=4000,
            purchased_at=now,
            lot_status="active",
        )
        db.add_all(
            [
                lot_m1,
                lot_m2,
                lot_m_dep,
                lot_r1,
                lot_r2,
                lot_t1,
                lot_t2,
                lot_sp,
                lot_unmapped,
                lot_unconf,
            ]
        )
        await db.flush()

        # 3 & 5: Stock adjustment on lot_m1 (-500 ml -> 1500 ml remaining)
        await adjust_lot(
            db,
            household_id=hh_a,
            actor_user_id=user_a,
            lot_id=lot_m1.id,
            payload=AdjustLotRequest(
                event_type="ADJUSTMENT_OUT",
                quantity_delta=Decimal("-500.000"),
                notes="Spilled 500ml",
            ),
        )
        # 6: Consumption (COOK_OUT) on lot_r2 (-200 g -> 300 g remaining, total rice = 1000g + 300g = 1300g)
        await allocate_fefo(
            db,
            household_id=hh_a,
            canonical_item_id=rice_p.id,
            required_qty=Decimal("200.000"),
            actor_user_id=user_a,
            reference_type="cooking_plan",
            reference_id=None,
            notes="Cooked biryani",
        )
        await db.commit()

        before_lots, before_ledger = await _snapshot_inventory(db)

    # Call GET /api/v1/inventory/ingredients/summary
    res = await client.get("/api/v1/inventory/ingredients/summary", headers=hdr_a)
    assert res.status_code == 200
    body = res.json()

    by_ing = {x["normalized_name"]: x for x in body["ingredients"]}

    # Milk: 1.5 L (1500 ml) + 500 ml = 2000.000 ml across 2 products, 2 active lots (depleted excluded)
    milk_out = by_ing["milk"]
    assert Decimal(milk_out["total_remaining_qty"]) == Decimal("2000.000")
    assert milk_out["standard_unit"] == "ml"
    assert milk_out["unit_completeness_status"] == "complete"
    assert milk_out["contributing_product_count"] == 2
    assert milk_out["lot_count"] == 2

    # Basmati Rice: 1 kg (1000 g) + 300 g = 1300.000 g, 1 surviving product (no double count from global mapping/merged item)
    rice_out = by_ing["basmati_rice"]
    assert Decimal(rice_out["total_remaining_qty"]) == Decimal("1300.000")
    assert rice_out["standard_unit"] == "g"
    assert rice_out["unit_completeness_status"] == "complete"
    assert rice_out["contributing_product_count"] == 1
    assert rice_out["lot_count"] == 2

    # Tomato: incompatible units (500 g + 2 pcs) -> incomplete, total_remaining_qty is None
    tom_out = by_ing["tomato"]
    assert tom_out["unit_completeness_status"] == "incomplete"
    assert tom_out["total_remaining_qty"] is None
    groups_t = {g["unit"]: Decimal(g["total_qty"]) for g in tom_out["unit_groups"]}
    assert groups_t == {"g": Decimal("500.000"), "pcs": Decimal("2.000")}

    # Spinach: incompatible unit ("pcs") vs standard_unit ("g") -> incomplete, total_remaining_qty is None
    sp_out = by_ing["spinach"]
    assert sp_out["unit_completeness_status"] == "incomplete"
    assert sp_out["total_remaining_qty"] is None
    assert sp_out["unit_groups"] == [{"unit": "pcs", "total_qty": "2.000", "lot_count": 1}]

    # Onion: mapped product with zero stock -> empty, 0.000
    onion_out = by_ing["onion"]
    assert onion_out["unit_completeness_status"] == "empty"
    assert Decimal(onion_out["total_remaining_qty"]) == Decimal("0.000")
    assert onion_out["lot_count"] == 0

    # Garlic: unconfirmed suggestion must NOT appear in confirmed ingredients
    assert "garlic" not in by_ing

    # Unmapped & unconfirmed transparency
    assert body["unmapped_products_count"] == 2
    assert body["unmapped_lots_count"] == 2
    assert body["unconfirmed_suggestions_count"] == 1
    unmapped_by_name = {u["name"]: u for u in body["unmapped_products"]}
    assert unmapped_by_name["Artisan Spelt Flour 250g"]["suggestion_status"] == "unmapped"
    assert unmapped_by_name["Garlic Pods 200g"]["suggestion_status"] == "unconfirmed_suggestion"
    assert unmapped_by_name["Garlic Pods 200g"]["suggested_ingredient_name"] == "Garlic"

    # 13: Household B isolation
    res_b = await client.get("/api/v1/inventory/ingredients/summary", headers=hdr_b)
    assert res_b.status_code == 200
    body_b = res_b.json()
    assert body_b["ingredients"] == []
    assert body_b["unmapped_products_count"] == 0

    # 17: Verify read-only behaviour (InventoryLot and StockLedger unchanged)
    async with Session() as db:
        after_lots, after_ledger = await _snapshot_inventory(db)
        assert after_lots == before_lots
        assert after_ledger == before_ledger

    await engine.dispose()


def test_phase4c_unit_conversion_policy() -> None:
    """Explicit unit conversions: kg<->g, l<->ml, pcs!=g, g!=ml, unknown units stay separate."""
    assert normalize_pantry_unit("GMS") == "g"
    assert normalize_pantry_unit("litres") == "l"

    assert convert_to_target_unit(Decimal("1.5"), "kg", "g") == (Decimal("1500.000"), "g")
    assert convert_to_target_unit(Decimal("500"), "g", "kg") == (Decimal("0.500"), "kg")
    assert convert_to_target_unit(Decimal("2"), "l", "ml") == (Decimal("2000.000"), "ml")
    assert convert_to_target_unit(Decimal("250"), "ml", "l") == (Decimal("0.250"), "l")

    # Never mix weight, volume, pcs, or unknown units
    assert convert_to_target_unit(Decimal("100"), "g", "ml") == (None, "g")
    assert convert_to_target_unit(Decimal("100"), "ml", "g") == (None, "ml")
    assert convert_to_target_unit(Decimal("2"), "pcs", "g") == (None, "pcs")
    assert convert_to_target_unit(Decimal("2"), "bunch", "g") == (None, "bunch")
    assert convert_to_target_unit(Decimal("3"), "jar", "ml") == (None, "jar")
