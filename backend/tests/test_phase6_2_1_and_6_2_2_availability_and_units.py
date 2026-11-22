"""Phase 6.2.1 & 6.2.2 tests: Recipe availability breakdown and product-aware pantry units."""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.db import build_async_engine, build_session_maker
from app.modules.inventory.ingredient_aggregation_service import (
    convert_to_target_unit,
    get_ingredient_inventory_summary,
    normalize_pantry_unit,
    suggest_units_for_product,
)
from app.modules.inventory.models import (
    CanonicalIngredient,
    CanonicalItem,
    IngredientMapping,
    InventoryLot,
    StockLedger,
)
from app.modules.purchases.models import Purchase, PurchaseItem
from app.modules.recipes.grounding import classify_recommendation_type, reconcile_ingredient


@pytest.mark.asyncio
async def test_phase6_2_1_recipe_availability_and_quantity_comparison() -> None:
    inv = [
        {
            "canonical_item_id": str(uuid.uuid4()),
            "name": "Rice",
            "qty": 500.0,
            "base_unit": "g",
            "confirmed_ingredient": True,
        },
        {
            "canonical_item_id": str(uuid.uuid4()),
            "name": "Toor Dal",
            "qty": 0.1,
            "base_unit": "kg",
            "confirmed_ingredient": True,
        },
        {
            "canonical_item_id": str(uuid.uuid4()),
            "name": "Milk",
            "qty": 1.0,
            "base_unit": "bottle",
            "confirmed_ingredient": True,
        },
    ]

    # 1. Sufficient quantity -> available
    r_rice = reconcile_ingredient({"name": "Rice", "required_qty": "250", "base_unit": "g"}, inv)
    assert r_rice is not None
    assert r_rice["status"] == "available"
    assert r_rice["unit_compatible"] is True

    # 2. Insufficient quantity (0.1 kg = 100 g < 250 g) -> partial with missing_qty in req_unit (g)
    r_dal = reconcile_ingredient({"name": "Toor Dal", "required_qty": "250", "base_unit": "g"}, inv)
    assert r_dal is not None
    assert r_dal["status"] == "partial"
    assert r_dal["missing_qty"] == "150"
    assert r_dal["base_unit"] == "g"

    # 3. Missing ingredient -> missing
    r_ghee = reconcile_ingredient({"name": "Ghee", "required_qty": "15", "base_unit": "ml"}, inv)
    assert r_ghee is not None
    assert r_ghee["status"] == "missing"

    # 4. Incompatible unit (bottle vs ml) -> partial, unit_compatible=False (never falsely
    # available)
    r_milk = reconcile_ingredient({"name": "Milk", "required_qty": "250", "base_unit": "ml"}, inv)
    assert r_milk is not None
    assert r_milk["status"] == "partial"
    assert r_milk["unit_compatible"] is False

    # 5. Optional missing ingredient does not block COOK_NOW
    r_opt = reconcile_ingredient(
        {"name": "Coriander", "required_qty": "10", "base_unit": "g"}, inv, is_optional=True
    )
    assert r_opt is not None
    assert classify_recommendation_type([r_rice, r_opt], [], inv) == "COOK_NOW"

    # 6. One insufficient essential ingredient -> COOK_WITH_SUBSTITUTIONS (Almost ready)
    assert classify_recommendation_type([r_rice, r_dal], [], inv) == "COOK_WITH_SUBSTITUTIONS"


def test_phase6_2_2_unit_conversions_and_product_aware_suggestions() -> None:
    # Safe dimension conversions
    assert convert_to_target_unit(Decimal("1"), "kg", "g")[0] == Decimal("1000.000")
    assert convert_to_target_unit(Decimal("1"), "l", "ml")[0] == Decimal("1000.000")
    assert convert_to_target_unit(Decimal("2"), "dozen", "pcs")[0] == Decimal("24.000")
    assert convert_to_target_unit(Decimal("12"), "pcs", "dozen")[0] == Decimal("1.000")

    # Packaging units remain distinct from pcs/g/ml
    assert normalize_pantry_unit("packs") == "pack"
    assert normalize_pantry_unit("bottles") == "bottle"
    assert convert_to_target_unit(Decimal("2"), "pcs", "g")[0] is None
    assert convert_to_target_unit(Decimal("1"), "bottle", "ml")[0] is None
    assert convert_to_target_unit(Decimal("1"), "pack", "pcs")[0] is None

    # Product-aware suggestions
    assert suggest_units_for_product("Basmati Rice", "kg")[:2] == ["g", "kg"]
    assert suggest_units_for_product("Full Cream Milk", "l")[:2] == ["ml", "l"]
    assert suggest_units_for_product("Eggs", "pcs")[:2] == ["pcs", "dozen"]
    assert suggest_units_for_product("Chocolate Bar", "pcs")[:2] == ["pcs", "g"]
    assert set(suggest_units_for_product("Biscuits", "pack")) == {"pack", "pcs", "packet"}
    assert suggest_units_for_product("Bread", "loaf") == ["loaf", "slice"]


