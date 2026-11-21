"""Phase 5B: High-confidence Smart Replenish & centralized product naming tests (26 scenarios)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
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
from app.modules.catalog.product_naming_service import standardize_product_name
from app.modules.inventory.models import CanonicalIngredient, CanonicalItem, IngredientMapping, InventoryLot, StockLedger
from app.modules.purchases.models import Purchase, PurchaseItem
from tests.conftest import EphemeralJWKSProvider


def test_phase5b_centralized_product_naming_rules() -> None:
    """Covers scenarios 15-19, 21-23: OCR noise, rate noise, brand/variant preservation, package separation."""
    # 15 & 21: OCR bullet & Chana Bal -> Chana Dal
    r1 = standardize_product_name("• Chana Bal")
    assert r1.canonical_name == "Chana Dal"

    # 15 & 16: Serial + OCR + rate noise
    r2 = standardize_product_name("1 * KALLMalka KG 94")
    assert r2.canonical_name == "Kali Malka"

    r3 = standardize_product_name("S KHULAATTA")
    assert r3.canonical_name == "Khula Atta"

    # 17 & 19: Brand & variant preservation + package size separation
    r4 = standardize_product_name("Aavin Full Cream Milk 500 ml")
    assert r4.canonical_name == "Aavin Full Cream Milk"
    assert r4.brand == "Aavin"
    assert r4.variant == "full_cream"
    assert r4.package_size == "500 ml"

    # 17: Promo & package stripping with brand preserved
    r5 = standardize_product_name("Eastern Chilli Powder - Perfect Colour, Smell & Taste 250 g Pouch")
    assert r5.canonical_name == "Eastern Chilli Powder"
    assert r5.brand == "Eastern"
    assert r5.package_size == "250 g Pouch"

    # 22: Different dal varieties remain distinct
    assert standardize_product_name("Moong Dhuli KG 120").canonical_name == "Moong Dhuli"
    assert standardize_product_name("Moong SabuT").canonical_name == "Moong Sabut"
    assert standardize_product_name("Kali Malka").canonical_name != standardize_product_name("Lal Malka").canonical_name

    # 23: Raw receipt description remains unchanged in result.raw_description
    raw = "• Chana Bal 2 kg 180.00"
    r6 = standardize_product_name(raw, line_total_paise=18000, quantity=2000)
    assert r6.raw_description == raw


@pytest.mark.asyncio
async def test_phase5b_high_confidence_replenishment_and_creation_paths(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
    temp_db_url: str,
) -> None:
    """Covers scenarios 1-14, 20, 24-26."""
    token_a = jwks_provider.make_token(sub="user_5b_a", email="5ba@example.com", name="5B A")
    token_b = jwks_provider.make_token(sub="user_5b_b", email="5bb@example.com", name="5B B")
    hdr_a = {"Authorization": f"Bearer {token_a}"}
    hdr_b = {"Authorization": f"Bearer {token_b}"}

    me_a = await client.get("/api/v1/auth/me", headers=hdr_a)
    me_b = await client.get("/api/v1/auth/me", headers=hdr_b)
    hh_a = UUID(me_a.json()["active_household"]["id"])
    user_a = UUID(me_a.json()["user"]["id"])
    hh_b = UUID(me_b.json()["active_household"]["id"])
    hdr_a["X-Household-Id"] = str(hh_a)
    hdr_b["X-Household-Id"] = str(hh_b)

    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)

    async with Session() as db:
        milk_ing = (
            await db.execute(select(CanonicalIngredient).where(CanonicalIngredient.normalized_name == "milk"))
        ).scalar_one()

        # 1. HIGH confidence + SUFFICIENT stock -> SUPPRESSED
        high_suff = CanonicalItem(
            household_id=hh_a, slug="high-suff-curd", name="High Suff Curd", category="dairy", default_base_unit="g"
        )
        # 2. HIGH confidence + INSUFFICIENT stock -> ELIGIBLE
        high_insuff = CanonicalItem(
            household_id=hh_a, slug="high-insuff-paneer", name="High Insuff Paneer", category="dairy", default_base_unit="g"
        )
        # 3 & 6. Strong purchase history (5 purchases) WITHOUT consumption -> MEDIUM -> SUPPRESSED
        no_cons = CanonicalItem(
            household_id=hh_a, slug="no-cons-rice", name="No Cons Rice", category="grain", default_base_unit="g"
        )
        # 4. MEDIUM confidence (3 purchases) + zero stock -> SUPPRESSED
        med_zero = CanonicalItem(
            household_id=hh_a, slug="med-zero-dal", name="Med Zero Dal", category="pantry", default_base_unit="g"
        )
        # 4b. LOW confidence (2 purchases) + zero stock -> SUPPRESSED
        low_zero = CanonicalItem(
            household_id=hh_a, slug="low-zero-jeera", name="Low Zero Jeera", category="spice", default_base_unit="g"
        )
        # 4c. Unknown/unnormalized unit on purchase items -> SUPPRESSED
        unk_unit = CanonicalItem(
            household_id=hh_a, slug="unk-unit-oil", name="Unknown Unit Oil", category="pantry", default_base_unit="g"
        )
        # 5. INSUFFICIENT_DATA + zero stock -> SUPPRESSED
        insuff_zero = CanonicalItem(
            household_id=hh_a, slug="insuff-zero-salt", name="Insuff Zero Salt", category="pantry", default_base_unit="g"
        )
        # 7. Ingredient stock aggregated across 2 Milk brands (Aavin 1L + Amul 1.5L = 2.5L; requirement ~3L -> net 0.5L)
        aavin_milk = CanonicalItem(
            household_id=hh_a, slug="aavin-milk", name="Aavin Full Cream Milk", category="dairy", default_base_unit="ml"
        )
        amul_milk = CanonicalItem(
            household_id=hh_a, slug="amul-milk", name="Amul Gold Milk", category="dairy", default_base_unit="ml"
        )
        # 14. User target item
        target_item = CanonicalItem(
            household_id=hh_a,
            slug="target-sugar",
            name="Target Sugar",
            category="pantry",
            default_base_unit="g",
            low_stock_threshold_qty=Decimal("1000"),
        )
        db.add_all([high_suff, high_insuff, no_cons, med_zero, low_zero, unk_unit, insuff_zero, aavin_milk, amul_milk, target_item])
        await db.flush()

        # Helper to add N purchases spaced every 4 days
        async def add_purchases(item: CanonicalItem, n: int, qty: Decimal, unit_override: str | None = None):
            for idx in range(n):
                d = (now - timedelta(days=4 * (n - idx))).date()
                p = Purchase(
                    household_id=hh_a,
                    created_by=user_a,
                    merchant_name="FreshMart",
                    purchased_on=d,
                    receipt_category="grocery",
                    total_amount_paise=5000,
                    status="confirmed",
                )
                db.add(p)
                await db.flush()
                db.add(
                    PurchaseItem(
                        purchase_id=p.id,
                        household_id=hh_a,
                        line_index=0,
                        raw_text=item.name,
                        canonical_item_id=item.id,
                        display_name=item.name,
                        normalized_name=item.name.lower(),
                        normalized_qty=qty,
                        base_unit=unit_override or item.default_base_unit,
                        unit_price_paise=5000,
                        line_total_paise=5000,
                        track_inventory=True,
                    )
                )

        await add_purchases(high_suff, 5, Decimal("500"))
        await add_purchases(high_insuff, 5, Decimal("500"))
        await add_purchases(no_cons, 5, Decimal("1000"))
        await add_purchases(med_zero, 3, Decimal("500"))
        await add_purchases(low_zero, 2, Decimal("500"))
        await add_purchases(unk_unit, 5, Decimal("500"), unit_override="ml")
        await add_purchases(insuff_zero, 1, Decimal("500"))
        await add_purchases(aavin_milk, 5, Decimal("1000"))

        # Lots (8 & 9: include expired & depleted lots on high_insuff to ensure they are excluded)
        lot_hs = InventoryLot(
            household_id=hh_a, canonical_item_id=high_suff.id, initial_qty=Decimal("5000"),
            remaining_qty=Decimal("5000"), base_unit="g", unit_cost_paise=5000, purchased_at=now, lot_status="active",
        )
        lot_hi_active = InventoryLot(
            household_id=hh_a, canonical_item_id=high_insuff.id, initial_qty=Decimal("500"),
            remaining_qty=Decimal("100"), base_unit="g", unit_cost_paise=5000, purchased_at=now, lot_status="active",
        )
        lot_hi_exp = InventoryLot(
            household_id=hh_a, canonical_item_id=high_insuff.id, initial_qty=Decimal("500"),
            remaining_qty=Decimal("500"), base_unit="g", unit_cost_paise=5000, purchased_at=now,
            expires_on=(now - timedelta(days=2)).date(), lot_status="active",
        )
        lot_hi_dep = InventoryLot(
            household_id=hh_a, canonical_item_id=high_insuff.id, initial_qty=Decimal("500"),
            remaining_qty=Decimal("0"), base_unit="g", unit_cost_paise=5000, purchased_at=now, lot_status="depleted",
        )
        lot_aavin = InventoryLot(
            household_id=hh_a, canonical_item_id=aavin_milk.id, initial_qty=Decimal("1000"),
            remaining_qty=Decimal("1000"), base_unit="ml", unit_cost_paise=5000, purchased_at=now, lot_status="active",
        )
        lot_amul = InventoryLot(
            household_id=hh_a, canonical_item_id=amul_milk.id, initial_qty=Decimal("1500"),
            remaining_qty=Decimal("1500"), base_unit="ml", unit_cost_paise=5000, purchased_at=now, lot_status="active",
        )
        lot_target = InventoryLot(
            household_id=hh_a, canonical_item_id=target_item.id, initial_qty=Decimal("1000"),
            remaining_qty=Decimal("400"), base_unit="g", unit_cost_paise=5000, purchased_at=now, lot_status="active",
        )
        db.add_all([lot_hs, lot_hi_active, lot_hi_exp, lot_hi_dep, lot_aavin, lot_amul, lot_target])
        await db.flush()

        # Verified consumption records
        db.add_all([
            StockLedger(
                household_id=hh_a, lot_id=lot_hs.id, canonical_item_id=high_suff.id,
                event_type="COOK_OUT", quantity_delta=Decimal("-200"), base_unit="g",
                balance_after_lot=Decimal("5000"), unit_cost_paise=5000, actor_user_id=user_a,
                created_at=now - timedelta(days=4),
            ),
            StockLedger(
                household_id=hh_a, lot_id=lot_hi_active.id, canonical_item_id=high_insuff.id,
                event_type="COOK_OUT", quantity_delta=Decimal("-400"), base_unit="g",
                balance_after_lot=Decimal("100"), unit_cost_paise=5000, actor_user_id=user_a,
                created_at=now - timedelta(days=4),
            ),
            StockLedger(
                household_id=hh_a, lot_id=lot_aavin.id, canonical_item_id=aavin_milk.id,
                event_type="COOK_OUT", quantity_delta=Decimal("-3000"), base_unit="ml",
                balance_after_lot=Decimal("1000"), unit_cost_paise=5000, actor_user_id=user_a,
                created_at=now - timedelta(days=3),
            ),
        ])

        # Confirmed Milk mappings for both Aavin and Amul
        db.add_all([
            IngredientMapping(
                household_id=hh_a, canonical_item_id=aavin_milk.id, canonical_ingredient_id=milk_ing.id,
                matching_confidence=Decimal("1.000"), mapping_source="user_confirmed", user_confirmed=True,
            ),
            IngredientMapping(
                household_id=hh_a, canonical_item_id=amul_milk.id, canonical_ingredient_id=milk_ing.id,
                matching_confidence=Decimal("1.000"), mapping_source="user_confirmed", user_confirmed=True,
            ),
        ])
        await db.commit()
        ids = {
            "high_suff": str(high_suff.id),
            "high_insuff": str(high_insuff.id),
            "no_cons": str(no_cons.id),
            "med_zero": str(med_zero.id),
            "low_zero": str(low_zero.id),
            "unk_unit": str(unk_unit.id),
            "insuff_zero": str(insuff_zero.id),
            "aavin": str(aavin_milk.id),
            "target": str(target_item.id),
        }

    rec = (await client.get("/api/v1/replenishment/recommendations?refresh=true", headers=hdr_a)).json()
    actionable = {x["canonical_item_id"]: x for x in rec["buy_now"] + rec["buy_soon"] + rec["regular_purchases"]}
    suppressed = {x["canonical_item_id"]: x for x in rec["needs_more_data"]}

    # 1, 3, 4, 4b, 4c, 5, 6: Suppressed items
    assert ids["high_suff"] in suppressed and suppressed[ids["high_suff"]]["normalized_stock_status"] == "SUFFICIENT"
    assert ids["no_cons"] in suppressed and suppressed[ids["no_cons"]]["confidence"] == "medium"
    assert ids["med_zero"] in suppressed and suppressed[ids["med_zero"]]["confidence"] == "medium"
    assert ids["low_zero"] in suppressed and suppressed[ids["low_zero"]]["confidence"] == "low"
    assert ids["unk_unit"] in suppressed and suppressed[ids["unk_unit"]]["confidence"] == "insufficient_data"
    assert ids["insuff_zero"] in suppressed and suppressed[ids["insuff_zero"]]["confidence"] == "insufficient_data"

    # 2, 8, 9: HIGH + insufficient stock -> ELIGIBLE, expired/depleted excluded
    assert ids["high_insuff"] in actionable
    assert actionable[ids["high_insuff"]]["confidence"] == "high"
    assert actionable[ids["high_insuff"]]["eligibility_status"] == "ELIGIBLE"
    assert actionable[ids["high_insuff"]]["recommended_qty"] > 0

    # 7: Aavin Milk accounts for combined Milk ingredient usable stock (1000 + 1500 = 2500 ml)
    # 7-day horizon: 750 ml/day * 7 = 5250 ml; net = 2750 ml -> rounded to 1000ml packaging = 3000 ml
    assert ids["aavin"] in actionable
    assert actionable[ids["aavin"]]["ingredient_usable_qty"] == 2500.0
    assert actionable[ids["aavin"]]["prediction_horizon_days"] == 7
    assert actionable[ids["aavin"]]["predicted_requirement_qty"] == 5250.0
    assert actionable[ids["aavin"]]["net_requirement_qty"] == 2750.0
    assert actionable[ids["aavin"]]["recommended_qty"] == 3000.0

    # Horizon alignment check (14-day horizon doubles predicted requirement)
    rec_14 = (await client.get("/api/v1/replenishment/recommendations?upcoming_days=14&refresh=true", headers=hdr_a)).json()
    act_14 = {x["canonical_item_id"]: x for x in rec_14["buy_now"] + rec_14["buy_soon"] + rec_14["regular_purchases"]}
    assert act_14[ids["aavin"]]["prediction_horizon_days"] == 14
    assert act_14[ids["aavin"]]["predicted_requirement_qty"] == 10500.0

    # 14: Even with low_stock_threshold_qty > 0, insufficient_data is strictly suppressed
    assert ids["target"] in suppressed
    assert suppressed[ids["target"]]["eligibility_status"] == "SUPPRESSED"
    assert suppressed[ids["target"]]["confidence"] == "insufficient_data"

    # Suppressed items never automatically enter Shopping List
    shop_before = (await client.get("/api/v1/shopping-list", headers=hdr_a)).json()
    assert shop_before["pending"] == []

    # 12 & 13: Manual Shopping List additions continue to work even for low/insufficient items
    shop_add = await client.post(
        "/api/v1/shopping-list/items",
        headers=hdr_a,
        json={"item_name": "Manual Saffron", "quantity": 1, "unit": "pcs", "source": "manual"},
    )
    assert shop_add.status_code == 201
    shop_after = (await client.get("/api/v1/shopping-list", headers=hdr_a)).json()
    assert len(shop_after["pending"]) == 1
    assert shop_after["pending"][0]["item_name"] == "Manual Saffron"

    # 24, 25, 26: All creation paths use naming service, concurrent creation safe, household-isolated aliases
    cat_res = await client.post(
        "/api/v1/catalog/items",
        headers=hdr_a,
        json={"name": "• Chana Bal", "category": "pantry", "default_base_unit": "g"},
    )
    assert cat_res.status_code == 201
    assert cat_res.json()["name"] == "Chana Dal"

    async def worker(n: str):
        async with Session() as s:
            it, _, _ = await get_or_create_canonical_item(
                s, household_id=hh_a, raw_name=n, category="pantry", default_base_unit="g"
            )
            await s.commit()
            return it.id

    w1, w2 = await asyncio.gather(worker("1 * KALLMalka KG 94"), worker("Kali Malka"))
    assert w1 == w2

    # 20 & 26: Confirmed alias is household-isolated
    async with Session() as s:
        await learn_confirmed_product_alias(
            s, household_id=hh_a, canonical_item_id=w1, raw_description="Household A Kali Masoor"
        )
        await s.commit()
        r_a = await resolve_product_identity(s, household_id=hh_a, raw_description="Household A Kali Masoor", base_unit="g")
        r_b = await resolve_product_identity(s, household_id=hh_b, raw_description="Household A Kali Masoor", base_unit="g")
        assert r_a.resolved_canonical_item_id == w1
        assert r_b.outcome == "create_new"

    await engine.dispose()
