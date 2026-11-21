"""Phase 7.2 tests: Household Ingredient Catalogue, IngredientResolver, brand deduplication, and household learning."""

from datetime import date
from decimal import Decimal
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import build_async_engine, build_session_maker
from app.modules.catalog.ingredient_catalogue import (
    CATALOGUE_JSON_PATH,
    get_ingredient_catalogue,
    normalize_ingredient_text,
)
from app.modules.catalog.ingredient_catalogue_sync import (
    record_household_learned_alias,
    sync_catalogue_to_db,
)
from app.modules.catalog.ingredient_mapping_service import (
    confirm_ingredient_mapping,
    resolve_or_suggest_mapping,
)
from app.modules.catalog.ingredient_resolver import IngredientResolver
from app.modules.inventory.ingredient_aggregation_service import (
    convert_to_target_unit,
    get_ingredient_inventory_summary,
)
from app.modules.inventory.models import CanonicalIngredient, CanonicalItem, IngredientAlias
from app.modules.recipes.grounding import match_inventory_item


def test_phase7_2_catalogue_loading_and_validation() -> None:
    assert CATALOGUE_JSON_PATH.exists()
    cat = get_ingredient_catalogue()
    assert cat.validation.valid is True
    assert cat.validation.errors == []
    assert cat.validation.family_count == 168
    assert cat.validation.variant_count == 393
    assert cat.validation.curated_alias_count == 851
    assert len(cat.families_by_id) == 168
    assert len(cat.variants_by_id) == 393
    assert cat.get_variant("milk__toned_milk") is not None
    assert cat.get_measurement_rules_for_variant("milk__toned_milk")["base_unit"] == "ml"
    assert any(v.variant_id == "rice__basmati_rice" for v in cat.lookup_by_alias("basmati chawal"))


def test_phase7_2_ingredient_resolver_priority_and_boundary_matching() -> None:
    resolver = IngredientResolver()

    cases = [
        ("Aloo", "potato__unspecified", "Potato"),
        ("Nariyal", "coconut__unspecified", "Coconut"),
        ("Arhar Dal", "toor_dal__unspecified", "Toor Dal"),
        ("Full Cream Milk", "milk__full_cream_milk", "Full Cream Milk"),
        ("Amul Taaza Toned Milk", "milk__toned_milk", "Toned Milk"),
        ("Mother Dairy Toned Milk", "milk__toned_milk", "Toned Milk"),
        ("Cherry Tomatoes", "tomato__cherry_tomato", "Cherry Tomato"),
        ("Fresh Desi Tomato", "tomato__desi_tomato", "Desi Tomato"),
        ("Generic Milk", "milk__unspecified", "Milk"),
        ("Generic Tomato", "tomato__unspecified", "Tomato"),
        ("Coconut Milk", "coconut_milk__unspecified", "Coconut Milk"),
        ("Coconut Oil", "edible_oil__coconut_oil", "Coconut Oil"),
        ("Dark Chocolate", "chocolate__dark_chocolate", "Dark Chocolate"),
        ("Basmati Chawal", "rice__basmati_rice", "Basmati Rice"),
    ]
    for text, expected_vid, expected_name in cases:
        res = resolver.resolve_text(text)
        assert res.requires_review is False, f"{text} unexpectedly required review: {res.reason}"
        assert res.variant_id == expected_vid, f"{text}: got {res.variant_id}, expected {expected_vid}"
        assert res.variant_name == expected_name

    # Brand name alone must NOT determine an ingredient
    brand_only = resolver.resolve_text("Amul")
    assert brand_only.requires_review is True
    assert brand_only.variant_id is None

    # Fuzzy-only match must suggest, never auto-assign
    fuzzy = resolver.resolve_text("Tomatosss")
    assert fuzzy.requires_review is True
    assert fuzzy.variant_id is None


def test_phase7_2_measurement_dimensions() -> None:
    assert convert_to_target_unit(Decimal("1"), "kg", "g")[0] == Decimal("1000.000")
    assert convert_to_target_unit(Decimal("1"), "L", "ml")[0] == Decimal("1000.000")
    assert convert_to_target_unit(Decimal("1"), "dozen", "pcs")[0] == Decimal("12.000")
    assert convert_to_target_unit(Decimal("1"), "m", "cm")[0] == Decimal("100.000")
    assert convert_to_target_unit(Decimal("1"), "bottle", "L")[0] is None
    assert convert_to_target_unit(Decimal("1"), "pack", "g")[0] is None
    assert convert_to_target_unit(Decimal("1"), "pcs", "g")[0] is None


def test_phase7_2_recipe_family_and_variant_matching() -> None:
    pantry = [
        {
            "canonical_item_id": str(uuid.uuid4()),
            "family_id": "milk",
            "variant_id": "milk__toned_milk",
            "is_unspecified_variant": False,
            "name": "Toned Milk",
            "qty": 1000.0,
            "base_unit": "ml",
        },
        {
            "canonical_item_id": str(uuid.uuid4()),
            "family_id": "tomato",
            "variant_id": "tomato__desi_tomato",
            "is_unspecified_variant": False,
            "name": "Desi Tomato",
            "qty": 500.0,
            "base_unit": "g",
        },
    ]

    # Generic Milk in recipe satisfied by Toned Milk in pantry
    assert match_inventory_item("Milk", pantry) is not None
    # Specific Cherry Tomato NOT silently satisfied by Desi Tomato
    assert match_inventory_item("Cherry Tomato", pantry) is None
    # Exact Desi Tomato satisfied
    assert match_inventory_item("Desi Tomato", pantry) is not None


