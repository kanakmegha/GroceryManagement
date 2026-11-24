from __future__ import annotations

import re
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.inventory.models import CanonicalIngredient, IngredientAlias, IngredientMapping
from app.modules.recipes.context_engine import classify_season_context
from app.modules.recipes.models import HouseholdRecipePreference
from app.modules.recipes.preferences import compute_household_preference_profile
from app.modules.recipes.service import (
    _load_active_inventory_payload,
    _load_recent_cooking_history,
)


async def build_daily_household_context(
    db: AsyncSession,
    household_id: UUID,
    *,
    local_date: date,
    timezone_str: str,
    weather_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a consistent, privacy-safe household context for daily meal generation.

    Uses only confirmed active inventory lots, explicit household preferences,
    computed cooking history/feedback signals, and optional weather metrics.
    """
    inventory_payload = await _load_active_inventory_payload(db, household_id)

    # Enrich inventory payload with confirmed mappings & approved aliases (read-only)
    confirmed_by_item: dict[str, tuple[UUID, str]] = {}
    map_rows = (
        await db.execute(
            select(
                IngredientMapping.canonical_item_id,
                CanonicalIngredient.id,
                CanonicalIngredient.normalized_name,
            )
            .join(
                CanonicalIngredient,
                CanonicalIngredient.id == IngredientMapping.canonical_ingredient_id,
            )
            .where(
                IngredientMapping.household_id == household_id,
                IngredientMapping.user_confirmed.is_(True),
            )
        )
    ).all()
    for ci_id, cing_id, cing_norm in map_rows:
        confirmed_by_item[str(ci_id)] = (cing_id, cing_norm)

    alias_rows = (
        await db.execute(
            select(
                IngredientAlias.normalized_alias,
                CanonicalIngredient.id,
                CanonicalIngredient.normalized_name,
            )
            .join(
                CanonicalIngredient,
                CanonicalIngredient.id == IngredientAlias.canonical_ingredient_id,
            )
            .where(
                IngredientAlias.is_approved.is_(True),
                or_(
                    IngredientAlias.household_id.is_(None),
                    IngredientAlias.household_id == household_id,
                ),
            )
        )
    ).all()
    alias_to_cing: dict[str, tuple[UUID, str]] = {}
    aliases_by_cing: dict[UUID, list[str]] = {}
    for norm_alias, cing_id, cing_norm in alias_rows:
        if norm_alias:
            alias_to_cing[norm_alias.lower()] = (cing_id, cing_norm)
            aliases_by_cing.setdefault(cing_id, []).append(norm_alias.lower())

    for item in inventory_payload:
        ci_id_str = str(item.get("canonical_item_id") or "")
        matched_cing = confirmed_by_item.get(ci_id_str)
        if matched_cing is None:
            norm_item = re.sub(
                r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", str(item.get("name") or "").lower())
            ).strip()
            matched_cing = alias_to_cing.get(norm_item)
            if matched_cing is None:
                for tok in norm_item.split():
                    if len(tok) >= 3 and tok in alias_to_cing:
                        matched_cing = alias_to_cing[tok]
                        break
        if matched_cing is not None:
            cing_id, cing_norm = matched_cing
            item["canonical_ingredient_name"] = cing_norm
            item["confirmed_aliases"] = sorted(
                set(aliases_by_cing.get(cing_id, []) + [cing_norm])
            )
    pref_row = (
        await db.execute(
            select(HouseholdRecipePreference).where(
                HouseholdRecipePreference.household_id == household_id
            )
        )
    ).scalar_one_or_none()
    pref_profile = await compute_household_preference_profile(db, household_id)
    recent_recipes = await _load_recent_cooking_history(db, household_id)

    temp_c = weather_snapshot.get("temperature_c") if weather_snapshot else None
    rain_mm = weather_snapshot.get("rainfall_mm") if weather_snapshot else None
    season_ctx = classify_season_context(
        local_date,
        temperature_c=temp_c,
        rainfall_mm=rain_mm,
    )

    sanitized_inventory = [
        {
            "name": item.get("name"),
            "quantity": item.get("quantity"),
            "unit": item.get("unit"),
            "expiring_soon": bool(item.get("expiring_soon")),
            "days_until_expiry": item.get("days_until_expiry"),
        }
        for item in inventory_payload
        if item.get("name")
    ]

    explicit_preferences = {
        "diet_type": pref_row.diet_type if pref_row else "any",
        "dietary_restrictions": list(pref_row.dietary_restrictions or []) if pref_row else [],
        "preferred_cuisines": list(pref_row.preferred_cuisines or []) if pref_row else [],
        "preferred_meal_types": list(pref_row.preferred_meal_types or []) if pref_row else [],
        "max_prep_minutes": pref_row.max_prep_minutes if pref_row else None,
    }

    sanitized_profile = {
        "favourite_dishes": [
            f.get("title")
            for f in (pref_profile.get("favourite_dishes") or [])
            if isinstance(f, dict) and f.get("title")
        ],
        "disliked_dishes": list(pref_profile.get("disliked_dishes") or []),
        "disliked_ingredients": list(pref_profile.get("disliked_ingredients") or []),
        "want_to_cook_again": list(pref_profile.get("want_to_cook_again") or []),
        "taste_and_time_signals": list(pref_profile.get("taste_and_time_signals") or []),
    }

    resolved_weather = weather_snapshot or {
        "season": season_ctx.season,
        "weather_used": season_ctx.weather_used,
        "summary": season_ctx.weather_summary,
    }

    return {
        "context_version": "1.0",
        "local_date": local_date.isoformat(),
        "timezone": timezone_str,
        "inventory_items": sanitized_inventory,
        "raw_inventory_payload": inventory_payload,
        "explicit_preferences": explicit_preferences,
        "preference_profile": sanitized_profile,
        "recent_recipes": recent_recipes,
        "weather": resolved_weather,
    }
