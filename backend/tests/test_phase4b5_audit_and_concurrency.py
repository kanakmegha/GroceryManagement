"""Phase 4B.5: Concurrent duplicate creation and similar-but-distinct bidirectional tests."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.modules.catalog.ingredient_identity_service import (
    are_ingredients_incompatible,
    get_or_create_canonical_ingredient,
    normalize_ingredient_name_key,
    resolve_ingredient_identity,
)
from app.modules.inventory.models import CanonicalIngredient, IngredientAlias

SIMILAR_BUT_DISTINCT_PAIRS: list[tuple[str, str]] = [
    ("Basmati Rice", "Idli Rice"),
    ("Basmati Rice", "Rice Flour"),
    ("Basmati Rice", "Sona Masoori Rice"),
    ("Coconut Milk", "Milk"),
    ("Coconut Oil", "Sunflower Oil"),
    ("Idli Rice", "Rice Flour"),
    ("Idli Rice", "Sona Masoori Rice"),
    ("Mustard Oil", "Sunflower Oil"),
    ("Rice Flour", "Sona Masoori Rice"),
    ("Tomato", "Tomato Puree"),
]


@pytest.mark.asyncio
async def test_concurrent_duplicate_creation_and_unrelated_integrity_error(
    temp_db_url: str,
) -> None:
    """Verify two concurrent requests never create duplicate CanonicalIngredients
    and unrelated IntegrityErrors are never swallowed.
    """
    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def worker(raw_name: str, category: str = "grain", unit: str = "g"):
        async with Session() as db:
            outcome = await get_or_create_canonical_ingredient(
                db,
                name=raw_name,
                category=category,
                standard_unit=unit,
            )
            await db.commit()
            return outcome

    # 1. Same spelling concurrently
    o1, o2 = await asyncio.gather(worker("Quinoa"), worker("Quinoa"))
    assert o1.ingredient is not None and o2.ingredient is not None
    assert o1.ingredient.id == o2.ingredient.id

    # 2. Different capitalization concurrently
    o3, o4 = await asyncio.gather(worker("buckwheat"), worker("BUCKWHEAT"))
    assert o3.ingredient is not None and o4.ingredient is not None
    assert o3.ingredient.id == o4.ingredient.id

    # 3. Extra whitespace concurrently
    o5, o6 = await asyncio.gather(worker("  Amaranth   Grain "), worker("Amaranth Grain"))
    assert o5.ingredient is not None and o6.ingredient is not None
    assert o5.ingredient.id == o6.ingredient.id

    # 4. Hyphen vs space concurrently
    o7, o8 = await asyncio.gather(worker("Black-Quinoa"), worker("Black Quinoa"))
    assert o7.ingredient is not None and o8.ingredient is not None
    assert o7.ingredient.id == o8.ingredient.id

    # 5. Existing alias vs new canonical name concurrently
    async with Session() as db:
        millet = await get_or_create_canonical_ingredient(
            db, name="Finger Millet", category="grain", standard_unit="g"
        )
        assert millet.ingredient is not None
        db.add(
            IngredientAlias(
                canonical_ingredient_id=millet.ingredient.id,
                household_id=None,
                alias="Ragi",
                normalized_alias="ragi",
                alias_type="transliteration",
                language="hi",
                source="seed",
            )
        )
        await db.commit()

    o9, o10 = await asyncio.gather(worker("Ragi"), worker("Finger Millet"))
    assert o9.ingredient is not None and o10.ingredient is not None
    assert o9.ingredient.id == o10.ingredient.id == millet.ingredient.id

    # Confirm exact DB row counts for created test ingredients
    async with Session() as db:
        for outcome in (o1, o3, o5, o7, o9):
            rows = (
                (
                    await db.execute(
                        select(CanonicalIngredient).where(
                            CanonicalIngredient.normalized_name
                            == outcome.ingredient.normalized_name
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 1

        # 6. Unrelated IntegrityError (CHECK constraint on standard_unit) must NOT be swallowed
        with pytest.raises(IntegrityError):
            await get_or_create_canonical_ingredient(
                db,
                name="Invalid Unit Ingredient",
                category="grain",
                standard_unit="invalid_unit_xyz",
            )

    await engine.dispose()


@pytest.mark.asyncio
async def test_similar_but_distinct_pairs_bidirectional(
    temp_db_url: str,
) -> None:
    """Verify all 10 similar-but-distinct pairs remain incompatible and distinct in both
    directions.
    """
    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        by_name = {
            ing.name: ing for ing in (await db.execute(select(CanonicalIngredient))).scalars().all()
        }
        for name_a, name_b in SIMILAR_BUT_DISTINCT_PAIRS:
            ing_a = by_name[name_a]
            ing_b = by_name[name_b]
            assert normalize_ingredient_name_key(name_a) != normalize_ingredient_name_key(name_b)
            assert (
                are_ingredients_incompatible(ing_a.normalized_name, ing_b.normalized_name) is True
            )
            assert (
                are_ingredients_incompatible(ing_b.normalized_name, ing_a.normalized_name) is True
            )

            res_a = await resolve_ingredient_identity(db, raw_name=name_a)
            res_b = await resolve_ingredient_identity(db, raw_name=name_b)
            assert res_a.matched_ingredient_id == ing_a.id
            assert res_a.matched_ingredient_id != ing_b.id
            assert res_b.matched_ingredient_id == ing_b.id
            assert res_b.matched_ingredient_id != ing_a.id

    await engine.dispose()
