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
from app.modules.catalog.ingredient_merge_service import (
    merge_canonical_ingredients,
    rollback_canonical_ingredient_merge,
)
from app.modules.inventory.models import CanonicalIngredient, IngredientAlias


@pytest.mark.parametrize(
    ("norm_a", "norm_b"),
    [
        ("basmati_rice", "rice_flour"),
        ("basmati_rice", "idli_rice"),
        ("tomato", "tomato_puree"),
        ("milk", "coconut_milk"),
        ("mustard_oil", "sunflower_oil"),
    ],
)
async def test_direct_incompatible_merge_rejected_with_409(
    temp_db_url: str, norm_a: str, norm_b: str
) -> None:
    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        ing_a = (
            await db.execute(
                select(CanonicalIngredient).where(CanonicalIngredient.normalized_name == norm_a)
            )
        ).scalar_one()
        ing_b = (
            await db.execute(
                select(CanonicalIngredient).where(CanonicalIngredient.normalized_name == norm_b)
            )
        ).scalar_one()

        with pytest.raises(AppError) as exc_info:
            await merge_canonical_ingredients(db, survivor_id=ing_a.id, duplicate_id=ing_b.id)
        assert exc_info.value.status_code == 409
        assert exc_info.value.code == "INCOMPATIBLE_INGREDIENTS"

        assert (await db.get(CanonicalIngredient, ing_a.id)).is_active is True
        assert (await db.get(CanonicalIngredient, ing_b.id)).is_active is True

    await engine.dispose()


async def test_db_aliases_isolation_conflicts_ocr_and_merge_rollback(
    temp_db_url: str,
) -> None:
    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        u1 = User(clerk_user_id="clerk_4b1_1", email="4b1a@example.com", display_name="4B1A")
        u2 = User(clerk_user_id="clerk_4b1_2", email="4b1b@example.com", display_name="4B1B")
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

        # Global seeded alias persists and resolves via DB
        res_global = await resolve_ingredient_identity(db, raw_name="tamatar", household_id=hh1.id)
        assert res_global.tier == "high"
        assert res_global.method == "db_alias"
        assert res_global.matched_ingredient_id == tomato.id

        # Household-specific alias isolation & conflict rejection
        await register_ingredient_alias(
            db,
            household_id=hh1.id,
            canonical_ingredient_id=tomato.id,
            alias="Nattu Thakkali",
            language="ta",
            alias_type="regional_name",
        )
        await db.commit()

        res_hh1 = await resolve_ingredient_identity(
            db, raw_name="Nattu Thakkali", household_id=hh1.id
        )
        assert res_hh1.matched_ingredient_id == tomato.id
        res_hh2 = await resolve_ingredient_identity(
            db, raw_name="Nattu Thakkali", household_id=hh2.id
        )
        assert res_hh2.matched_ingredient_id is None

        # Conflict in same household rejected
        with pytest.raises(AppError) as exc_alias:
            await register_ingredient_alias(
                db,
                household_id=hh1.id,
                canonical_ingredient_id=onion.id,
                alias="Nattu Thakkali",
            )
        assert exc_alias.value.code == "ALIAS_CONFLICT"

        # Context-aware OCR normalization: preserves raw_input, idempotent, NEVER auto-HIGH
        for ocr_raw, expected_name in [
            ("T0mat0 500g", "Tomato"),
            ("CHANA D4L 1kg", "Chana Dal"),
            ("FLAX SEED5 200g", "Flax Seeds"),
            ("MUSTARD 01L 1L", "Mustard Oil"),
        ]:
            n1 = normalize_ingredient_input(ocr_raw)
            n2 = normalize_ingredient_input(ocr_raw)
            assert n1.raw_input == ocr_raw
            assert n1 == n2
            r_ocr = await resolve_ingredient_identity(db, raw_name=ocr_raw)
            assert r_ocr.tier == "medium"
            assert r_ocr.matched_ingredient_name == expected_name

        # Merge + rollback restores reassigned IngredientAlias
        dup_tom = CanonicalIngredient(
            name="Desi Tomato",
            normalized_name="desi_tomato_dup",
            category="vegetable",
            subcategory="nightshade",
            standard_unit="g",
            cooking_variants=[],
            supported_units=["g"],
        )
        db.add(dup_tom)
        await db.flush()
        dup_alias = await register_ingredient_alias(
            db,
            household_id=hh1.id,
            canonical_ingredient_id=dup_tom.id,
            alias="Desi Tamatar",
            language="hi",
            alias_type="regional_name",
        )
        await db.commit()

        rec = await merge_canonical_ingredients(db, survivor_id=tomato.id, duplicate_id=dup_tom.id)
        await db.commit()
        assert (await db.get(IngredientAlias, dup_alias.id)).canonical_ingredient_id == tomato.id

        await rollback_canonical_ingredient_merge(db, record=rec)
        await db.commit()
        assert (await db.get(IngredientAlias, dup_alias.id)).canonical_ingredient_id == dup_tom.id

    await engine.dispose()
