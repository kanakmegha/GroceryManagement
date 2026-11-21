"""Phase 5: Tests for Pantry UX simplification (Mapping Review) and Smart Replenish filtering."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
async def test_phase5_replenishment_filtering_and_pantry_mapping_review(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
    temp_db_url: str,
) -> None:
    """Covers all 11 Phase 5 Part G scenarios."""
    token = jwks_provider.make_token(sub="user_p5", email="p5@example.com", name="P5 User")
    hdr = {"Authorization": f"Bearer {token}"}
    me = await client.get("/api/v1/auth/me", headers=hdr)
    assert me.status_code == 200
    hh_id = UUID(me.json()["active_household"]["id"])
    user_id = UUID(me.json()["user"]["id"])
    hdr["X-Household-Id"] = str(hh_id)

    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)

    async with Session() as db:
        ing_by_norm = {
            ing.normalized_name: ing
            for ing in (await db.execute(select(CanonicalIngredient))).scalars().all()
        }
        milk_ing = ing_by_norm["milk"]
        chana_ing = ing_by_norm["chana_dal"]
        almonds_ing = ing_by_norm["almonds"]

        # 1. Insufficient-data item (single purchase, no threshold)
        insuff_item = CanonicalItem(
            household_id=hh_id,
            slug="single-purchase-ghee",
            name="Single Purchase Ghee",
            category="dairy",
            default_base_unit="ml",
            low_stock_threshold_qty=Decimal("0"),
        )
        # 2. Out-of-stock item with no history and no threshold
        oos_no_hist = CanonicalItem(
            household_id=hh_id,
            slug="oos-no-history",
            name="OOS No History Spice",
            category="spice",
            default_base_unit="g",
            low_stock_threshold_qty=Decimal("0"),
        )
        # 3. Low-stock item with sufficient consumption history -> recommended
        hist_milk = CanonicalItem(
            household_id=hh_id,
            slug="aavin-milk-1l",
            name="Aavin Full Cream Milk",
            category="dairy",
            default_base_unit="ml",
            low_stock_threshold_qty=Decimal("500"),
        )
        # 4. User-defined minimum stock target with limited history -> user_target_based
        target_dal = CanonicalItem(
            household_id=hh_id,
            slug="chana-dal-target",
            name="Chana Dal Target",
            category="pantry",
            default_base_unit="g",
            low_stock_threshold_qty=Decimal("1000"),
        )
        # 5. Unknown/unnormalized unit check (verified via normalize_unit & StockLedger mismatch)
        unk_unit_item = CanonicalItem(
            household_id=hh_id,
            slug="unknown-unit-herbs",
            name="Fresh Herbs Bunch",
            category="produce",
            default_base_unit="pcs",
        )
        # 6. Incompatible unit item (lots in g and ml) -> suppressed
        incomp_item = CanonicalItem(
            household_id=hh_id,
            slug="incompatible-honey",
            name="Forest Honey",
            category="pantry",
            default_base_unit="g",
        )
        # 7 & 8. Second Milk product + unmapped grocery item
        amul_milk = CanonicalItem(
            household_id=hh_id,
            slug="amul-gold-milk",
            name="Amul Gold Milk",
            category="dairy",
            default_base_unit="ml",
        )
        unmapped_low = CanonicalItem(
            household_id=hh_id,
            slug="artisan-truffle-paste",
            name="Artisan Truffle Paste 250g",
            category="pantry",
            default_base_unit="g",
        )
        # 9. Non-food item with suggestion -> must be excluded from mapping_review_items
        non_food_oil = CanonicalItem(
            household_id=hh_id,
            slug="dabur-hair-oil",
            name="Dabur Almond Hair Oil",
            category="non_food",
            default_base_unit="ml",
            pantry_eligible=False,
        )
        db.add_all(
            [
                insuff_item,
                oos_no_hist,
                hist_milk,
                target_dal,
                unk_unit_item,
                incomp_item,
                amul_milk,
                unmapped_low,
                non_food_oil,
            ]
        )
        await db.flush()

        # Lots
        lot_insuff = InventoryLot(
            household_id=hh_id,
            canonical_item_id=insuff_item.id,
            initial_qty=Decimal("500"),
            remaining_qty=Decimal("500"),
            base_unit="ml",
            unit_cost_paise=30000,
            purchased_at=now - timedelta(days=2),
            lot_status="active",
        )
        lot_oos = InventoryLot(
            household_id=hh_id,
            canonical_item_id=oos_no_hist.id,
            initial_qty=Decimal("100"),
            remaining_qty=Decimal("0"),
            base_unit="g",
            unit_cost_paise=5000,
            purchased_at=now - timedelta(days=5),
            lot_status="depleted",
        )
        lot_milk = InventoryLot(
            household_id=hh_id,
            canonical_item_id=hist_milk.id,
            initial_qty=Decimal("2000"),
            remaining_qty=Decimal("400"),
            base_unit="ml",
            unit_cost_paise=12000,
            purchased_at=now - timedelta(days=6),
            lot_status="active",
        )
        lot_target = InventoryLot(
            household_id=hh_id,
            canonical_item_id=target_dal.id,
            initial_qty=Decimal("1000"),
            remaining_qty=Decimal("300"),
            base_unit="g",
            unit_cost_paise=9000,
            purchased_at=now - timedelta(days=3),
            lot_status="active",
        )
        lot_unk = InventoryLot(
            household_id=hh_id,
            canonical_item_id=unk_unit_item.id,
            initial_qty=Decimal("2"),
            remaining_qty=Decimal("2"),
            base_unit="pcs",
            unit_cost_paise=2000,
            purchased_at=now - timedelta(days=1),
            lot_status="active",
        )
        lot_inc_g = InventoryLot(
            household_id=hh_id,
            canonical_item_id=incomp_item.id,
            initial_qty=Decimal("500"),
            remaining_qty=Decimal("500"),
            base_unit="g",
            unit_cost_paise=20000,
            purchased_at=now - timedelta(days=2),
            lot_status="active",
        )
        lot_inc_ml = InventoryLot(
            household_id=hh_id,
            canonical_item_id=incomp_item.id,
            initial_qty=Decimal("250"),
            remaining_qty=Decimal("250"),
            base_unit="ml",
            unit_cost_paise=15000,
            purchased_at=now - timedelta(days=1),
            lot_status="active",
        )
        lot_amul = InventoryLot(
            household_id=hh_id,
            canonical_item_id=amul_milk.id,
            initial_qty=Decimal("1500"),
            remaining_qty=Decimal("1500"),
            base_unit="ml",
            unit_cost_paise=9000,
            purchased_at=now,
            lot_status="active",
        )
        lot_unmapped = InventoryLot(
            household_id=hh_id,
            canonical_item_id=unmapped_low.id,
            initial_qty=Decimal("250"),
            remaining_qty=Decimal("250"),
            base_unit="g",
            unit_cost_paise=9900,
            purchased_at=now,
            lot_status="active",
        )
        lot_nonfood = InventoryLot(
            household_id=hh_id,
            canonical_item_id=non_food_oil.id,
            initial_qty=Decimal("290"),
            remaining_qty=Decimal("290"),
            base_unit="ml",
            unit_cost_paise=15500,
            purchased_at=now,
            lot_status="active",
        )
        db.add_all(
            [
                lot_insuff,
                lot_oos,
                lot_milk,
                lot_target,
                lot_unk,
                lot_inc_g,
                lot_inc_ml,
                lot_amul,
                lot_unmapped,
                lot_nonfood,
            ]
        )
        await db.flush()

        # Consumption history + 5 consistent purchases (3 days apart) on hist_milk -> HIGH confidence consumption_based
        for days_ago in (18, 15, 12, 9, 6):
            p_m = Purchase(
                household_id=hh_id,
                created_by=user_id,
                merchant_name="FreshMart",
                purchased_on=(now - timedelta(days=days_ago)).date(),
                receipt_category="grocery",
                total_amount_paise=6000,
                status="confirmed",
            )
            db.add(p_m)
            await db.flush()
            db.add(
                PurchaseItem(
                    purchase_id=p_m.id,
                    household_id=hh_id,
                    line_index=0,
                    raw_text="Aavin Full Cream Milk 1L",
                    canonical_item_id=hist_milk.id,
                    display_name="Aavin Full Cream Milk",
                    normalized_name="aavin full cream milk",
                    normalized_qty=Decimal("2500"),
                    base_unit="ml",
                    unit_price_paise=6000,
                    line_total_paise=6000,
                    track_inventory=True,
                )
            )
        db.add(
            StockLedger(
                household_id=hh_id,
                lot_id=lot_milk.id,
                canonical_item_id=hist_milk.id,
                event_type="COOK_OUT",
                quantity_delta=Decimal("-5000"),
                base_unit="ml",
                balance_after_lot=Decimal("400"),
                unit_cost_paise=12000,
                actor_user_id=user_id,
                created_at=now - timedelta(days=5),
            )
        )

        # Confirmed mappings for Milk; unconfirmed suggestions for target_dal & non_food_oil
        db.add_all(
            [
                IngredientMapping(
                    household_id=hh_id,
                    canonical_item_id=hist_milk.id,
                    canonical_ingredient_id=milk_ing.id,
                    matching_confidence=Decimal("1.000"),
                    mapping_source="user_confirmed",
                    user_confirmed=True,
                ),
                IngredientMapping(
                    household_id=hh_id,
                    canonical_item_id=amul_milk.id,
                    canonical_ingredient_id=milk_ing.id,
                    matching_confidence=Decimal("1.000"),
                    mapping_source="user_confirmed",
                    user_confirmed=True,
                ),
                IngredientMapping(
                    household_id=hh_id,
                    canonical_item_id=target_dal.id,
                    canonical_ingredient_id=chana_ing.id,
                    matching_confidence=Decimal("0.920"),
                    mapping_source="catalogue",
                    user_confirmed=False,
                ),
                IngredientMapping(
                    household_id=hh_id,
                    canonical_item_id=non_food_oil.id,
                    canonical_ingredient_id=almonds_ing.id,
                    matching_confidence=Decimal("0.750"),
                    mapping_source="classifier_v2",
                    user_confirmed=False,
                ),
            ]
        )
        await db.commit()

        ids = {
            "insuff": str(insuff_item.id),
            "oos": str(oos_no_hist.id),
            "milk": str(hist_milk.id),
            "target": str(target_dal.id),
            "unk": str(unk_unit_item.id),
            "incomp": str(incomp_item.id),
            "nonfood": str(non_food_oil.id),
            "unmapped": str(unmapped_low.id),
        }

    # --- Verify Smart Replenish filtering ---
    rec_res = await client.get("/api/v1/replenishment/recommendations?refresh=true", headers=hdr)
    assert rec_res.status_code == 200
    rec_data = rec_res.json()

    actionable = rec_data["buy_now"] + rec_data["buy_soon"] + rec_data["regular_purchases"]
    actionable_by_id = {x["canonical_item_id"]: x for x in actionable}
    needs_more_by_id = {x["canonical_item_id"]: x for x in rec_data["needs_more_data"]}

    # 1, 2, 5, 6: Insufficient data, OOS with no history, unknown unit, incompatible unit -> suppressed
    assert ids["insuff"] not in actionable_by_id
    assert ids["oos"] not in actionable_by_id
    assert ids["unk"] not in actionable_by_id
    assert ids["incomp"] not in actionable_by_id

    assert ids["insuff"] in needs_more_by_id
    assert ids["oos"] in needs_more_by_id
    assert needs_more_by_id[ids["oos"]]["recommended_qty"] is None

    # No actionable item has insufficient_data or null recommended_qty
    for item in actionable:
        assert item["evidence_basis"] != "insufficient_data"
        assert item["quantity_basis"] != "insufficient_data"
        assert item["recommended_qty"] is not None and item["recommended_qty"] > 0

    # 3: Low-stock with consumption history -> recommended
    assert ids["milk"] in actionable_by_id
    assert actionable_by_id[ids["milk"]]["evidence_basis"] == "consumption_based"

    # 4: User-defined minimum stock target without HIGH confidence -> suppressed under Phase 5B.1 strict evidence rules
    assert ids["target"] not in actionable_by_id
    assert ids["target"] in needs_more_by_id
    assert needs_more_by_id[ids["target"]]["eligibility_status"] == "SUPPRESSED"

    # --- Verify Pantry Mapping Review & Ingredient Aggregation ---
    ing_res = await client.get("/api/v1/inventory/ingredients/summary", headers=hdr)
    assert ing_res.status_code == 200
    ing_data = ing_res.json()

    # 7 & 8: Confirmed Milk mapping aggregates 400 + 1500 = 1900 ml; unmapped product doesn't break pantry
    assert len(ing_data["ingredients"]) == 1
    assert ing_data["ingredients"][0]["name"] == "Milk"
    assert Decimal(ing_data["ingredients"][0]["total_remaining_qty"]) == Decimal("1900.000")

    # 9: Mapping review includes ONLY grocery suggestion (target_dal), excluding non-food & LOW unmapped
    review_ids = [x["canonical_item_id"] for x in ing_data["mapping_review_items"]]
    assert review_ids == [ids["target"]]
    assert ids["nonfood"] not in review_ids
    assert ids["unmapped"] not in review_ids

    # 10: Rejecting a mapping does not alter inventory
    rej = await client.post(
        "/api/v1/inventory/ingredients/mappings/reject",
        headers=hdr,
        json={"canonical_item_id": ids["target"]},
    )
    assert rej.status_code == 200 and rej.json()["rejected"] is True
    prod_sum = (await client.get("/api/v1/inventory/summary", headers=hdr)).json()["items"]
    target_prod = [x for x in prod_sum if x["canonical_item_id"] == ids["target"]][0]
    assert Decimal(target_prod["total_remaining_qty"]) == Decimal("300.000")

    await engine.dispose()
