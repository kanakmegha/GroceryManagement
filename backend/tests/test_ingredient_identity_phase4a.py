from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.errors import AppError
from app.modules.auth.models import Household, User
from app.modules.catalog.ingredient_identity_service import (
    are_ingredients_incompatible,
    detect_ingredient_duplicate_candidates,
    normalize_ingredient_input,
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
    IngredientMapping,
    InventoryLot,
)


async def test_ingredient_normalization_equivalence_and_incompatibility(temp_db_url: str) -> None:
    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        # 1, 2, 3, 6: Attributes, singular/plural, OCR noise
        n_tom = normalize_ingredient_input('" Fresh Red Tomatoes!! 1kg')
        assert n_tom.core_normalized_name == "tomato"
        assert n_tom.preferred_name == "Tomato"
        assert "fresh" in n_tom.attributes and "red" in n_tom.attributes
        assert n_tom.packaging_qty == "1kg"

        r_tom = await resolve_ingredient_identity(db, raw_name='" Fresh Red Tomatoes!! 1kg')
        assert r_tom.tier == "high"
        assert r_tom.matched_ingredient_name == "Tomato"

        # 4, 5: Hindi transliterations & Indian grocery synonyms
        for raw, expected_norm in [
            ("Badam", "almonds"),
            ("Whole Wheat Atta", "whole_wheat_flour"),
            ("Arhar Dal", "toor_dal"),
            ("Haldi", "turmeric_powder"),
            ("Jeera", "cumin_seeds"),
            ("Palak", "spinach"),
            ("Rava", "semolina"),
            ("Tamatar", "tomato"),
            ("Pyaz", "onion"),
            ("Aloo", "potato"),
        ]:
            res = await resolve_ingredient_identity(db, raw_name=raw)
            assert res.tier == "high", f"{raw} -> {res}"
            ing = await db.get(CanonicalIngredient, res.matched_ingredient_id)
            assert ing is not None and ing.normalized_name == expected_norm

        # 7, 8, 9: Forms, varieties, and incompatible pairs
        assert are_ingredients_incompatible("basmati_rice", "idli_rice") is True
        assert are_ingredients_incompatible("rice_flour", "basmati_rice") is True
        assert are_ingredients_incompatible("coconut_milk", "milk") is True
        assert are_ingredients_incompatible("mustard_oil", "sunflower_oil") is True
        assert are_ingredients_incompatible("tomato_puree", "tomato") is True

        r_puree = await resolve_ingredient_identity(db, raw_name="Tomato Puree")
        assert r_puree.tier != "high" or r_puree.matched_ingredient_name != "Tomato"

        r_coco = await resolve_ingredient_identity(db, raw_name="Coconut Milk")
        assert r_coco.matched_ingredient_name != "Milk"

        # 10: Ambiguous/fuzzy match never yields HIGH
        r_amb = await resolve_ingredient_identity(db, raw_name="Almond Essence")
        assert r_amb.tier != "high"

    await engine.dispose()


async def test_ingredient_duplicate_discovery_merge_rollback_and_isolation(
    temp_db_url: str,
) -> None:
    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        u1 = User(clerk_user_id="clerk_4a_1", email="4a1@example.com", display_name="4A1")
        u2 = User(clerk_user_id="clerk_4a_2", email="4a2@example.com", display_name="4A2")
        db.add_all([u1, u2])
        await db.flush()
        hh1 = Household(name="HH1", created_by=u1.id)
        hh2 = Household(name="HH2", created_by=u2.id)
        db.add_all([hh1, hh2])
        await db.flush()

        # Simulate a duplicate ingredient introduced via external import ("Fresh Tomato")
        dup_ing = CanonicalIngredient(
            name="Fresh Tomato",
            normalized_name="fresh_tomato",
            category="vegetable",
            subcategory="nightshade",
            standard_unit="g",
            cooking_variants=[],
            supported_units=["g", "kg"],
        )
        db.add(dup_ing)
        await db.flush()

        # Candidate discovery finds ("Tomato", "Fresh Tomato")
        cands = await detect_ingredient_duplicate_candidates(db)
        tom_cands = [c for c in cands if c.cluster_key == "tomato"]
        assert len(tom_cands) == 1
        assert set(tom_cands[0].original_names) == {"Tomato", "Fresh Tomato"}

        # Map distinct CanonicalItems (hh1 & hh2) + inventory lot
        prod_hh1 = CanonicalItem(
            household_id=hh1.id, slug="desi-tomato", name="Desi Tomato 1kg", category="vegetable", default_base_unit="g"
        )
        prod_hh2 = CanonicalItem(
            household_id=hh2.id, slug="hybrid-tomato", name="Hybrid Tomato 1kg", category="vegetable", default_base_unit="g"
        )
        db.add_all([prod_hh1, prod_hh2])
        await db.flush()

        lot_hh1 = InventoryLot(
            household_id=hh1.id,
            canonical_item_id=prod_hh1.id,
            initial_qty=Decimal("1000.000"),
            remaining_qty=Decimal("1000.000"),
            base_unit="g",
            unit_cost_paise=4000,
        )
        db.add(lot_hh1)
        await db.flush()

        surv_ing = (
            await db.execute(
                select(CanonicalIngredient).where(CanonicalIngredient.normalized_name == "tomato")
            )
        ).scalar_one()

        await confirm_ingredient_mapping(
            db, household_id=hh1.id, canonical_item_id=prod_hh1.id, canonical_ingredient_id=dup_ing.id
        )
        await confirm_ingredient_mapping(
            db, household_id=hh2.id, canonical_item_id=prod_hh2.id, canonical_ingredient_id=surv_ing.id
        )
        await db.commit()

        # 13, 15, 16: Merge dup_ing -> surv_ing; CanonicalItems, mappings, household isolation, and lots preserved
        rec = await merge_canonical_ingredients(db, survivor_id=surv_ing.id, duplicate_id=dup_ing.id)
        await db.commit()

        assert (await db.get(CanonicalIngredient, dup_ing.id)).is_active is False
        assert (await db.get(CanonicalItem, prod_hh1.id)).is_active is True
        assert (await db.get(CanonicalItem, prod_hh2.id)).is_active is True
        assert (await db.get(InventoryLot, lot_hh1.id)).remaining_qty == Decimal("1000.000")

        hh1_map = (
            await db.execute(
                select(IngredientMapping).where(IngredientMapping.household_id == hh1.id)
            )
        ).scalar_one()
        assert hh1_map.canonical_ingredient_id == surv_ing.id

        # Repeated merge rejected
        with pytest.raises(AppError) as exc_rep:
            await merge_canonical_ingredients(db, survivor_id=surv_ing.id, duplicate_id=dup_ing.id)
        assert exc_rep.value.code == "ALREADY_MERGED"

        # 14: Rollback restores duplicate ingredient & original mapping
        await rollback_canonical_ingredient_merge(db, record=rec)
        await db.commit()
        assert (await db.get(CanonicalIngredient, dup_ing.id)).is_active is True
        hh1_map_after = (
            await db.execute(
                select(IngredientMapping).where(IngredientMapping.household_id == hh1.id)
            )
        ).scalar_one()
        assert hh1_map_after.canonical_ingredient_id == dup_ing.id

    await engine.dispose()
