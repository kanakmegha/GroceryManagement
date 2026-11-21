from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.errors import AppError
from app.modules.auth.models import Household, User
from app.modules.catalog.ingredient_identity_service import (
    normalize_ingredient_input,
    register_ingredient_alias,
    resolve_ingredient_identity,
)
from app.modules.catalog.ingredient_mapping_service import confirm_ingredient_mapping
from app.modules.catalog.ingredient_merge_service import (
    merge_canonical_ingredients,
    rollback_canonical_ingredient_merge,
)
from app.modules.inventory.models import (
    CanonicalIngredient,
    CanonicalItem,
    IngredientAlias,
    IngredientMapping,
    InventoryLot,
    StockLedger,
)
from app.modules.purchases.models import Purchase, PurchaseItem


async def test_alias_lifecycle_and_reload_persistence(temp_db_url: str) -> None:
    engine1 = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session1 = async_sessionmaker(engine1, expire_on_commit=False)

    async with Session1() as db:
        u1 = User(clerk_user_id="clerk_4b2_1", email="4b2a@example.com", display_name="4B2A")
        u2 = User(clerk_user_id="clerk_4b2_2", email="4b2b@example.com", display_name="4B2B")
        db.add_all([u1, u2])
        await db.flush()
        hh1 = Household(name="HH1", created_by=u1.id)
        hh2 = Household(name="HH2", created_by=u2.id)
        db.add_all([hh1, hh2])
        await db.flush()

        tomato = (
            await db.execute(
                select(CanonicalIngredient).where(CanonicalIngredient.normalized_name == "tomato")
            )
        ).scalar_one()
        onion = (
            await db.execute(
                select(CanonicalIngredient).where(CanonicalIngredient.normalized_name == "onion")
            )
        ).scalar_one()

        # 1. Global alias registration
        await register_ingredient_alias(
            db,
            household_id=None,
            canonical_ingredient_id=tomato.id,
            alias="Country Tomato",
            language="en",
            alias_type="synonym",
        )
        # 2. Household-specific alias registration
        await register_ingredient_alias(
            db,
            household_id=hh1.id,
            canonical_ingredient_id=tomato.id,
            alias="Nattu Thakkali",
            language="ta",
            alias_type="regional_name",
        )
        await db.commit()
        hh1_id, hh2_id, tomato_id, onion_id = hh1.id, hh2.id, tomato.id, onion.id
    await engine1.dispose()

    # 8. Reload database with a fresh engine & verify persistence, global lookup, and isolation
    engine2 = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session2 = async_sessionmaker(engine2, expire_on_commit=False)
    async with Session2() as db:
        # 3. Global alias lookup from household context
        r_glob = await resolve_ingredient_identity(
            db, raw_name="Country Tomato", household_id=hh2_id
        )
        assert r_glob.tier == "high" and r_glob.matched_ingredient_id == tomato_id

        # 4 & 5. Household alias visible to hh1, isolated from hh2
        r_hh1 = await resolve_ingredient_identity(
            db, raw_name="Nattu Thakkali", household_id=hh1_id
        )
        assert r_hh1.tier == "high" and r_hh1.matched_ingredient_id == tomato_id
        r_hh2 = await resolve_ingredient_identity(
            db, raw_name="Nattu Thakkali", household_id=hh2_id
        )
        assert r_hh2.matched_ingredient_id is None

        # 6 & 7. Duplicate/conflicting alias rejected before any mutation
        before_cnt = len((await db.execute(select(IngredientAlias))).scalars().all())
        with pytest.raises(AppError) as exc:
            await register_ingredient_alias(
                db,
                household_id=hh1_id,
                canonical_ingredient_id=onion_id,
                alias="  nattu   thakkali  ",
            )
        assert exc.value.code == "ALIAS_CONFLICT"
        after_cnt = len((await db.execute(select(IngredientAlias))).scalars().all())
        assert after_cnt == before_cnt

    await engine2.dispose()


