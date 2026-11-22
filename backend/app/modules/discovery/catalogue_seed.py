from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.ingredient_catalogue import (
    IngredientCatalogue,
    get_ingredient_catalogue,
)
from app.modules.discovery.catalogue_data import CURATED_DISH_CATALOGUE
from app.modules.discovery.models import Dish, DishIngredient
from app.modules.discovery.schemas import (
    CatalogueSeedResult,
    DishCatalogueEntry,
    DishIngredientSpec,
    DishSubstitutionRule,
)
from app.modules.inventory.models import CanonicalIngredient


class CatalogueValidationError(ValueError):
    """Raised when a dish catalogue entry fails IngredientCatalogue validation."""


def is_verified_substitution(rule: dict[str, Any] | DishSubstitutionRule) -> bool:
    """Return True only when a substitution rule is explicitly verified."""
    if isinstance(rule, DishSubstitutionRule):
        return bool(rule.verified)
    return bool(rule.get("verified") is True)


def validate_dish_catalogue_entry(
    entry: DishCatalogueEntry,
    catalogue: IngredientCatalogue,
) -> None:
    """Validate all ingredient requirements and substitutions against IngredientCatalogue."""
    for ing in entry.ingredients:
        _validate_ingredient_spec(entry.slug, ing, catalogue)


def _validate_ingredient_spec(
    dish_slug: str,
    ing: DishIngredientSpec,
    catalogue: IngredientCatalogue,
) -> None:
    if ing.requirement_mode == "exact_variant":
        if not ing.variant_id:
            raise CatalogueValidationError(f"{dish_slug}: exact_variant requires variant_id.")
        variant = catalogue.get_variant(ing.variant_id)
        if variant is None:
            raise CatalogueValidationError(
                f"{dish_slug}: unknown variant_id '{ing.variant_id}'."
            )
        if variant.is_unspecified_variant:
            raise CatalogueValidationError(
                f"{dish_slug}: exact_variant cannot reference unspecified variant "
                f"'{ing.variant_id}'."
            )
        if ing.family_id and variant.family_id != ing.family_id:
            raise CatalogueValidationError(
                f"{dish_slug}: variant_id '{ing.variant_id}' belongs to family "
                f"'{variant.family_id}', not '{ing.family_id}'."
            )

    elif ing.requirement_mode == "compatible_family":
        if not ing.family_id or catalogue.get_family(ing.family_id) is None:
            raise CatalogueValidationError(
                f"{dish_slug}: unknown family_id '{ing.family_id}'."
            )
        for compat_vid in ing.compatible_variant_ids:
            compat_var = catalogue.get_variant(compat_vid)
            if compat_var is None or compat_var.family_id != ing.family_id:
                raise CatalogueValidationError(
                    f"{dish_slug}: compatible_variant_id '{compat_vid}' is invalid for "
                    f"family '{ing.family_id}'."
                )

    elif ing.requirement_mode == "generic_ingredient":
        if ing.variant_id is not None:
            variant = catalogue.get_variant(ing.variant_id)
            if variant is None:
                raise CatalogueValidationError(
                    f"{dish_slug}: unknown variant_id '{ing.variant_id}'."
                )
            if not variant.is_unspecified_variant:
                raise CatalogueValidationError(
                    f"{dish_slug}: generic_ingredient cannot require specific variant "
                    f"'{ing.variant_id}'."
                )
        if ing.family_id is not None and catalogue.get_family(ing.family_id) is None:
            raise CatalogueValidationError(
                f"{dish_slug}: unknown family_id '{ing.family_id}'."
            )

    for sub in ing.substitutions:
        if sub.applicable_dish_slug is not None and sub.applicable_dish_slug != dish_slug:
            raise CatalogueValidationError(
                f"{dish_slug}: substitution applicable_dish_slug '{sub.applicable_dish_slug}' "
                f"does not match '{dish_slug}'."
            )
        if sub.original_variant_id is not None:
            orig_var = catalogue.get_variant(sub.original_variant_id)
            if orig_var is None:
                raise CatalogueValidationError(
                    f"{dish_slug}: unknown original_variant_id '{sub.original_variant_id}'."
                )
        if sub.substitute_variant_id is not None:
            sub_var = catalogue.get_variant(sub.substitute_variant_id)
            if sub_var is None:
                raise CatalogueValidationError(
                    f"{dish_slug}: unknown substitute_variant_id '{sub.substitute_variant_id}'."
                )
        if sub.substitute_family_id is not None and (
            catalogue.get_family(sub.substitute_family_id) is None
        ):
            raise CatalogueValidationError(
                f"{dish_slug}: unknown substitute_family_id '{sub.substitute_family_id}'."
            )