@pytest.mark.asyncio
async def test_phase7_2_brand_dedup_idempotency_and_household_learning(
    client: AsyncClient,
    jwks_provider,
    temp_db_url: str,
) -> None:
    sub_a = f"user-a-{uuid.uuid4().hex[:8]}"
    sub_b = f"user-b-{uuid.uuid4().hex[:8]}"
    headers_a = {"Authorization": f"Bearer {jwks_provider.make_token(sub=sub_a, email=f'{sub_a}@example.com')}"}
    headers_b = {"Authorization": f"Bearer {jwks_provider.make_token(sub=sub_b, email=f'{sub_b}@example.com')}"}

    me_a = await client.get("/api/v1/auth/me", headers=headers_a)
    me_b = await client.get("/api/v1/auth/me", headers=headers_b)
    hh_a = uuid.UUID(me_a.json()["active_household"]["id"])
    hh_b = uuid.UUID(me_b.json()["active_household"]["id"])

    engine = build_async_engine(temp_db_url)
    sm = build_session_maker(engine)

    async with sm() as db:  # type: AsyncSession
        s1 = await sync_catalogue_to_db(db)
        s2 = await sync_catalogue_to_db(db)
        assert s2["created"] == 0  # Idempotent

        # Create 2 brands of Toned Milk + 1 Full Cream Milk
        item_amul = CanonicalItem(household_id=hh_a, slug=f"amul-toned-{uuid.uuid4().hex[:6]}", name="Amul Taaza Toned Milk", category="dairy", default_base_unit="ml")
        item_mother = CanonicalItem(household_id=hh_a, slug=f"md-toned-{uuid.uuid4().hex[:6]}", name="Mother Dairy Toned Milk", category="dairy", default_base_unit="ml")
        item_fc = CanonicalItem(household_id=hh_a, slug=f"amul-fc-{uuid.uuid4().hex[:6]}", name="Amul Gold Full Cream Milk", category="dairy", default_base_unit="ml")
        item_custom = CanonicalItem(household_id=hh_a, slug=f"custom-xyz-{uuid.uuid4().hex[:6]}", name="Ghar Ka Special Masala X99", category="spices_seasonings", default_base_unit="g")
        db.add_all([item_amul, item_mother, item_fc, item_custom])
        await db.flush()

        res_amul = await resolve_or_suggest_mapping(db, household_id=hh_a, canonical_item=item_amul)
        res_mother = await resolve_or_suggest_mapping(db, household_id=hh_a, canonical_item=item_mother)
        res_fc = await resolve_or_suggest_mapping(db, household_id=hh_a, canonical_item=item_fc)
        res_custom_unconf = await resolve_or_suggest_mapping(db, household_id=hh_a, canonical_item=item_custom)

        assert res_amul.suggested_ingredient is not None and res_mother.suggested_ingredient is not None
        assert res_amul.suggested_ingredient.id == res_mother.suggested_ingredient.id
        assert res_amul.suggested_ingredient.variant_id == "milk__toned_milk"
        assert res_fc.suggested_ingredient is not None
        assert res_fc.suggested_ingredient.variant_id == "milk__full_cream_milk"
        assert res_fc.suggested_ingredient.id != res_amul.suggested_ingredient.id

        # Unconfirmed/unknown item must NOT be learned
        assert res_custom_unconf.status in ("unmapped", "suggested")
        learned_before = (
            await db.execute(
                select(IngredientAlias).where(
                    IngredientAlias.household_id == hh_a,
                    IngredientAlias.normalized_alias == normalize_ingredient_text("Ghar Ka Special Masala X99"),
                )
            )
        ).scalar_one_or_none()
        assert learned_before is None

        # Explicit user confirmation learns the alias for Household A ONLY
        garam = (await db.execute(select(CanonicalIngredient).where(CanonicalIngredient.variant_id == "garam_masala__unspecified"))).scalar_one()
        await confirm_ingredient_mapping(
            db,
            household_id=hh_a,
            canonical_item_id=item_custom.id,
            canonical_ingredient_id=garam.id,
        )
        # Repeated confirmation updates count without duplicating
        await record_household_learned_alias(
            db,
            household_id=hh_a,
            phrase="Ghar Ka Special Masala X99",
            canonical_ingredient=garam,
        )
        await db.commit()

        aliases_a = (
            await db.execute(
                select(IngredientAlias).where(
                    IngredientAlias.household_id == hh_a,
                    IngredientAlias.normalized_alias == normalize_ingredient_text("Ghar Ka Special Masala X99"),
                )
            )
        ).scalars().all()
        assert len(aliases_a) == 1
        assert aliases_a[0].confirmation_count == 2
        assert aliases_a[0].target_variant_id == "garam_masala__unspecified"

        # Subsequent receipt in Household A reuses learned alias; Household B does NOT
        resolver = IngredientResolver()
        reuse_a = await resolver.resolve_for_household(db, household_id=hh_a, receipt_text="Ghar Ka Special Masala X99")
        reuse_b = await resolver.resolve_for_household(db, household_id=hh_b, receipt_text="Ghar Ka Special Masala X99")
        assert reuse_a.variant_id == "garam_masala__unspecified"
        assert reuse_a.match_source == "household_learned_alias"
        assert reuse_b.requires_review is True

    await engine.dispose()