async def test_merge_lifecycle_no_partial_mutation_and_history_preservation(
    temp_db_url: str,
) -> None:
    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        u = User(clerk_user_id="clerk_4b2_m", email="4b2m@example.com", display_name="4B2M")
        db.add(u)
        await db.flush()
        hh = Household(name="HHM", created_by=u.id)
        db.add(hh)
        await db.flush()

        tomato = (
            await db.execute(
                select(CanonicalIngredient).where(CanonicalIngredient.normalized_name == "tomato")
            )
        ).scalar_one()
        puree = (
            await db.execute(
                select(CanonicalIngredient).where(CanonicalIngredient.normalized_name == "tomato_puree")
            )
        ).scalar_one()
        dup_tom = CanonicalIngredient(
            name="Red Tomato",
            normalized_name="red_tomato_dup",
            category="vegetable",
            subcategory="nightshade",
            standard_unit="g",
            cooking_variants=[],
            supported_units=["g"],
        )
        db.add(dup_tom)
        await db.flush()

        prod = CanonicalItem(
            household_id=hh.id,
            slug="farm-tomato-1kg",
            name="Farm Tomato 1kg",
            category="vegetable",
            default_base_unit="g",
        )
        db.add(prod)
        await db.flush()
        pur = Purchase(
            household_id=hh.id,
            created_by=u.id,
            status="confirmed",
            source_type="manual",
            purchased_on=date(2026, 11, 20),
            total_amount_paise=4500,
        )
        db.add(pur)
        await db.flush()
        pi = PurchaseItem(
            purchase_id=pur.id,
            household_id=hh.id,
            canonical_item_id=prod.id,
            display_name="Farm Tomato 1kg",
            raw_text="FARM TOMATO 1KG 45.00",
            normalized_qty=Decimal("1000.000"),
            base_unit="g",
            unit_price_paise=4500,
            line_total_paise=4500,
        )
        db.add(pi)
        await db.flush()
        lot = InventoryLot(
            household_id=hh.id,
            canonical_item_id=prod.id,
            purchase_item_id=pi.id,
            initial_qty=Decimal("1000.000"),
            remaining_qty=Decimal("1000.000"),
            base_unit="g",
            unit_cost_paise=4500,
        )
        db.add(lot)
        await db.flush()
        led = StockLedger(
            household_id=hh.id,
            lot_id=lot.id,
            canonical_item_id=prod.id,
            event_type="PURCHASE_IN",
            quantity_delta=Decimal("1000.000"),
            base_unit="g",
            balance_after_lot=Decimal("1000.000"),
            unit_cost_paise=4500,
            actor_user_id=u.id,
        )
        db.add(led)
        await confirm_ingredient_mapping(
            db, household_id=hh.id, canonical_item_id=prod.id, canonical_ingredient_id=dup_tom.id
        )
        await db.commit()

        # Self-merge rejected
        with pytest.raises(AppError) as e_self:
            await merge_canonical_ingredients(db, survivor_id=tomato.id, duplicate_id=tomato.id)
        assert e_self.value.status_code == 422

        # Invalid survivor rejected
        with pytest.raises(AppError) as e_inv:
            await merge_canonical_ingredients(db, survivor_id=uuid4(), duplicate_id=dup_tom.id)
        assert e_inv.value.status_code == 404

        # Incompatible merge rejected with zero mutation
        with pytest.raises(AppError) as e_inc:
            await merge_canonical_ingredients(db, survivor_id=tomato.id, duplicate_id=puree.id)
        assert e_inc.value.code == "INCOMPATIBLE_INGREDIENTS"
        assert (await db.get(CanonicalIngredient, puree.id)).is_active is True

        # Valid merge preserves product, purchase, lot, ledger
        rec = await merge_canonical_ingredients(db, survivor_id=tomato.id, duplicate_id=dup_tom.id)
        await db.commit()
        assert (await db.get(CanonicalItem, prod.id)).is_active is True
        assert (await db.get(InventoryLot, lot.id)).remaining_qty == Decimal("1000.000")
        assert (await db.get(PurchaseItem, pi.id)).unit_price_paise == 4500
        assert (await db.get(StockLedger, led.id)).quantity_delta == Decimal("1000.000")

        # Already-merged source rejected
        with pytest.raises(AppError) as e_dup:
            await merge_canonical_ingredients(db, survivor_id=tomato.id, duplicate_id=dup_tom.id)
        assert e_dup.value.code == "ALREADY_MERGED"

        # Rollback + repeated rollback rejection
        await rollback_canonical_ingredient_merge(db, record=rec)
        await db.commit()
        assert (await db.get(CanonicalIngredient, dup_tom.id)).is_active is True

        with pytest.raises(AppError) as e_rev:
            await rollback_canonical_ingredient_merge(db, record=rec)
        assert e_rev.value.code == "ALREADY_REVERSED"

    await engine.dispose()


async def test_ocr_adversarial_numeric_preservation(temp_db_url: str) -> None:
    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        # Pack sizes & measurements preserved; raw_input preserved; A2 Ghee does not corrupt '2'
        n_pack = normalize_ingredient_input("T0mat0 500g")
        assert n_pack.raw_input == "T0mat0 500g"
        assert n_pack.packaging_qty == "500g"

        n_a2 = normalize_ingredient_input("A2 Cow Ghee 500ml")
        assert n_a2.raw_input == "A2 Cow Ghee 500ml"
        assert n_a2.packaging_qty == "500ml"

        n_num = normalize_ingredient_input("Vitamin B12 100g")
        assert n_num.raw_input == "Vitamin B12 100g"
        assert n_num.packaging_qty == "100g"
        r_num = await resolve_ingredient_identity(db, raw_name="Vitamin B12 100g")
        assert r_num.tier != "high"

    await engine.dispose()