def compute_dish_content_hash(entry: DishCatalogueEntry) -> str:
    """Compute a deterministic SHA-256 hash of a DishCatalogueEntry and its ingredients."""
    sorted_ingredients = sorted(
        entry.ingredients,
        key=lambda i: (i.sort_order, i.ingredient_name, i.requirement_mode),
    )
    payload = {
        "slug": entry.slug,
        "canonical_name": entry.canonical_name,
        "alternative_names": list(entry.alternative_names),
        "regional_names": dict(sorted(entry.regional_names.items())),
        "cuisine": entry.cuisine,
        "region": entry.region,
        "meal_types": list(entry.meal_types),
        "diet_tags": list(entry.diet_tags),
        "description": entry.description,
        "prep_minutes": entry.prep_minutes,
        "cook_minutes": entry.cook_minutes,
        "default_servings": entry.default_servings,
        "search_terms": list(entry.search_terms),
        "catalogue_version": entry.catalogue_version,
        "ingredients": [
            {
                "ingredient_name": ing.ingredient_name,
                "requirement_mode": ing.requirement_mode,
                "requirement_type": ing.requirement_type,
                "variant_id": ing.variant_id,
                "family_id": ing.family_id,
                "compatible_variant_ids": list(ing.compatible_variant_ids),
                "quantity": str(ing.quantity) if ing.quantity is not None else None,
                "unit": ing.unit,
                "substitutions": [sub.model_dump(mode="json") for sub in ing.substitutions],
                "sort_order": ing.sort_order,
            }
            for ing in sorted_ingredients
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def _load_canonical_variant_map(db: AsyncSession) -> dict[str, UUID]:
    rows = (
        await db.execute(
            select(CanonicalIngredient.variant_id, CanonicalIngredient.id).where(
                CanonicalIngredient.variant_id.is_not(None)
            )
        )
    ).all()
    return {str(variant_id): row_id for variant_id, row_id in rows if variant_id is not None}


def _resolve_canonical_id(
    ing: DishIngredientSpec,
    canonical_by_variant: dict[str, UUID],
) -> UUID | None:
    if ing.variant_id and ing.variant_id in canonical_by_variant:
        return canonical_by_variant[ing.variant_id]
    if ing.family_id:
        unspecified = f"{ing.family_id}__unspecified"
        return canonical_by_variant.get(unspecified)
    return None


async def seed_dish_catalogue(
    db: AsyncSession,
    entries: Sequence[DishCatalogueEntry] | None = None,
    catalogue: IngredientCatalogue | None = None,
) -> CatalogueSeedResult:
    """Validate and idempotently seed the dish catalogue in a single transaction."""
    target_entries = list(entries if entries is not None else CURATED_DISH_CATALOGUE)
    cat = catalogue or get_ingredient_catalogue()

    seen_slugs: set[str] = set()
    for entry in target_entries:
        if entry.slug in seen_slugs:
            raise CatalogueValidationError(f"Duplicate dish slug '{entry.slug}' in catalogue.")
        seen_slugs.add(entry.slug)
        validate_dish_catalogue_entry(entry, cat)

    async def _apply() -> CatalogueSeedResult:
        canonical_by_variant = await _load_canonical_variant_map(db)
        existing_dishes = {
            d.slug: d for d in (await db.execute(select(Dish))).scalars().all()
        }

        inserted = 0
        updated = 0
        unchanged = 0
        deactivated = 0

        for entry in target_entries:
            content_hash = compute_dish_content_hash(entry)
            existing = existing_dishes.get(entry.slug)

            if (
                existing is not None
                and existing.content_hash == content_hash
                and existing.is_active
            ):
                unchanged += 1
                continue

            if existing is None:
                dish = Dish(
                    slug=entry.slug,
                    canonical_name=entry.canonical_name,
                    alternative_names=list(entry.alternative_names),
                    regional_names=dict(entry.regional_names),
                    cuisine=entry.cuisine,
                    region=entry.region,
                    meal_types=list(entry.meal_types),
                    diet_tags=list(entry.diet_tags),
                    description=entry.description,
                    prep_minutes=entry.prep_minutes,
                    cook_minutes=entry.cook_minutes,
                    default_servings=entry.default_servings,
                    search_terms=list(entry.search_terms),
                    catalogue_version=entry.catalogue_version,
                    content_hash=content_hash,
                    is_active=True,
                )
                db.add(dish)
                await db.flush()
                inserted += 1
            else:
                dish = existing
                dish.canonical_name = entry.canonical_name
                dish.alternative_names = list(entry.alternative_names)
                dish.regional_names = dict(entry.regional_names)
                dish.cuisine = entry.cuisine
                dish.region = entry.region
                dish.meal_types = list(entry.meal_types)
                dish.diet_tags = list(entry.diet_tags)
                dish.description = entry.description
                dish.prep_minutes = entry.prep_minutes
                dish.cook_minutes = entry.cook_minutes
                dish.default_servings = entry.default_servings
                dish.search_terms = list(entry.search_terms)
                dish.catalogue_version = entry.catalogue_version
                dish.content_hash = content_hash
                dish.is_active = True
                await db.execute(
                    delete(DishIngredient).where(DishIngredient.dish_id == dish.id)
                )
                await db.flush()
                updated += 1

            for ing in entry.ingredients:
                db.add(
                    DishIngredient(
                        dish_id=dish.id,
                        canonical_ingredient_id=_resolve_canonical_id(ing, canonical_by_variant),
                        ingredient_name=ing.ingredient_name,
                        requirement_mode=ing.requirement_mode,
                        requirement_type=ing.requirement_type,
                        variant_id=ing.variant_id,
                        family_id=ing.family_id,
                        compatible_variant_ids=list(ing.compatible_variant_ids),
                        quantity=ing.quantity,
                        unit=ing.unit,
                        substitutions_json=[
                            sub.model_dump(mode="json") for sub in ing.substitutions
                        ],
                        sort_order=ing.sort_order,
                    )
                )

        for slug, existing in existing_dishes.items():
            if slug not in seen_slugs and existing.is_active:
                existing.is_active = False
                deactivated += 1

        await db.flush()
        return CatalogueSeedResult(
            inserted=inserted,
            updated=updated,
            unchanged=unchanged,
            deactivated=deactivated,
        )

    if db.in_transaction():
        async with db.begin_nested():
            return await _apply()
    async with db.begin():
        return await _apply()
