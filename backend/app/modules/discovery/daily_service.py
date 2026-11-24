from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.discovery.daily_generator import (
    DAILY_CATEGORIES,
    compute_recipe_availability,
)
from app.modules.discovery.daily_models import DailyMealPlan, DailyMealPlanRecipe
from app.modules.discovery.daily_scheduler import (
    MANUAL_REGENERATION_DAILY_LIMIT,
    compute_household_local_now,
)
from app.modules.discovery.daily_schemas import (
    DailyMealCategory,
    DailyMealPlanRecipeOut,
    DailyMealPlanResponse,
)
from app.modules.recipes.models import HouseholdRecipePreference
from app.modules.recipes.schemas import RecipeChatIngredientOut, RecipeStepOut
from app.modules.recipes.service import _load_active_inventory_payload


def get_active_meal_category(local_dt: datetime) -> DailyMealCategory:
    """Derive active meal category from household-local hour:
    Breakfast 03:00-10:59, Lunch 11:00-14:59, Snacks 15:00-17:59, Dinner 18:00-02:59.
    """
    hr = local_dt.hour
    if 3 <= hr <= 10:
        return "breakfast"
    if 11 <= hr <= 14:
        return "lunch"
    if 15 <= hr <= 17:
        return "snacks"
    return "dinner"


def _to_recipe_out(
    row: DailyMealPlanRecipe,
    *,
    avail_override: dict[str, Any] | None = None,
) -> DailyMealPlanRecipeOut:
    rec = row.recipe_data or {}
    avail = avail_override or row.availability_data or {}

    def _to_ing(d: dict[str, Any]) -> RecipeChatIngredientOut:
        cid = d.get("canonical_item_id")
        return RecipeChatIngredientOut(
            name=str(d.get("name") or ""),
            canonical_item_id=UUID(str(cid)) if cid else None,
            quantity=str(d.get("quantity") or ""),
            base_unit=str(d.get("base_unit") or "pcs"),
            essential=bool(d.get("essential", True)),
            status=str(d.get("status") or "missing"),
            available=bool(d.get("available", False)),
            available_qty=d.get("available_qty"),
            missing_qty=d.get("missing_qty"),
        )

    raw_steps = rec.get("structured_steps") or []
    structured_steps = [
        RecipeStepOut(
            step_number=int(st.get("step_number") or idx),
            title=str(st.get("title") or f"Step {idx}"),
            instruction=str(st.get("instruction") or ""),
            ingredients_used=[str(x) for x in (st.get("ingredients_used") or [])],
            ingredient_quantities={
                str(k): str(v) for k, v in (st.get("ingredient_quantities") or {}).items()
            },
            duration_minutes=st.get("duration_minutes"),
            heat_level=st.get("heat_level"),
            technique=st.get("technique"),
            equipment=[str(x) for x in (st.get("equipment") or [])],
            completion_cue=str(st.get("completion_cue") or ""),
            caution=st.get("caution"),
            next_action=st.get("next_action"),
        )
        for idx, st in enumerate(raw_steps, start=1)
        if isinstance(st, dict)
    ]

    return DailyMealPlanRecipeOut(
        id=row.id,
        meal_category=row.meal_category,  # type: ignore[arg-type]
        position=row.position,
        slug=str(rec.get("slug") or f"{row.meal_category}-{row.position}"),
        title=str(rec.get("title") or "Daily Recipe"),
        description=str(rec.get("description") or ""),
        servings=int(rec.get("servings") or 2),
        prep_minutes=int(rec.get("prep_minutes") or 15),
        cook_minutes=rec.get("cook_minutes"),
        difficulty=rec.get("difficulty"),
        cuisine=rec.get("cuisine"),
        dietary_tags=[str(x) for x in (rec.get("dietary_tags") or [])],
        equipment_required=[str(x) for x in (rec.get("equipment_required") or [])],
        preparation_steps=[str(x) for x in (rec.get("preparation_steps") or [])],
        structured_steps=structured_steps,
        instructions=[str(x) for x in (rec.get("steps") or rec.get("instructions") or [])],
        substitutions=[str(x) for x in (rec.get("substitutions") or [])],
        cooking_tips=[str(x) for x in (rec.get("cooking_tips") or [])],
        troubleshooting_tips=[str(x) for x in (rec.get("troubleshooting_tips") or [])],
        serving_result=rec.get("serving_result"),
        food_safety_notes=[str(x) for x in (rec.get("food_safety_notes") or [])],
        availability_bucket=avail.get("availability_bucket", "explore_more"),
        available_ingredients=[_to_ing(x) for x in (avail.get("available_ingredients") or [])],
        partial_ingredients=[_to_ing(x) for x in (avail.get("partial_ingredients") or [])],
        missing_ingredients=[_to_ing(x) for x in (avail.get("missing_ingredients") or [])],
        optional_ingredients=[_to_ing(x) for x in (avail.get("optional_ingredients") or [])],
        quality_status=row.quality_status,
        quality_warnings=list(row.quality_warnings or []),
    )


