"""Data-driven, idempotent seeder for CanonicalIngredient taxonomy."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.ingredient_identity_service import normalize_ingredient_name_key
from app.modules.inventory.models import CanonicalIngredient, IngredientAlias

DEFAULT_SEED_PATH = Path(__file__).resolve().parent / "seed_ingredients.json"


def normalize_ingredient_name(value: str) -> str:
    """Normalize an ingredient name to snake_case for deduplication."""
    cleaned = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return cleaned


def load_ingredient_seed_records(seed_path: Path | None = None) -> list[dict[str, Any]]:
    """Load ingredient seed records from a JSON file."""
    path = seed_path or DEFAULT_SEED_PATH
    with path.open("r", encoding="utf-8") as fh:
        records = json.load(fh)
    if not isinstance(records, list):
        raise ValueError(f"Ingredient seed file must contain a JSON array: {path}")
    return records


async def seed_canonical_ingredients(
    session: AsyncSession,
    seed_path: Path | None = None,
) -> int:
    """Idempotently insert missing CanonicalIngredient rows from JSON.

    Returns the number of newly inserted ingredients. Existing rows are preserved.
    """
    records = load_ingredient_seed_records(seed_path)
    existing_rows = (
        await session.execute(select(CanonicalIngredient.name, CanonicalIngredient.normalized_name))
    ).all()
    existing_names = {norm for _, norm in existing_rows}
    existing_keys = {
        normalize_ingredient_name_key(val) for row in existing_rows for val in row if val
    }
    alias_rows = (await session.execute(select(IngredientAlias.normalized_alias))).scalars().all()
    existing_keys.update(normalize_ingredient_name_key(a) for a in alias_rows if a)

    inserted = 0
    for rec in records:
        norm = rec.get("normalized_name") or normalize_ingredient_name(rec["name"])
        key = normalize_ingredient_name_key(rec["name"])
        norm_key = normalize_ingredient_name_key(norm)
        if norm in existing_names or key in existing_keys or norm_key in existing_keys:
            continue
        ingredient = CanonicalIngredient(
            name=rec["name"],
            normalized_name=norm,
            category=rec["category"],
            subcategory=rec.get("subcategory"),
            standard_unit=rec["standard_unit"],
            cooking_variants=list(rec.get("cooking_variants") or []),
            supported_units=list(rec.get("supported_units") or [rec["standard_unit"]]),
        )
        session.add(ingredient)
        existing_names.add(norm)
        existing_keys.add(key)
        existing_keys.add(norm_key)
        inserted += 1

    if inserted:
        await session.flush()
    return inserted
