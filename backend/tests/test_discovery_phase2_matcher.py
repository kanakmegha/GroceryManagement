from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alembic import command
from app.core.db import build_async_engine, build_session_maker
from app.modules.auth.models import Household, User
from app.modules.discovery.catalogue_seed import seed_dish_catalogue
from app.modules.discovery.matcher import match_dishes_for_household
from app.modules.discovery.models import Dish, DishFeedback, DishIngredient, SavedDish
from app.modules.inventory.models import (
    CanonicalIngredient,
    CanonicalItem,
    IngredientMapping,
    InventoryLot,
    StockLedger,
)
from app.modules.recipes.models import HouseholdRecipePreference

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _run_upgrade_head(db_url: str) -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, "head")


async def _create_household(db: AsyncSession, suffix: str) -> Household:
    owner = User(clerk_user_id=f"clerk_{suffix}", display_name=f"User {suffix}")
    db.add(owner)
    await db.flush()
    hh = Household(name=f"Household {suffix}", created_by=owner.id)
    db.add(hh)
    await db.flush()
    return hh


async def _ensure_canonical_ingredient(
    db: AsyncSession,
    *,
    name: str,
    variant_id: str | None,
    family_id: str | None,
    is_unspecified_variant: bool = False,
    standard_unit: str = "g",
) -> CanonicalIngredient:
    norm_name = name.strip().lower()
    existing = None
    if variant_id:
        existing = (
            await db.execute(
                select(CanonicalIngredient).where(CanonicalIngredient.variant_id == variant_id)
            )
        ).scalar_one_or_none()
    if existing is not None:
        return existing

    ing = CanonicalIngredient(
        name=name,
        normalized_name=f"{norm_name} ({variant_id or family_id or 'test'})",
        variant_id=variant_id,
        family_id=family_id,
        is_unspecified_variant=is_unspecified_variant,
        category="pantry",
        standard_unit=standard_unit,
    )
    db.add(ing)
    await db.flush()
    return ing


async def _add_pantry_stock(
    db: AsyncSession,
    *,
    household_id: UUID,
    canonical_ing: CanonicalIngredient,
    item_name: str,
    qty: Decimal,
    unit: str,
    user_confirmed: bool = True,
    lot_status: str = "active",
    expires_on: date | None = None,
) -> InventoryLot:
    item = CanonicalItem(
        household_id=household_id,
        slug=f"{item_name.lower().replace(' ', '-')}-{household_id}",
        name=item_name,
        category="pantry",
        default_base_unit=canonical_ing.standard_unit,
    )
    db.add(item)
    await db.flush()

    mapping = IngredientMapping(
        household_id=household_id,
        canonical_item_id=item.id,
        canonical_ingredient_id=canonical_ing.id,
        mapping_source="user_confirmed",
        matching_confidence=Decimal("1.000"),
        user_confirmed=user_confirmed,
    )
    db.add(mapping)

    lot = InventoryLot(
        household_id=household_id,
        canonical_item_id=item.id,
        initial_qty=qty if qty > Decimal("0") else Decimal("100"),
        remaining_qty=qty,
        base_unit=unit,
        lot_status=lot_status,
        expires_on=expires_on,
        purchased_at=datetime.now(UTC),
    )
    db.add(lot)
    await db.flush()
    return lot


async def _make_dish(
    db: AsyncSession,
    *,
    slug: str,
    name: str,
    diet_tags: list[str] | None = None,
    cuisine: str = "Indian",
    region: str = "Pan-Indian",
    meal_types: list[str] | None = None,
    prep_minutes: int | None = 15,
    ingredients: list[dict[str, object]],
) -> Dish:
    dish = Dish(
        slug=slug,
        canonical_name=name,
        diet_tags=diet_tags or ["vegetarian"],
        cuisine=cuisine,
        region=region,
        meal_types=meal_types or ["lunch"],
        prep_minutes=prep_minutes,
        is_active=True,
    )
    db.add(dish)
    await db.flush()
    for idx, spec in enumerate(ingredients):
        db.add(
            DishIngredient(
                dish_id=dish.id,
                canonical_ingredient_id=spec.get("canonical_ingredient_id"),  # type: ignore[arg-type]
                ingredient_name=str(spec["ingredient_name"]),
                requirement_mode=str(spec["requirement_mode"]),
                requirement_type=str(spec.get("requirement_type", "required")),
                variant_id=spec.get("variant_id"),  # type: ignore[arg-type]
                family_id=spec.get("family_id"),  # type: ignore[arg-type]
                compatible_variant_ids=list(spec.get("compatible_variant_ids", [])),  # type: ignore[arg-type]
                quantity=spec.get("quantity"),  # type: ignore[arg-type]
                unit=spec.get("unit"),  # type: ignore[arg-type]
                substitutions_json=list(spec.get("substitutions_json", [])),  # type: ignore[arg-type]
                sort_order=idx,
            )
        )
    await db.flush()
    return dish