async def get_daily_plan_for_household(
    db: AsyncSession,
    household_id: UUID,
    *,
    category: DailyMealCategory | None = None,
    recompute_availability: bool = False,
    persist_availability: bool = False,
    utc_now: datetime | None = None,
) -> DailyMealPlanResponse:
    """Retrieve stored daily meal plan without ever triggering LLM generation."""
    pref_tz = (
        await db.execute(
            select(HouseholdRecipePreference.timezone).where(
                HouseholdRecipePreference.household_id == household_id
            )
        )
    ).scalar_one_or_none()
    resolved_tz, local_dt = compute_household_local_now(pref_tz, utc_now)
    today_local = local_dt.date()
    active_cat = category or get_active_meal_category(local_dt)

    plan = (
        await db.execute(
            select(DailyMealPlan)
            .options(selectinload(DailyMealPlan.recipes))
            .where(
                DailyMealPlan.household_id == household_id,
                DailyMealPlan.local_date == today_local,
            )
        )
    ).scalar_one_or_none()

    is_stale_fallback = False
    if plan is None:
        # Check if a previous ready plan exists as a clearly-labelled temporary fallback
        prev_ready = (
            await db.execute(
                select(DailyMealPlan)
                .options(selectinload(DailyMealPlan.recipes))
                .where(
                    DailyMealPlan.household_id == household_id,
                    DailyMealPlan.status == "ready",
                )
                .order_by(DailyMealPlan.local_date.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if prev_ready is None:
            return DailyMealPlanResponse(
                status="pending",
                local_date=today_local,
                timezone=resolved_tz,
                active_meal_category=active_cat,
                auto_attempted=False,
                manual_regeneration_count=0,
                manual_regeneration_limit=MANUAL_REGENERATION_DAILY_LIMIT,
            )
        plan = prev_ready
        is_stale_fallback = True

    raw_inv: list[dict[str, Any]] | None = None
    if recompute_availability and plan.recipes:
        raw_inv = await _load_active_inventory_payload(db, household_id)

    categories: dict[DailyMealCategory, list[DailyMealPlanRecipeOut]] = {
        cat: [] for cat in DAILY_CATEGORIES
    }
    recipes_out: list[DailyMealPlanRecipeOut] = []

    for row in plan.recipes:
        if category is not None and row.meal_category != category:
            continue
        avail_override = None
        if raw_inv is not None:
            avail_override = compute_recipe_availability(row.recipe_data or {}, raw_inv)
            if persist_availability:
                row.availability_data = avail_override
        item_out = _to_recipe_out(row, avail_override=avail_override)
        recipes_out.append(item_out)
        if row.meal_category in categories:
            categories[row.meal_category].append(item_out)  # type: ignore[index]

    if persist_availability and raw_inv is not None:
        await db.commit()

    weather_summary = None
    if isinstance(plan.weather_snapshot, dict):
        weather_summary = plan.weather_snapshot.get("summary")

    return DailyMealPlanResponse(
        status=plan.status,  # type: ignore[arg-type]
        local_date=plan.local_date,
        timezone=plan.timezone,
        active_meal_category=active_cat,
        auto_attempted=plan.auto_attempted,
        manual_regeneration_count=plan.manual_regeneration_count,
        manual_regeneration_limit=MANUAL_REGENERATION_DAILY_LIMIT,
        is_stale_fallback=is_stale_fallback,
        generated_at=plan.generation_completed_at,
        weather_summary=weather_summary,
        provider_metadata=dict(plan.provider_metadata or {}),
        error_code=plan.error_code,
        error_message=plan.error_message,
        categories=categories,
        recipes=recipes_out,
    )
