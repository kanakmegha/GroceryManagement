"""Phase 4D: Tests for ingredient mapping confirmation, rejection, isolation, and remaining-stock
totals.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.modules.inventory.models import (
    CanonicalItem,
    IngredientMapping,
    InventoryLot,
)
from app.modules.inventory.schemas import AdjustLotRequest
from app.modules.inventory.service import adjust_lot
from tests.conftest import EphemeralJWKSProvider


@pytest.mark.asyncio
async def test_phase4d_confirm_reject_multiple_milk_isolation_and_remaining_qty(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
    temp_db_url: str,
) -> None:
    token_a = jwks_provider.make_token(sub="user_4d_a", email="4da@example.com", name="4D A")
    token_b = jwks_provider.make_token(sub="user_4d_b", email="4db@example.com", name="4D B")
    hdr_a = {"Authorization": f"Bearer {token_a}"}
    hdr_b = {"Authorization": f"Bearer {token_b}"}

    me_a = await client.get("/api/v1/auth/me", headers=hdr_a)
    me_b = await client.get("/api/v1/auth/me", headers=hdr_b)
    assert me_a.status_code == 200 and me_b.status_code == 200
    hh_a = UUID(me_a.json()["active_household"]["id"])
    user_a = UUID(me_a.json()["user"]["id"])
    hdr_a["X-Household-Id"] = str(hh_a)
    hdr_b["X-Household-Id"] = me_b.json()["active_household"]["id"]

    # 1. Fetch ingredient catalog
    cat_res = await client.get("/api/v1/inventory/ingredients/catalog", headers=hdr_a)
    assert cat_res.status_code == 200
    ings = {x["normalized_name"]: x for x in cat_res.json()["items"]}
    milk_id = ings["milk"]["id"]
    curd_id = ings["curd"]["id"]
    almonds_id = ings["almonds"]["id"]

    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        amul = CanonicalItem(
            household_id=hh_a,
            slug="amul-gold-1l",
            name="Amul Gold Milk 1L",
            category="dairy",
            default_base_unit="ml",
        )
        aavin = CanonicalItem(
            household_id=hh_a,
            slug="aavin-toned-500ml",
            name="Aavin Toned Milk 500ml",
            category="dairy",
            default_base_unit="ml",
        )
        hair_oil = CanonicalItem(
            household_id=hh_a,
            slug="dabur-almond-hair-oil",
            name="Dabur Almond Hair Oil 290ml",
            category="non_food",
            default_base_unit="ml",
            pantry_eligible=False,
        )
        db.add_all([amul, aavin, hair_oil])
        await db.flush()

        # Unconfirmed suggestion on amul -> Milk, and on hair_oil -> Almonds (to test reject)
        db.add(
            IngredientMapping(
                household_id=hh_a,
                canonical_item_id=amul.id,
                canonical_ingredient_id=UUID(milk_id),
                matching_confidence=Decimal("0.920"),
                mapping_source="catalogue",
                user_confirmed=False,
            )
        )
        db.add(
            IngredientMapping(
                household_id=hh_a,
                canonical_item_id=hair_oil.id,
                canonical_ingredient_id=UUID(almonds_id),
                matching_confidence=Decimal("0.750"),
                mapping_source="classifier_v2",
                user_confirmed=False,
            )
        )

        now = datetime.now(UTC)
        lot_amul = InventoryLot(
            household_id=hh_a,
            canonical_item_id=amul.id,
            initial_qty=Decimal("1000.000"),
            remaining_qty=Decimal("1000.000"),
            base_unit="ml",
            unit_cost_paise=7000,
            purchased_at=now,
            lot_status="active",
        )
        lot_aavin = InventoryLot(
            household_id=hh_a,
            canonical_item_id=aavin.id,
            initial_qty=Decimal("500.000"),
            remaining_qty=Decimal("500.000"),
            base_unit="ml",
            unit_cost_paise=3000,
            purchased_at=now,
            lot_status="active",
        )
        lot_oil = InventoryLot(
            household_id=hh_a,
            canonical_item_id=hair_oil.id,
            initial_qty=Decimal("290.000"),
            remaining_qty=Decimal("290.000"),
            base_unit="ml",
            unit_cost_paise=15500,
            purchased_at=now,
            lot_status="active",
        )
        db.add_all([lot_amul, lot_aavin, lot_oil])
        await db.flush()

        # Consume 250 ml from amul -> remaining is 750 ml (NOT initial 1000 ml)
        await adjust_lot(
            db,
            household_id=hh_a,
            actor_user_id=user_a,
            lot_id=lot_amul.id,
            payload=AdjustLotRequest(
                event_type="ADJUSTMENT_OUT",
                quantity_delta=Decimal("-250.000"),
                notes="Used in tea",
            ),
        )
        await db.commit()
        amul_id, aavin_id, oil_id = str(amul.id), str(aavin.id), str(hair_oil.id)

    # Before confirmation: 0 confirmed ingredients, 2 unconfirmed suggestions
    pre = (await client.get("/api/v1/inventory/ingredients/summary", headers=hdr_a)).json()
    assert pre["ingredients"] == []
    assert pre["unconfirmed_suggestions_count"] == 2

    # Household B cannot confirm Household A's item -> 403
    forb = await client.post(
        "/api/v1/inventory/ingredients/mappings/confirm",
        headers=hdr_b,
        json={"canonical_item_id": amul_id, "canonical_ingredient_id": milk_id},
    )
    assert forb.status_code == 403

    # Confirm Amul & Aavin -> Milk; Reject Hair Oil -> Almonds
    c1 = await client.post(
        "/api/v1/inventory/ingredients/mappings/confirm",
        headers=hdr_a,
        json={"canonical_item_id": amul_id, "canonical_ingredient_id": milk_id},
    )
    assert c1.status_code == 200
    assert c1.json()["user_confirmed"] is True
    assert c1.json()["mapping_source"] == "user_confirmed"

    # Conflict check: trying to map Amul to Curd without allow_override -> 409
    conf = await client.post(
        "/api/v1/inventory/ingredients/mappings/confirm",
        headers=hdr_a,
        json={"canonical_item_id": amul_id, "canonical_ingredient_id": curd_id},
    )
    assert conf.status_code == 409

    c2 = await client.post(
        "/api/v1/inventory/ingredients/mappings/confirm",
        headers=hdr_a,
        json={"canonical_item_id": aavin_id, "canonical_ingredient_id": milk_id},
    )
    assert c2.status_code == 200

    rej = await client.post(
        "/api/v1/inventory/ingredients/mappings/reject",
        headers=hdr_a,
        json={"canonical_item_id": oil_id},
    )
    assert rej.status_code == 200
    assert rej.json()["rejected"] is True

    # Verify By Ingredient reflects actual remaining stock (750 + 500 = 1250 ml, NOT 1500 ml)
    post = (await client.get("/api/v1/inventory/ingredients/summary", headers=hdr_a)).json()
    assert len(post["ingredients"]) == 1
    milk_sum = post["ingredients"][0]
    assert milk_sum["name"] == "Milk"
    assert Decimal(milk_sum["total_remaining_qty"]) == Decimal("1250.000")
    assert milk_sum["contributing_product_count"] == 2
    assert post["unconfirmed_suggestions_count"] == 0

    # Verify By Product retains separate product entries and separate price histories
    prod_sum = (await client.get("/api/v1/inventory/summary", headers=hdr_a)).json()["items"]
    by_pid = {x["canonical_item_id"]: x for x in prod_sum}
    assert Decimal(by_pid[amul_id]["total_remaining_qty"]) == Decimal("750.000")
    assert Decimal(by_pid[aavin_id]["total_remaining_qty"]) == Decimal("500.000")

    ph_amul = await client.get(f"/api/v1/inventory/items/{amul_id}/price-history", headers=hdr_a)
    ph_aavin = await client.get(f"/api/v1/inventory/items/{aavin_id}/price-history", headers=hdr_a)
    assert ph_amul.status_code == 200 and ph_aavin.status_code == 200

    await engine.dispose()


@pytest.mark.asyncio
async def test_phase4d1_all_six_high_suggestions_and_rejection_loop_safety(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
    temp_db_url: str,
) -> None:
    """Verify all 6 HIGH-confidence suggestions, MEDIUM/LOW non-auto-confirm, and rejection
    non-recreation.
    """
    from app.modules.catalog.ingredient_mapping_service import resolve_or_suggest_mapping

    token = jwks_provider.make_token(sub="user_4d1", email="4d1@example.com", name="4D1 User")
    hdr = {"Authorization": f"Bearer {token}"}
    me = await client.get("/api/v1/auth/me", headers=hdr)
    assert me.status_code == 200
    hh_id = UUID(me.json()["active_household"]["id"])
    hdr["X-Household-Id"] = str(hh_id)

    cat_res = await client.get("/api/v1/inventory/ingredients/catalog", headers=hdr)
    ings_by_name = {x["name"]: x["id"] for x in cat_res.json()["items"]}

    six_pairs = [
        ("Arhar Dal", "Toor Dal", "g", Decimal("500.000")),
        ("BADAM", "Almonds", "g", Decimal("3750.000")),
        ("Chana Dal", "Chana Dal", "g", Decimal("9000.000")),
        (
            "Eastern Chilli Powder - Perfect Colour, Smell & Taste 250 g Pouch",
            "Red Chilli Powder",
            "g",
            Decimal("1.000"),
        ),
        ("KHULA ATTA", "Whole Wheat Flour (Atta)", "g", Decimal("41000.000")),
        ("Milk", "Milk", "g", Decimal("1000.000")),
    ]

    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)

    created_items: list[tuple[str, str, Decimal]] = []
    now = datetime.now(UTC)

    async with Session() as db:
        for idx, (prod_name, ing_name, unit, qty) in enumerate(six_pairs):
            ci = CanonicalItem(
                household_id=hh_id,
                slug=f"p4d1-{idx}",
                name=prod_name,
                category="pantry",
                default_base_unit=unit,
            )
            db.add(ci)
            await db.flush()
            db.add(
                IngredientMapping(
                    household_id=hh_id,
                    canonical_item_id=ci.id,
                    canonical_ingredient_id=UUID(ings_by_name[ing_name]),
                    matching_confidence=Decimal("0.920"),
                    mapping_source="catalogue",
                    user_confirmed=False,
                )
            )
            db.add(
                InventoryLot(
                    household_id=hh_id,
                    canonical_item_id=ci.id,
                    initial_qty=qty,
                    remaining_qty=qty,
                    base_unit=unit,
                    unit_cost_paise=5000,
                    purchased_at=now,
                    lot_status="active",
                )
            )
            created_items.append((str(ci.id), ing_name, qty))

        # MEDIUM and LOW items must NOT be auto-confirmed
        med_apple = CanonicalItem(
            household_id=hh_id,
            slug="mported-daily-apple",
            name="mported Daily Apple (Sebu) R$",
            category="produce",
            default_base_unit="pcs",
        )
        low_snack = CanonicalItem(
            household_id=hh_id,
            slug="homenack-crunchy",
            name="(Homenack) Crunchy& (125 g) Shinezan Tasty nack",
            category="packaged_grocery",
            default_base_unit="g",
        )
        db.add_all([med_apple, low_snack])
        await db.flush()
        res_med = await resolve_or_suggest_mapping(
            db, household_id=hh_id, canonical_item=med_apple, auto_map_high_confidence=True
        )
        res_low = await resolve_or_suggest_mapping(
            db, household_id=hh_id, canonical_item=low_snack, auto_map_high_confidence=True
        )
        assert res_med.tier == "medium" and not res_med.user_confirmed
        assert res_low.tier == "low" and res_low.status == "unmapped"
        await db.commit()

    # Confirm first 5, reject the 6th (Milk) to verify rejection does not recreate or alter stock
    for ci_id, ing_name, _qty in created_items[:5]:
        res = await client.post(
            "/api/v1/inventory/ingredients/mappings/confirm",
            headers=hdr,
            json={"canonical_item_id": ci_id, "canonical_ingredient_id": ings_by_name[ing_name]},
        )
        assert res.status_code == 200
        assert res.json()["user_confirmed"] is True

    milk_ci_id = created_items[5][0]
    rej = await client.post(
        "/api/v1/inventory/ingredients/mappings/reject",
        headers=hdr,
        json={"canonical_item_id": milk_ci_id},
    )
    assert rej.status_code == 200 and rej.json()["rejected"] is True

    # Refresh twice -> Milk stays unmapped (not recreated in a loop), stock remains 1000.000 g
    s1 = (await client.get("/api/v1/inventory/ingredients/summary", headers=hdr)).json()
    s2 = (await client.get("/api/v1/inventory/ingredients/summary", headers=hdr)).json()
    assert len(s1["ingredients"]) == 5
    assert s1["unconfirmed_suggestions_count"] == 0
    assert s2["unconfirmed_suggestions_count"] == 0
    milk_unmapped = [u for u in s2["unmapped_products"] if u["canonical_item_id"] == milk_ci_id][0]
    assert milk_unmapped["suggestion_status"] == "unmapped"
    assert Decimal(milk_unmapped["total_remaining_qty"]) == Decimal("1000.000")

    await engine.dispose()