@pytest.mark.asyncio
async def test_empty_pantry_never_produces_can_make_now(tmp_path: Path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'empty_pantry.db'}"
    await asyncio.to_thread(_run_upgrade_head, db_url)
    engine = build_async_engine(db_url)
    session_maker = build_session_maker(engine)

    try:
        async with session_maker() as db:
            await seed_dish_catalogue(db)
            hh = await _create_household(db, "empty")
            await db.commit()

            resp = await match_dishes_for_household(db, hh.id)
            assert resp.can_make_now == []
            assert resp.counts["can_make_now"] == 0
            assert resp.counts["almost_there"] + resp.counts["explore_more"] == 10
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_exact_variant_compatible_family_and_generic_matching(tmp_path: Path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'modes.db'}"
    await asyncio.to_thread(_run_upgrade_head, db_url)
    engine = build_async_engine(db_url)
    session_maker = build_session_maker(engine)

    try:
        async with session_maker() as db:
            hh = await _create_household(db, "modes")
            full_cream = await _ensure_canonical_ingredient(
                db,
                name="Full Cream Milk",
                variant_id="milk__full_cream_milk",
                family_id="milk",
                standard_unit="ml",
            )
            mustard_oil = await _ensure_canonical_ingredient(
                db,
                name="Mustard Oil",
                variant_id="edible_oil__mustard_oil",
                family_id="edible_oil",
                standard_unit="ml",
            )
            sunflower_oil = await _ensure_canonical_ingredient(
                db,
                name="Sunflower Oil",
                variant_id="edible_oil__sunflower_oil",
                family_id="edible_oil",
                standard_unit="ml",
            )
            onion_generic = await _ensure_canonical_ingredient(
                db,
                name="Onion",
                variant_id="onion__unspecified",
                family_id="onion",
                is_unspecified_variant=True,
                standard_unit="g",
            )
            spinach_generic = await _ensure_canonical_ingredient(
                db,
                name="Spinach",
                variant_id="spinach__unspecified",
                family_id="spinach",
                is_unspecified_variant=True,
                standard_unit="g",
            )

            # Pantry has Full Cream Milk, Mustard Oil, generic Onion, generic Spinach
            await _add_pantry_stock(
                db,
                household_id=hh.id,
                canonical_ing=full_cream,
                item_name="Amul Gold Milk",
                qty=Decimal("500"),
                unit="ml",
            )
            await _add_pantry_stock(
                db,
                household_id=hh.id,
                canonical_ing=mustard_oil,
                item_name="Kachi Ghani Mustard Oil",
                qty=Decimal("500"),
                unit="ml",
            )
            await _add_pantry_stock(
                db,
                household_id=hh.id,
                canonical_ing=onion_generic,
                item_name="Loose Onion",
                qty=Decimal("300"),
                unit="g",
            )
            await _add_pantry_stock(
                db,
                household_id=hh.id,
                canonical_ing=spinach_generic,
                item_name="Fresh Palak",
                qty=Decimal("250"),
                unit="g",
            )

            # 1. Exact variant: Toned Milk must NOT match Full Cream Milk
            await _make_dish(
                db,
                slug="toned-milk-chai",
                name="Toned Milk Chai",
                ingredients=[
                    {
                        "ingredient_name": "Toned Milk",
                        "requirement_mode": "exact_variant",
                        "variant_id": "milk__toned_milk",
                        "family_id": "milk",
                    }
                ],
            )
            # 2. Exact variant: Red Onion must NOT be satisfied by generic onion__unspecified
            await _make_dish(
                db,
                slug="red-onion-salad",
                name="Red Onion Salad",
                ingredients=[
                    {
                        "ingredient_name": "Red Onion",
                        "requirement_mode": "exact_variant",
                        "variant_id": "onion__red_onion",
                        "family_id": "onion",
                    }
                ],
            )
            # 3. Compatible family allowlist: Mustard Oil must NOT satisfy neutral-oil allowlist
            await _make_dish(
                db,
                slug="neutral-oil-poha",
                name="Neutral Oil Poha",
                ingredients=[
                    {
                        "ingredient_name": "Neutral Cooking Oil",
                        "requirement_mode": "compatible_family",
                        "family_id": "edible_oil",
                        "compatible_variant_ids": [
                            "edible_oil__sunflower_oil",
                            "edible_oil__groundnut_oil",
                        ],
                    }
                ],
            )
            # 4. Generic ingredient: generic Spinach matches cleanly
            await _make_dish(
                db,
                slug="palak-soup",
                name="Palak Soup",
                ingredients=[
                    {
                        "ingredient_name": "Spinach",
                        "requirement_mode": "generic_ingredient",
                        "family_id": "spinach",
                        "canonical_ingredient_id": spinach_generic.id,
                    }
                ],
            )
            await db.commit()

            resp = await match_dishes_for_household(db, hh.id)
            assert [d.slug for d in resp.can_make_now] == ["palak-soup"]
            almost_slugs = {d.slug for d in resp.almost_there}
            assert almost_slugs == {"toned-milk-chai", "red-onion-salad", "neutral-oil-poha"}

            # Now add Sunflower Oil -> neutral-oil-poha moves to can_make_now
            await _add_pantry_stock(
                db,
                household_id=hh.id,
                canonical_ing=sunflower_oil,
                item_name="Fortune Sunflower Oil",
                qty=Decimal("500"),
                unit="ml",
            )
            await db.commit()
            resp2 = await match_dishes_for_household(db, hh.id)
            assert {d.slug for d in resp2.can_make_now} == {"palak-soup", "neutral-oil-poha"}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_verified_vs_unverified_substitutions_and_ratio_safety(tmp_path: Path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'subs.db'}"
    await asyncio.to_thread(_run_upgrade_head, db_url)
    engine = build_async_engine(db_url)
    session_maker = build_session_maker(engine)

    try:
        async with session_maker() as db:
            hh = await _create_household(db, "subs")
            tofu = await _ensure_canonical_ingredient(
                db,
                name="Firm Tofu",
                variant_id="tofu__firm_tofu",
                family_id="tofu",
                standard_unit="g",
            )
            await _add_pantry_stock(
                db,
                household_id=hh.id,
                canonical_ing=tofu,
                item_name="Organic Firm Tofu",
                qty=Decimal("250"),
                unit="g",
            )

            # 1. Verified substitution with explicit 1:1 g conversion -> satisfied
            await _make_dish(
                db,
                slug="verified-sub-dish",
                name="Verified Sub Dish",
                ingredients=[
                    {
                        "ingredient_name": "Paneer",
                        "requirement_mode": "generic_ingredient",
                        "family_id": "paneer",
                        "quantity": Decimal("200.000"),
                        "unit": "g",
                        "substitutions_json": [
                            {
                                "original_variant_id": "paneer__unspecified",
                                "substitute_variant_id": "tofu__firm_tofu",
                                "substitute_name": "Firm Tofu",
                                "verified": True,
                                "applicable_dish_slug": "verified-sub-dish",
                                "conversion_ratio": 1.0,
                                "conversion_unit": "g",
                            }
                        ],
                    }
                ],
            )
            # 2. Unverified substitution -> must NOT satisfy
            await _make_dish(
                db,
                slug="unverified-sub-dish",
                name="Unverified Sub Dish",
                ingredients=[
                    {
                        "ingredient_name": "Paneer",
                        "requirement_mode": "generic_ingredient",
                        "family_id": "paneer",
                        "substitutions_json": [
                            {
                                "substitute_variant_id": "tofu__firm_tofu",
                                "substitute_name": "Firm Tofu",
                                "verified": False,
                            }
                        ],
                    }
                ],
            )
            # 3. Verified sub WITHOUT ratio/unit when qty is required -> must NOT assume 1:1
            await _make_dish(
                db,
                slug="missing-ratio-sub-dish",
                name="Missing Ratio Sub Dish",
                ingredients=[
                    {
                        "ingredient_name": "Paneer",
                        "requirement_mode": "generic_ingredient",
                        "family_id": "paneer",
                        "quantity": Decimal("200.000"),
                        "unit": "g",
                        "substitutions_json": [
                            {
                                "substitute_variant_id": "tofu__firm_tofu",
                                "substitute_name": "Firm Tofu",
                                "verified": True,
                            }
                        ],
                    }
                ],
            )
            await db.commit()

            resp = await match_dishes_for_household(db, hh.id)
            assert [d.slug for d in resp.can_make_now] == ["verified-sub-dish"]
            req_out = resp.can_make_now[0].requirements[0]
            assert req_out.matched_via == "verified_substitution"
            assert req_out.matched_substitution is not None
            assert req_out.matched_substitution.substitute_name == "Firm Tofu"

            almost_slugs = {d.slug for d in resp.almost_there}
            assert almost_slugs == {"unverified-sub-dish", "missing-ratio-sub-dish"}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unit_conversions_incomparable_units_and_no_cross_variant_combine(
    tmp_path: Path,
) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'units.db'}"
    await asyncio.to_thread(_run_upgrade_head, db_url)
    engine = build_async_engine(db_url)
    session_maker = build_session_maker(engine)

    try:
        async with session_maker() as db:
            hh = await _create_household(db, "units")
            rice = await _ensure_canonical_ingredient(
                db, name="Basmati Rice", variant_id="rice__basmati_rice", family_id="rice"
            )
            milk = await _ensure_canonical_ingredient(
                db,
                name="Milk",
                variant_id="milk__full_cream_milk",
                family_id="milk",
                standard_unit="ml",
            )
            eggs = await _ensure_canonical_ingredient(
                db,
                name="Eggs",
                variant_id="eggs__unspecified",
                family_id="eggs",
                standard_unit="pcs",
            )
            red_onion = await _ensure_canonical_ingredient(
                db, name="Red Onion", variant_id="onion__red_onion", family_id="onion"
            )
            white_onion = await _ensure_canonical_ingredient(
                db, name="White Onion", variant_id="onion__white_onion", family_id="onion"
            )
            tomato = await _ensure_canonical_ingredient(
                db, name="Tomato", variant_id="tomato__desi_tomato", family_id="tomato"
            )

            # 1000 g rice, 1000 ml milk, 12 pcs eggs (tested against kg, l, dozen dish reqs)
            await _add_pantry_stock(
                db,
                household_id=hh.id,
                canonical_ing=rice,
                item_name="Rice 1kg",
                qty=Decimal("1000"),
                unit="g",
            )
            await _add_pantry_stock(
                db,
                household_id=hh.id,
                canonical_ing=milk,
                item_name="Milk 1L",
                qty=Decimal("1000"),
                unit="ml",
            )
            await _add_pantry_stock(
                db,
                household_id=hh.id,
                canonical_ing=eggs,
                item_name="Eggs 1doz",
                qty=Decimal("12"),
                unit="pcs",
            )
            # Alternative variants: 100g red + 150g white onion (must NOT combine to 250g!)
            await _add_pantry_stock(
                db,
                household_id=hh.id,
                canonical_ing=red_onion,
                item_name="Red Onion",
                qty=Decimal("100"),
                unit="g",
            )
            await _add_pantry_stock(
                db,
                household_id=hh.id,
                canonical_ing=white_onion,
                item_name="White Onion",
                qty=Decimal("150"),
                unit="g",
            )
            # Incomparable unit: 4 pcs tomato vs 200 g requirement
            await _add_pantry_stock(
                db,
                household_id=hh.id,
                canonical_ing=tomato,
                item_name="Desi Tomato",
                qty=Decimal("4"),
                unit="pcs",
            )

            # Mass/Volume/Count conversion dish -> can_make_now (g->kg, ml->l, pcs->dozen)
            await _make_dish(
                db,
                slug="converted-dish",
                name="Converted Dish",
                ingredients=[
                    {
                        "ingredient_name": "Basmati Rice",
                        "requirement_mode": "exact_variant",
                        "variant_id": "rice__basmati_rice",
                        "family_id": "rice",
                        "quantity": Decimal("0.5"),
                        "unit": "kg",
                    },
                    {
                        "ingredient_name": "Full Cream Milk",
                        "requirement_mode": "exact_variant",
                        "variant_id": "milk__full_cream_milk",
                        "family_id": "milk",
                        "quantity": Decimal("0.5"),
                        "unit": "l",
                    },
                    {
                        "ingredient_name": "Eggs",
                        "requirement_mode": "generic_ingredient",
                        "family_id": "eggs",
                        "quantity": Decimal("0.5"),
                        "unit": "dozen",
                    },
                ],
            )
            # Split-variant onion dish: needs 200g; 100g red + 150g white must stay partial!
            await _make_dish(
                db,
                slug="split-onion-dish",
                name="Split Onion Dish",
                ingredients=[
                    {
                        "ingredient_name": "Onion",
                        "requirement_mode": "compatible_family",
                        "family_id": "onion",
                        "quantity": Decimal("200"),
                        "unit": "g",
                    }
                ],
            )
            # Incomparable unit: needs 200g tomato, pantry has 4 pcs -> partial INCOMPARABLE_UNIT
            await _make_dish(
                db,
                slug="incomparable-tomato-dish",
                name="Incomparable Tomato Dish",
                ingredients=[
                    {
                        "ingredient_name": "Tomato",
                        "requirement_mode": "compatible_family",
                        "family_id": "tomato",
                        "quantity": Decimal("200"),
                        "unit": "g",
                    }
                ],
            )
            await db.commit()

            resp = await match_dishes_for_household(db, hh.id)
            assert [d.slug for d in resp.can_make_now] == ["converted-dish"]

            by_slug = {d.slug: d for d in resp.almost_there}
            assert by_slug["split-onion-dish"].requirements[0].status == "partial"
            assert by_slug["split-onion-dish"].requirements[0].reason == "INSUFFICIENT_QUANTITY"
            assert by_slug["split-onion-dish"].requirements[0].available_qty == Decimal("150.000")

            assert by_slug["incomparable-tomato-dish"].requirements[0].status == "partial"
            assert (
                by_slug["incomparable-tomato-dish"].requirements[0].reason == "INCOMPARABLE_UNIT"
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_expired_zero_unconfirmed_optional_and_bucket_boundaries(tmp_path: Path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'buckets.db'}"
    await asyncio.to_thread(_run_upgrade_head, db_url)
    engine = build_async_engine(db_url)
    session_maker = build_session_maker(engine)

    try:
        async with session_maker() as db:
            hh = await _create_household(db, "buckets")
            ing1 = await _ensure_canonical_ingredient(
                db, name="Expired Spinach", variant_id="spinach__unspecified", family_id="spinach"
            )
            ing2 = await _ensure_canonical_ingredient(
                db, name="Zero Garlic", variant_id="garlic__unspecified", family_id="garlic"
            )
            ing3 = await _ensure_canonical_ingredient(
                db, name="Unconfirmed Ginger", variant_id="ginger__unspecified", family_id="ginger"
            )
            ing4 = await _ensure_canonical_ingredient(
                db, name="Salt", variant_id="salt__unspecified", family_id="salt"
            )

            yesterday = datetime.now(UTC).date() - timedelta(days=1)
            await _add_pantry_stock(
                db,
                household_id=hh.id,
                canonical_ing=ing1,
                item_name="Old Spinach",
                qty=Decimal("200"),
                unit="g",
                expires_on=yesterday,
            )
            await _add_pantry_stock(
                db,
                household_id=hh.id,
                canonical_ing=ing2,
                item_name="Empty Garlic",
                qty=Decimal("0"),
                unit="g",
            )
            await _add_pantry_stock(
                db,
                household_id=hh.id,
                canonical_ing=ing3,
                item_name="Unconfirmed Ginger",
                qty=Decimal("100"),
                unit="g",
                user_confirmed=False,
            )
            await _add_pantry_stock(
                db,
                household_id=hh.id,
                canonical_ing=ing4,
                item_name="Table Salt",
                qty=Decimal("500"),
                unit="g",
            )

            # 0 missing required (optional missing ghee must NOT block can_make_now)
            await _make_dish(
                db,
                slug="bucket-0",
                name="Bucket 0",
                ingredients=[
                    {
                        "ingredient_name": "Salt",
                        "requirement_mode": "generic_ingredient",
                        "family_id": "salt",
                        "requirement_type": "required",
                    },
                    {
                        "ingredient_name": "Ghee",
                        "requirement_mode": "generic_ingredient",
                        "family_id": "ghee",
                        "requirement_type": "optional",
                    },
                ],
            )
            # 1 missing (expired spinach counts as missing)
            await _make_dish(
                db,
                slug="bucket-1",
                name="Bucket 1",
                ingredients=[
                    {
                        "ingredient_name": "Spinach",
                        "requirement_mode": "generic_ingredient",
                        "family_id": "spinach",
                    }
                ],
            )
            # 3 missing (expired + zero + unconfirmed) -> almost_there
            await _make_dish(
                db,
                slug="bucket-3",
                name="Bucket 3",
                ingredients=[
                    {
                        "ingredient_name": "Spinach",
                        "requirement_mode": "generic_ingredient",
                        "family_id": "spinach",
                    },
                    {
                        "ingredient_name": "Garlic",
                        "requirement_mode": "generic_ingredient",
                        "family_id": "garlic",
                    },
                    {
                        "ingredient_name": "Ginger",
                        "requirement_mode": "generic_ingredient",
                        "family_id": "ginger",
                    },
                ],
            )
            # 4 missing -> explore_more
            await _make_dish(
                db,
                slug="bucket-4",
                name="Bucket 4",
                ingredients=[
                    {
                        "ingredient_name": "Spinach",
                        "requirement_mode": "generic_ingredient",
                        "family_id": "spinach",
                    },
                    {
                        "ingredient_name": "Garlic",
                        "requirement_mode": "generic_ingredient",
                        "family_id": "garlic",
                    },
                    {
                        "ingredient_name": "Ginger",
                        "requirement_mode": "generic_ingredient",
                        "family_id": "ginger",
                    },
                    {
                        "ingredient_name": "Paneer",
                        "requirement_mode": "generic_ingredient",
                        "family_id": "paneer",
                    },
                ],
            )
            await db.commit()

            resp = await match_dishes_for_household(db, hh.id)
            assert [d.slug for d in resp.can_make_now] == ["bucket-0"]
            assert [d.slug for d in resp.almost_there] == ["bucket-1", "bucket-3"]
            assert [d.slug for d in resp.explore_more] == ["bucket-4"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ranking_expiry_preferences_isolation_and_zero_mutations(tmp_path: Path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'ranking.db'}"
    await asyncio.to_thread(_run_upgrade_head, db_url)
    engine = build_async_engine(db_url)
    session_maker = build_session_maker(engine)

    try:
        async with session_maker() as db:
            hh_a = await _create_household(db, "rank_a")
            hh_b = await _create_household(db, "rank_b")

            db.add(
                HouseholdRecipePreference(
                    household_id=hh_a.id,
                    diet_type="vegetarian",
                    dietary_restrictions=[],
                )
            )

            spinach = await _ensure_canonical_ingredient(
                db, name="Spinach", variant_id="spinach__unspecified", family_id="spinach"
            )
            salt = await _ensure_canonical_ingredient(
                db, name="Salt", variant_id="salt__unspecified", family_id="salt"
            )
            chicken = await _ensure_canonical_ingredient(
                db, name="Chicken", variant_id="chicken__unspecified", family_id="chicken"
            )

            soon = datetime.now(UTC).date() + timedelta(days=2)
            later = datetime.now(UTC).date() + timedelta(days=20)

            await _add_pantry_stock(
                db,
                household_id=hh_a.id,
                canonical_ing=spinach,
                item_name="Spinach Expiring Soon",
                qty=Decimal("200"),
                unit="g",
                expires_on=soon,
            )
            await _add_pantry_stock(
                db,
                household_id=hh_a.id,
                canonical_ing=salt,
                item_name="Salt Long Shelf",
                qty=Decimal("500"),
                unit="g",
                expires_on=later,
            )
            await _add_pantry_stock(
                db,
                household_id=hh_a.id,
                canonical_ing=chicken,
                item_name="Chicken",
                qty=Decimal("500"),
                unit="g",
            )

            d_expiring = await _make_dish(
                db,
                slug="a-expiring-dish",
                name="Zesty Spinach",
                diet_tags=["vegetarian"],
                ingredients=[
                    {
                        "ingredient_name": "Spinach",
                        "requirement_mode": "generic_ingredient",
                        "family_id": "spinach",
                    }
                ],
            )
            d_normal = await _make_dish(
                db,
                slug="b-normal-dish",
                name="Salted Water",
                diet_tags=["vegetarian"],
                ingredients=[
                    {
                        "ingredient_name": "Salt",
                        "requirement_mode": "generic_ingredient",
                        "family_id": "salt",
                    }
                ],
            )
            d_hidden = await _make_dish(
                db,
                slug="c-hidden-dish",
                name="Hidden Salt",
                diet_tags=["vegetarian"],
                ingredients=[
                    {
                        "ingredient_name": "Salt",
                        "requirement_mode": "generic_ingredient",
                        "family_id": "salt",
                    }
                ],
            )
            d_nonveg = await _make_dish(
                db,
                slug="d-nonveg-dish",
                name="Chicken Curry",
                diet_tags=["non_vegetarian"],
                ingredients=[
                    {
                        "ingredient_name": "Chicken",
                        "requirement_mode": "generic_ingredient",
                        "family_id": "chicken",
                    }
                ],
            )
            d_liked_missing = await _make_dish(
                db,
                slug="e-liked-missing",
                name="Liked Missing Dish",
                diet_tags=["vegetarian"],
                ingredients=[
                    {
                        "ingredient_name": "Paneer",
                        "requirement_mode": "generic_ingredient",
                        "family_id": "paneer",
                    }
                ],
            )

            db.add(SavedDish(household_id=hh_a.id, dish_id=d_liked_missing.id))
            db.add(DishFeedback(household_id=hh_a.id, dish_id=d_liked_missing.id, action="like"))
            db.add(DishFeedback(household_id=hh_a.id, dish_id=d_hidden.id, action="hide"))
            await db.commit()

            lots_before = (
                await db.execute(select(func.count()).select_from(InventoryLot))
            ).scalar_one()
            ledger_before = (
                await db.execute(select(func.count()).select_from(StockLedger))
            ).scalar_one()

            resp_a = await match_dishes_for_household(db, hh_a.id)
            can_slugs = [d.slug for d in resp_a.can_make_now]
            # Expiring dish ranks ahead of non-expiring; hidden and non-veg are excluded
            assert can_slugs == [d_expiring.slug, d_normal.slug]
            assert d_nonveg.slug not in can_slugs
            assert d_hidden.slug not in can_slugs
            # Liked/saved missing dish stays in almost_there (never outranks can_make_now)
            assert [d.slug for d in resp_a.almost_there] == [d_liked_missing.slug]
            assert resp_a.almost_there[0].is_saved is True
            assert resp_a.almost_there[0].user_feedback_action == "like"

            # Household B has empty pantry -> nothing in can_make_now, no leaked saved/feedback
            resp_b = await match_dishes_for_household(db, hh_b.id)
            assert resp_b.can_make_now == []
            assert all(not d.is_saved for d in resp_b.almost_there)

            # Verify zero inventory/ledger mutations
            lots_after = (
                await db.execute(select(func.count()).select_from(InventoryLot))
            ).scalar_one()
            ledger_after = (
                await db.execute(select(func.count()).select_from(StockLedger))
            ).scalar_one()
            assert lots_after == lots_before
            assert ledger_after == ledger_before
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_generic_req_with_specific_variant_and_multi_lot_aggregation(
    tmp_path: Path,
) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'generic_and_multilot.db'}"
    await asyncio.to_thread(_run_upgrade_head, db_url)
    engine = build_async_engine(db_url)
    session_maker = build_session_maker(engine)

    try:
        async with session_maker() as db:
            hh = await _create_household(db, "multilot")
            red_onion = await _ensure_canonical_ingredient(
                db,
                name="Red Onion",
                variant_id="onion__red_onion",
                family_id="onion",
            )
            toor_dal = await _ensure_canonical_ingredient(
                db,
                name="Split Toor Dal",
                variant_id="toor_dal__split_toor_dal",
                family_id="toor_dal",
            )

            # Specific variant in pantry for generic onion requirement
            await _add_pantry_stock(
                db,
                household_id=hh.id,
                canonical_ing=red_onion,
                item_name="Nasik Red Onion",
                qty=Decimal("250"),
                unit="g",
            )

            # Two separate active lots (300 g + 300 g = 600 g) for the same CanonicalIngredient
            item_dal = CanonicalItem(
                household_id=hh.id,
                slug=f"toor-dal-pack-{hh.id}",
                name="Toor Dal Pack",
                category="pantry",
                default_base_unit="g",
            )
            db.add(item_dal)
            await db.flush()
            db.add(
                IngredientMapping(
                    household_id=hh.id,
                    canonical_item_id=item_dal.id,
                    canonical_ingredient_id=toor_dal.id,
                    mapping_source="user_confirmed",
                    matching_confidence=Decimal("1.000"),
                    user_confirmed=True,
                )
            )
            for _ in range(2):
                db.add(
                    InventoryLot(
                        household_id=hh.id,
                        canonical_item_id=item_dal.id,
                        initial_qty=Decimal("300"),
                        remaining_qty=Decimal("300"),
                        base_unit="g",
                        lot_status="active",
                        purchased_at=datetime.now(UTC),
                    )
                )

            await _make_dish(
                db,
                slug="generic-onion-specific-pantry",
                name="Generic Onion Dish",
                ingredients=[
                    {
                        "ingredient_name": "Onion",
                        "requirement_mode": "generic_ingredient",
                        "family_id": "onion",
                    }
                ],
            )
            await _make_dish(
                db,
                slug="multi-lot-dal-dish",
                name="Multi Lot Dal",
                ingredients=[
                    {
                        "ingredient_name": "Split Toor Dal",
                        "requirement_mode": "exact_variant",
                        "variant_id": "toor_dal__split_toor_dal",
                        "family_id": "toor_dal",
                        "quantity": Decimal("500"),
                        "unit": "g",
                    }
                ],
            )
            await db.commit()

            resp = await match_dishes_for_household(db, hh.id)
            by_slug = {d.slug: d for d in resp.can_make_now}
            assert set(by_slug.keys()) == {
                "generic-onion-specific-pantry",
                "multi-lot-dal-dish",
            }
            assert (
                by_slug["generic-onion-specific-pantry"].requirements[0].matched_variant_id
                == "onion__red_onion"
            )
            dal_req = by_slug["multi-lot-dal-dish"].requirements[0]
            assert dal_req.status == "available"
            assert dal_req.available_qty == Decimal("600.000")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_dietary_restrictions_preferences_dislike_and_include_hidden(
    tmp_path: Path,
) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'prefs_dislike_hidden.db'}"
    await asyncio.to_thread(_run_upgrade_head, db_url)
    engine = build_async_engine(db_url)
    session_maker = build_session_maker(engine)

    try:
        async with session_maker() as db:
            hh_a = await _create_household(db, "prefs_a")
            hh_b = await _create_household(db, "prefs_b")

            db.add(
                HouseholdRecipePreference(
                    household_id=hh_a.id,
                    diet_type="vegetarian",
                    dietary_restrictions=["dairy_free"],
                    preferred_cuisines=["South Indian"],
                    preferred_meal_types=["breakfast"],
                )
            )

            salt = await _ensure_canonical_ingredient(
                db, name="Salt", variant_id="salt__unspecified", family_id="salt"
            )
            await _add_pantry_stock(
                db,
                household_id=hh_a.id,
                canonical_ing=salt,
                item_name="Salt",
                qty=Decimal("500"),
                unit="g",
            )

            # Violates dairy_free restriction -> must be hard-excluded
            await _make_dish(
                db,
                slug="dairy-violating-dish",
                name="A Dairy Dish",
                diet_tags=["vegetarian"],
                cuisine="Indian",
                region="South Indian",
                meal_types=["breakfast"],
                ingredients=[
                    {
                        "ingredient_name": "Salt",
                        "requirement_mode": "generic_ingredient",
                        "family_id": "salt",
                    }
                ],
            )
            # Matches preferred_cuisines + preferred_meal_types + dairy_free
            d_pref = await _make_dish(
                db,
                slug="preferred-south-breakfast",
                name="Z South Breakfast",
                diet_tags=["vegetarian", "dairy_free"],
                cuisine="Indian",
                region="South Indian",
                meal_types=["breakfast"],
                ingredients=[
                    {
                        "ingredient_name": "Salt",
                        "requirement_mode": "generic_ingredient",
                        "family_id": "salt",
                    }
                ],
            )
            # Neutral dairy_free dish (alphabetically earlier, non-preferred cuisine/meal)
            d_neutral = await _make_dish(
                db,
                slug="neutral-dish",
                name="A Neutral Dish",
                diet_tags=["vegetarian", "dairy_free"],
                cuisine="Indian",
                region="North Indian",
                meal_types=["dinner"],
                ingredients=[
                    {
                        "ingredient_name": "Salt",
                        "requirement_mode": "generic_ingredient",
                        "family_id": "salt",
                    }
                ],
            )
            # Disliked dairy_free dish (otherwise equivalent to neutral, alphabetically earlier)
            d_disliked = await _make_dish(
                db,
                slug="disliked-dish",
                name="0 Disliked Dish",
                diet_tags=["vegetarian", "dairy_free"],
                cuisine="Indian",
                region="North Indian",
                meal_types=["dinner"],
                ingredients=[
                    {
                        "ingredient_name": "Salt",
                        "requirement_mode": "generic_ingredient",
                        "family_id": "salt",
                    }
                ],
            )
            # Hidden dairy_free dish
            d_hidden = await _make_dish(
                db,
                slug="hidden-dairy-free",
                name="B Hidden Dish",
                diet_tags=["vegetarian", "dairy_free"],
                cuisine="Indian",
                region="North Indian",
                meal_types=["dinner"],
                ingredients=[
                    {
                        "ingredient_name": "Salt",
                        "requirement_mode": "generic_ingredient",
                        "family_id": "salt",
                    }
                ],
            )

            db.add(DishFeedback(household_id=hh_a.id, dish_id=d_disliked.id, action="dislike"))
            db.add(DishFeedback(household_id=hh_a.id, dish_id=d_hidden.id, action="hide"))
            await db.commit()

            resp_default = await match_dishes_for_household(db, hh_a.id)
            slugs_default = [d.slug for d in resp_default.can_make_now]
            assert "dairy-violating-dish" not in slugs_default
            assert d_hidden.slug not in slugs_default
            # Preferred cuisine/meal ranks first; neutral ranks ahead of disliked
            assert slugs_default == [d_pref.slug, d_neutral.slug, d_disliked.slug]

            # include_hidden=True includes the hidden dish while still excluding dairy-violating
            resp_with_hidden = await match_dishes_for_household(
                db, hh_a.id, include_hidden=True
            )
            slugs_with_hidden = [d.slug for d in resp_with_hidden.can_make_now]
            assert "dairy-violating-dish" not in slugs_with_hidden
            assert d_hidden.slug in slugs_with_hidden

            # Household B has no restrictions/hide/dislike and empty pantry
            resp_b = await match_dishes_for_household(db, hh_b.id)
            assert resp_b.can_make_now == []
            b_slugs = {d.slug for d in resp_b.almost_there}
            assert "dairy-violating-dish" in b_slugs
            assert d_hidden.slug in b_slugs
    finally:
        await engine.dispose()