@pytest.mark.asyncio
async def test_phase6_2_2_pantry_edit_preserves_ledger_and_rejects_cross_dimension(
    client: AsyncClient,
    jwks_provider,
    temp_db_url: str,
) -> None:
    sub = f"user-{uuid.uuid4().hex[:8]}"
    token = jwks_provider.make_token(sub=sub, email=f"{sub}@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    # Bootstrap household
    me_resp = await client.get("/api/v1/auth/me", headers=headers)
    assert me_resp.status_code == 200
    hh_id = uuid.UUID(me_resp.json()["active_household"]["id"])
    user_id = uuid.UUID(me_resp.json()["user"]["id"])

    engine = build_async_engine(temp_db_url)
    session_factory = build_session_maker(engine)
    async with session_factory() as db:  # type: AsyncSession

        async def _get_or_create_ing(
            name: str, norm: str, cat: str, std_unit: str
        ) -> CanonicalIngredient:
            existing = (
                await db.execute(
                    select(CanonicalIngredient).where(CanonicalIngredient.normalized_name == norm)
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing
            created = CanonicalIngredient(
                name=name, normalized_name=norm, category=cat, standard_unit=std_unit
            )
            db.add(created)
            await db.flush()
            return created

        c_ing_rice = await _get_or_create_ing("Rice", "rice", "grains", "g")
        c_ing_milk = await _get_or_create_ing("Milk", "milk", "dairy", "ml")
        c_ing_eggs = await _get_or_create_ing("Eggs", "eggs", "dairy", "pcs")
        c_ing_biscuits = await _get_or_create_ing("Biscuits", "biscuits", "snacks", "pcs")

        item_rice = CanonicalItem(
            household_id=hh_id,
            slug=f"rice-{uuid.uuid4().hex[:6]}",
            name="Basmati Rice",
            category="grains",
            default_base_unit="g",
        )
        item_milk = CanonicalItem(
            household_id=hh_id,
            slug=f"milk-{uuid.uuid4().hex[:6]}",
            name="Toned Milk",
            category="dairy",
            default_base_unit="ml",
        )
        item_eggs = CanonicalItem(
            household_id=hh_id,
            slug=f"eggs-{uuid.uuid4().hex[:6]}",
            name="Farm Eggs",
            category="dairy",
            default_base_unit="pcs",
        )
        item_biscuits = CanonicalItem(
            household_id=hh_id,
            slug=f"biscuits-{uuid.uuid4().hex[:6]}",
            name="Marie Biscuits",
            category="snacks",
            default_base_unit="pcs",
        )
        item_biscuits_pcs = CanonicalItem(
            household_id=hh_id,
            slug=f"biscuits-pcs-{uuid.uuid4().hex[:6]}",
            name="Marie Biscuits Loose",
            category="snacks",
            default_base_unit="pcs",
        )
        db.add_all([item_rice, item_milk, item_eggs, item_biscuits, item_biscuits_pcs])
        await db.flush()

        db.add_all(
            [
                IngredientMapping(
                    household_id=hh_id,
                    canonical_item_id=item_rice.id,
                    canonical_ingredient_id=c_ing_rice.id,
                    matching_confidence=Decimal("1.0"),
                    mapping_source="manual",
                    user_confirmed=True,
                ),
                IngredientMapping(
                    household_id=hh_id,
                    canonical_item_id=item_milk.id,
                    canonical_ingredient_id=c_ing_milk.id,
                    matching_confidence=Decimal("1.0"),
                    mapping_source="manual",
                    user_confirmed=True,
                ),
                IngredientMapping(
                    household_id=hh_id,
                    canonical_item_id=item_eggs.id,
                    canonical_ingredient_id=c_ing_eggs.id,
                    matching_confidence=Decimal("1.0"),
                    mapping_source="manual",
                    user_confirmed=True,
                ),
                IngredientMapping(
                    household_id=hh_id,
                    canonical_item_id=item_biscuits.id,
                    canonical_ingredient_id=c_ing_biscuits.id,
                    matching_confidence=Decimal("1.0"),
                    mapping_source="manual",
                    user_confirmed=True,
                ),
                IngredientMapping(
                    household_id=hh_id,
                    canonical_item_id=item_biscuits_pcs.id,
                    canonical_ingredient_id=c_ing_biscuits.id,
                    matching_confidence=Decimal("1.0"),
                    mapping_source="manual",
                    user_confirmed=True,
                ),
            ]
        )

        now = datetime.now(UTC)
        purchase = Purchase(
            household_id=hh_id,
            created_by=user_id,
            status="confirmed",
            source_type="manual",
            document_type="receipt",
            merchant_name="Local Mart",
            purchased_on=date.today(),
            currency_code="INR",
            total_amount_paise=50000,
        )
        db.add(purchase)
        await db.flush()
        p_item = PurchaseItem(
            purchase_id=purchase.id,
            household_id=hh_id,
            line_index=0,
            raw_text="Basmati Rice 1 kg",
            canonical_item_id=item_rice.id,
            display_name="Basmati Rice",
            category="grains",
            pack_size="1 kg",
            normalized_qty=Decimal("1000.000"),
            base_unit="g",
            line_total_paise=12000,
        )
        db.add(p_item)

        lot_rice = InventoryLot(
            household_id=hh_id,
            canonical_item_id=item_rice.id,
            initial_qty=Decimal("1000.000"),
            remaining_qty=Decimal("1000.000"),
            base_unit="g",
            purchased_at=now,
            expires_on=date.today() + timedelta(days=90),
            lot_status="active",
        )
        lot_milk = InventoryLot(
            household_id=hh_id,
            canonical_item_id=item_milk.id,
            initial_qty=Decimal("1000.000"),
            remaining_qty=Decimal("1000.000"),
            base_unit="ml",
            purchased_at=now,
            expires_on=date.today() + timedelta(days=5),
            lot_status="active",
        )
        lot_eggs = InventoryLot(
            household_id=hh_id,
            canonical_item_id=item_eggs.id,
            initial_qty=Decimal("12.000"),
            remaining_qty=Decimal("12.000"),
            base_unit="pcs",
            purchased_at=now,
            lot_status="active",
        )
        lot_bisc_pack = InventoryLot(
            household_id=hh_id,
            canonical_item_id=item_biscuits.id,
            initial_qty=Decimal("200.000"),
            remaining_qty=Decimal("200.000"),
            base_unit="g",
            purchased_at=now,
            lot_status="active",
        )
        lot_bisc_pcs = InventoryLot(
            household_id=hh_id,
            canonical_item_id=item_biscuits_pcs.id,
            initial_qty=Decimal("5.000"),
            remaining_qty=Decimal("5.000"),
            base_unit="pcs",
            purchased_at=now,
            lot_status="active",
        )
        db.add_all([lot_rice, lot_milk, lot_eggs, lot_bisc_pack, lot_bisc_pcs])
        await db.commit()

        rice_ing_id = c_ing_rice.id
        milk_ing_id = c_ing_milk.id
        eggs_ing_id = c_ing_eggs.id
        bisc_ing_id = c_ing_biscuits.id
        p_item_id = p_item.id
        lot_rice_id = lot_rice.id

    # 1. Edit rice from 1 kg to 500 g -> lot_rice becomes 0.500 kg, purchase_item untouched
    edit_rice = await client.patch(
        f"/api/v1/inventory/ingredients/{rice_ing_id}",
        headers=headers,
        json={"name": "Rice", "target_quantity": 500, "unit": "g"},
    )
    assert edit_rice.status_code == 200
    assert Decimal(str(edit_rice.json()["total_remaining_qty"])) == Decimal("500.000")

    # 2. Edit milk from 1 L to 500 ml
    edit_milk = await client.patch(
        f"/api/v1/inventory/ingredients/{milk_ing_id}",
        headers=headers,
        json={"name": "Milk", "target_quantity": 500, "unit": "ml"},
    )
    assert edit_milk.status_code == 200
    assert Decimal(str(edit_milk.json()["total_remaining_qty"])) == Decimal("500.000")

    # 3. Edit eggs using dozen (2 dozen = 24 pcs)
    edit_eggs = await client.patch(
        f"/api/v1/inventory/ingredients/{eggs_ing_id}",
        headers=headers,
        json={"name": "Eggs", "target_quantity": 2, "unit": "dozen"},
    )
    assert edit_eggs.status_code == 200
    assert Decimal(str(edit_eggs.json()["total_remaining_qty"])) == Decimal("24.000")

    # 4. Reject pcs -> g without conversion factor AND prevent partial rename
    bad_edit = await client.patch(
        f"/api/v1/inventory/ingredients/{eggs_ing_id}",
        headers=headers,
        json={"name": "Renamed Eggs", "target_quantity": 500, "unit": "g"},
    )
    assert bad_edit.status_code == 422
    assert bad_edit.json()["error"]["code"] == "INCOMPATIBLE_UNIT"

    # 5. Verify StockLedger, PurchaseItem, and incompatible-unit separation in aggregation
    async with session_factory() as db:
        p_row = await db.get(PurchaseItem, p_item_id)
        assert p_row is not None
        assert (
            p_row.normalized_qty == Decimal("1000.000")
            and p_row.base_unit == "g"
            and p_row.pack_size == "1 kg"
        )

        lot_row = await db.get(InventoryLot, lot_rice_id)
        assert lot_row is not None
        assert lot_row.remaining_qty == Decimal("500.000")

        egg_ing_row = await db.get(CanonicalIngredient, eggs_ing_id)
        assert egg_ing_row is not None and egg_ing_row.name == "Eggs"  # No partial rename

        ledgers = (
            (await db.execute(select(StockLedger).where(StockLedger.lot_id == lot_rice_id)))
            .scalars()
            .all()
        )
        assert any(
            led.event_type == "ADJUSTMENT_OUT" and led.quantity_delta == Decimal("-500.000")
            for led in ledgers
        )

        summary = await get_ingredient_inventory_summary(db, hh_id)
        bisc_summary = next(
            i for i in summary.ingredients if i.canonical_ingredient_id == bisc_ing_id
        )
        # Incompatible dimensions (g vs pcs) must NOT be aggregated together
        assert len(bisc_summary.unit_groups) == 2

    await engine.dispose()
