"""Idempotent catalogue-to-DB sync and household learning persistence (Phase 7.2)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.ingredient_catalogue import (
    CatalogueVariant,
    get_ingredient_catalogue,
    normalize_ingredient_text,
)
from app.modules.inventory.models import CanonicalIngredient, IngredientAlias


def _db_norm_name(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")


def _safe_standard_unit(variant: CatalogueVariant) -> str:
    bu = str((variant.measurement or {}).get("base_unit") or "pcs").lower()
    if bu in ("g", "ml", "pcs"):
        return bu
    dim = str((variant.measurement or {}).get("default_dimension") or "").lower()
    if dim == "mass":
        return "g"
    if dim == "volume":
        return "ml"
    return "pcs"


async def sync_catalogue_to_db(db: AsyncSession) -> dict[str, int]:
    """Idempotently ensure all curated catalogue variants exist in `canonical_ingredients`."""
    cat = get_ingredient_catalogue()
    existing = list((await db.execute(select(CanonicalIngredient))).scalars().all())
    by_var_id: dict[str, CanonicalIngredient] = {c.variant_id: c for c in existing if c.variant_id}
    by_norm: dict[str, CanonicalIngredient] = {
        c.normalized_name: c for c in existing if c.normalized_name
    }

    reused = 0
    created = 0
    for variant in cat.variants_by_id.values():
        row = by_var_id.get(variant.variant_id)
        if row is None:
            cand_norm = _db_norm_name(variant.canonical_name)
            row = by_norm.get(cand_norm)
        if row is not None:
            changed = False
            if row.variant_id is None:
                row.variant_id = variant.variant_id
                row.family_id = variant.family_id
                row.family_name = variant.family_name
                row.variant_status = variant.variant_status
                row.is_unspecified_variant = variant.is_unspecified_variant
                row.attributes_json = dict(variant.attributes)
                row.name = variant.canonical_name
                changed = True
            if changed:
                by_var_id[variant.variant_id] = row
            reused += 1
            continue

        norm_key = _db_norm_name(variant.canonical_name)
        if norm_key in by_norm:
            norm_key = f"{norm_key}__{variant.variant_id.replace('__', '_')}"

        supported = list((variant.measurement or {}).get("supported_units") or [])
        new_row = CanonicalIngredient(
            family_id=variant.family_id,
            family_name=variant.family_name,
            variant_id=variant.variant_id,
            variant_status=variant.variant_status,
            is_unspecified_variant=variant.is_unspecified_variant,
            attributes_json=dict(variant.attributes),
            name=variant.canonical_name,
            normalized_name=norm_key,
            category=variant.category,
            standard_unit=_safe_standard_unit(variant),
            supported_units=supported,
            is_active=True,
        )
        db.add(new_row)
        by_var_id[variant.variant_id] = new_row
        by_norm[norm_key] = new_row
        created += 1

    await db.flush()
    return {"reused": reused, "created": created, "total_variants": len(cat.variants_by_id)}


async def record_household_learned_alias(
    db: AsyncSession,
    *,
    household_id: UUID,
    phrase: str,
    canonical_ingredient: CanonicalIngredient,
    source: str = "user_correction",
    provenance: str = "explicit_user_confirmation",
) -> IngredientAlias | None:
    """Persist a household-scoped learned alias only from explicit user confirmation.

    Never overwrites curated global aliases (`household_id IS NULL`).
    Idempotent under repeated or concurrent confirmations for the same
    (household_id, normalized_alias).
    """
    norm_phrase = normalize_ingredient_text(phrase)
    if not norm_phrase:
        return None

    existing = (
        await db.execute(
            select(IngredientAlias).where(
                IngredientAlias.household_id == household_id,
                IngredientAlias.normalized_alias == norm_phrase,
            )
        )
    ).scalar_one_or_none()

    now = datetime.now(UTC)
    if existing is not None:
        existing.canonical_ingredient_id = canonical_ingredient.id
        existing.target_family_id = canonical_ingredient.family_id
        existing.target_variant_id = canonical_ingredient.variant_id
        existing.confirmed_at = now
        existing.confirmation_count = int(existing.confirmation_count or 1) + 1
        existing.is_approved = True
        existing.provenance = provenance
        await db.flush()
        return existing

    alias_row = IngredientAlias(
        household_id=household_id,
        canonical_ingredient_id=canonical_ingredient.id,
        alias=phrase.strip(),
        original_phrase=phrase.strip(),
        normalized_alias=norm_phrase,
        language="en",
        alias_type="user_correction",
        source=source,
        confidence=Decimal("1.000"),
        is_approved=True,
        target_family_id=canonical_ingredient.family_id,
        target_variant_id=canonical_ingredient.variant_id,
        confirmed_at=now,
        confirmation_count=1,
        provenance=provenance,
    )
    db.add(alias_row)
    await db.flush()
    return alias_row
