import inspect
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.inventory.models import (
    CanonicalIngredient,
    CanonicalItem,
    IngredientMapping,
    InventoryLot,
)
from app.modules.recipes.models import HouseholdRecipePreference, RecipeFeedback
from app.modules.recipes.schemas import (
    HouseholdRecipePreferencesIn,
    HouseholdRecipePreferencesOut,
    RecipeChatExternalLinkOut,
    RecipeChatIngredientOut,
    RecipeChatRequest,
    RecipeChatResponse,
    RecipeChatSuggestionOut,
    RecipeFeedbackIn,
    RecipeFeedbackOut,
    RecipeIngredientOut,
    RecipeRecommendationOut,
    RecipeRecommendationsResponse,
    RecipeReferenceOut,
    RecipeStepOut,
)

logger = logging.getLogger(__name__)


def _get_hf_provider_cls() -> Any:
    from app.infra.llm.hf_provider import (  # type: ignore[import-not-found]
        HuggingFaceRecipeProvider,
    )

    return HuggingFaceRecipeProvider


def _get_discover_references_fn() -> Any:
    from app.infra.recipes.reference_discovery import (  # type: ignore[import-not-found]
        discover_recipe_references,
    )

    return discover_recipe_references


def _build_provider(settings: Settings) -> Any:
    provider_cls = _get_hf_provider_cls()
    sig = inspect.signature(provider_cls)
    init_kwargs: dict[str, Any] = {}
    if "hf_token" in sig.parameters:
        init_kwargs["hf_token"] = settings.HF_TOKEN
    elif "token" in sig.parameters:
        init_kwargs["token"] = settings.HF_TOKEN
    elif "api_token" in sig.parameters:
        init_kwargs["api_token"] = settings.HF_TOKEN
    if "model_id" in sig.parameters:
        init_kwargs["model_id"] = settings.HF_MODEL_ID
    if "timeout_seconds" in sig.parameters:
        init_kwargs["timeout_seconds"] = settings.HF_TIMEOUT_SECONDS
    elif "timeout" in sig.parameters:
        init_kwargs["timeout"] = settings.HF_TIMEOUT_SECONDS
    return provider_cls(**init_kwargs)


async def _load_active_inventory_payload(
    db: AsyncSession, household_id: UUID
) -> list[dict[str, Any]]:
    today = datetime.now(UTC).date()
    expiring_cutoff = today + timedelta(days=3)

    stmt = (
        select(InventoryLot, CanonicalItem)
        .join(CanonicalItem, CanonicalItem.id == InventoryLot.canonical_item_id)
        .where(
            InventoryLot.household_id == household_id,
            InventoryLot.lot_status == "active",
            InventoryLot.remaining_qty > Decimal("0"),
            CanonicalItem.pantry_eligible.is_(True),
            or_(InventoryLot.expires_on.is_(None), InventoryLot.expires_on >= today),
        )
        .order_by(InventoryLot.expires_on.asc().nulls_last(), InventoryLot.purchased_at.asc())
    )
    rows = (await db.execute(stmt)).all()

    from app.modules.catalog.classifier import FoodNameNormalizer
    from app.modules.inventory.ingredient_aggregation_service import convert_to_target_unit

    all_maps = (
        await db.execute(
            select(IngredientMapping, CanonicalIngredient)
            .join(
                CanonicalIngredient,
                CanonicalIngredient.id == IngredientMapping.canonical_ingredient_id,
            )
            .where(
                IngredientMapping.household_id == household_id,
                CanonicalIngredient.is_active.is_(True),
            )
        )
    ).all()
    confirmed_ing_by_item: dict[UUID, CanonicalIngredient] = {
        m.canonical_item_id: ing for m, ing in all_maps if m.user_confirmed
    }
    unconfirmed_item_ids: set[UUID] = {
        m.canonical_item_id for m, _ in all_maps if not m.user_confirmed
    }

    aggregated_ing: dict[UUID, dict[str, Any]] = {}
    aggregated_item: dict[UUID, dict[str, Any]] = {}
    for lot, item in rows:
        if item.category in ("non_food", "prepared_food", "cooked_meal"):
            continue
        if item.food_state in ("cooked", "non_food"):
            continue
        expiring_soon = lot.expires_on is not None and lot.expires_on <= expiring_cutoff

        c_ing = confirmed_ing_by_item.get(item.id)
        if c_ing is not None:
            conv_qty, _ = convert_to_target_unit(
                lot.remaining_qty, lot.base_unit, c_ing.standard_unit
            )
            add_qty = float(conv_qty) if conv_qty is not None else float(lot.remaining_qty)
            add_unit = c_ing.standard_unit if conv_qty is not None else lot.base_unit
            entry = aggregated_ing.get(c_ing.id)
            if entry is None:
                aggregated_ing[c_ing.id] = {
                    "canonical_item_id": str(item.id),
                    "canonical_ingredient_id": str(c_ing.id),
                    "family_id": c_ing.family_id,
                    "variant_id": c_ing.variant_id,
                    "is_unspecified_variant": c_ing.is_unspecified_variant,
                    "name": c_ing.name,
                    "normalized_name": FoodNameNormalizer.normalize(c_ing.name),
                    "category": c_ing.category,
                    "qty": add_qty,
                    "base_unit": add_unit,
                    "expiring_soon": expiring_soon,
                    "earliest_expires_on": lot.expires_on.isoformat() if lot.expires_on else None,
                    "confirmed_ingredient": True,
                }
            else:
                if entry["base_unit"] == add_unit:
                    entry["qty"] += add_qty
                entry["expiring_soon"] = entry["expiring_soon"] or expiring_soon
            continue

        entry = aggregated_item.get(item.id)
        if entry is None:
            aggregated_item[item.id] = {
                "canonical_item_id": str(item.id),
                "name": item.name,
                "normalized_name": FoodNameNormalizer.normalize(item.name),
                "category": item.category,
                "qty": float(lot.remaining_qty),
                "base_unit": lot.base_unit,
                "expiring_soon": expiring_soon,
                "earliest_expires_on": lot.expires_on.isoformat() if lot.expires_on else None,
                "confirmed_ingredient": False,
                "unconfirmed_mapping": item.id in unconfirmed_item_ids,
            }
        else:
            entry["qty"] += float(lot.remaining_qty)
            entry["expiring_soon"] = entry["expiring_soon"] or expiring_soon

    return list(aggregated_ing.values()) + list(aggregated_item.values())


async def _load_recent_cooking_history(db: AsyncSession, household_id: UUID) -> list[str]:
    from app.modules.cooking.models import CookingPlan

    stmt = (
        select(CookingPlan.recipe_title)
        .where(CookingPlan.household_id == household_id)
        .order_by(CookingPlan.started_at.desc())
        .limit(8)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [str(title) for title in rows if title]


async def _invoke_provider(
    provider: Any,
    inventory_items: list[dict[str, Any]],
    *,
    limit: int = 10,
    message: str = "Suggest recipes using expiring and available household inventory.",
    servings: int = 2,
    max_prep_minutes: int | None = None,
    dietary: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
    recent_recipes: list[str] | None = None,
    equipment: list[str] | None = None,
    meal_type: str | None = None,
    hunger_level: str | None = None,
    preference_tags: list[str] | None = None,
    preference_profile: dict[str, Any] | None = None,
    tool_executor: Any = None,
    timeout_seconds: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    meta: dict[str, Any] = {
        "provider_status": "live",
        "provider_message": None,
        "reply": None,
        "follow_up_questions": [],
        "model": None,
    }
    if hasattr(provider, "generate_recipes"):
        sig = inspect.signature(provider.generate_recipes)
        call_kwargs: dict[str, Any] = {
            "message": message,
            "inventory_items": inventory_items,
            "servings": servings,
            "max_prep_minutes": max_prep_minutes,
            "dietary": dietary,
        }
        if "conversation_history" in sig.parameters:
            call_kwargs["conversation_history"] = conversation_history
        if "recent_recipes" in sig.parameters:
            call_kwargs["recent_recipes"] = recent_recipes
        if "equipment" in sig.parameters:
            call_kwargs["equipment"] = equipment
        if "meal_type" in sig.parameters:
            call_kwargs["meal_type"] = meal_type
        if "hunger_level" in sig.parameters and hunger_level is not None:
            call_kwargs["hunger_level"] = hunger_level
        if "preference_tags" in sig.parameters and preference_tags:
            call_kwargs["preference_tags"] = preference_tags
        if "preference_profile" in sig.parameters and preference_profile is not None:
            call_kwargs["preference_profile"] = preference_profile
        if "tool_executor" in sig.parameters and tool_executor is not None:
            call_kwargs["tool_executor"] = tool_executor
        if "timeout_seconds" in sig.parameters and timeout_seconds is not None:
            call_kwargs["timeout_seconds"] = timeout_seconds
        res = provider.generate_recipes(**call_kwargs)
        if inspect.isawaitable(res):
            res = await res
        if hasattr(res, "provider_status"):
            meta["provider_status"] = getattr(res, "provider_status", "live")
            meta["provider_message"] = getattr(res, "provider_message", None)
            meta["reply"] = getattr(res, "reply", None)
            meta["follow_up_questions"] = list(getattr(res, "follow_up_questions", []) or [])
            meta["model"] = getattr(res, "model", None)
        recipes = getattr(res, "recipes", None)
        if recipes is None and isinstance(res, dict):
            meta["provider_status"] = res.get("provider_status", meta["provider_status"])
            meta["provider_message"] = res.get("provider_message")
            meta["reply"] = res.get("reply")
            meta["follow_up_questions"] = list(res.get("follow_up_questions") or [])
            recipes = res.get("recipes") or res.get("items") or []
        if recipes is not None:
            return [
                r.model_dump() if hasattr(r, "model_dump") else dict(r)
                for r in list(recipes)[:limit]
            ], meta

    for method_name in ("recommend_recipes", "generate_recommendations", "recommend", "generate"):
        method = getattr(provider, method_name, None)
        if method is None:
            continue
        sig = inspect.signature(method)
        kwargs: dict[str, Any] = {}
        if "inventory_items" in sig.parameters:
            kwargs["inventory_items"] = inventory_items
        elif "inventory" in sig.parameters:
            kwargs["inventory"] = inventory_items
        elif "items" in sig.parameters:
            kwargs["items"] = inventory_items
        if "limit" in sig.parameters:
            kwargs["limit"] = limit

        res = method(**kwargs) if kwargs else method(inventory_items)
        if inspect.isawaitable(res):
            res = await res
        if isinstance(res, dict) and "items" in res:
            return list(res["items"]), meta
        if isinstance(res, list):
            return [
                item.model_dump() if hasattr(item, "model_dump") else dict(item) for item in res
            ], meta
    return [], meta


async def _invoke_discover_references(
    query: str,
    limit: int = 2,
    key_ingredients: list[str] | None = None,
) -> list[RecipeReferenceOut]:
    fn = _get_discover_references_fn()
    sig = inspect.signature(fn)
    kwargs: dict[str, Any] = {}
    if "recipe_title" in sig.parameters:
        kwargs["recipe_title"] = query
    elif "query" in sig.parameters:
        kwargs["query"] = query
    elif "q" in sig.parameters:
        kwargs["q"] = query
    if "key_ingredients" in sig.parameters:
        kwargs["key_ingredients"] = key_ingredients or []
    if "limit" in sig.parameters:
        kwargs["limit"] = limit

    res = fn(**kwargs) if kwargs else fn(query)
    if inspect.isawaitable(res):
        res = await res
    if not res:
        return []

    out: list[RecipeReferenceOut] = []
    for ref in res[:limit]:
        if isinstance(ref, dict):
            url_str = str(ref.get("url") or "")
            l_type = (
                "video"
                if ("youtube.com" in url_str.lower() or "youtu.be" in url_str.lower())
                else "written_recipe"
            )
            out.append(
                RecipeReferenceOut(
                    title=str(ref.get("title") or query),
                    url=url_str,
                    source=ref.get("source"),
                    link_type=str(ref.get("link_type") or l_type),
                    verified=bool(ref.get("verified", True)),
                )
            )
        else:
            url_str = str(getattr(ref, "url", ""))
            l_type = (
                "video"
                if ("youtube.com" in url_str.lower() or "youtu.be" in url_str.lower())
                else "written_recipe"
            )
            out.append(
                RecipeReferenceOut(
                    title=str(getattr(ref, "title", query)),
                    url=url_str,
                    source=getattr(ref, "source", None),
                    link_type=str(getattr(ref, "link_type", None) or l_type),
                    verified=bool(getattr(ref, "verified", True)),
                )
            )
    return out


_RECOMMENDATIONS_CACHE: dict[tuple[Any, ...], tuple[float, RecipeRecommendationsResponse]] = {}
_REC_CACHE_TTL_SECONDS = 300.0


def invalidate_recipe_recommendations_cache(household_id: UUID | str) -> None:
    hid_str = str(household_id)
    for k in [key for key in _RECOMMENDATIONS_CACHE if key[0] == hid_str]:
        _RECOMMENDATIONS_CACHE.pop(k, None)


async def get_household_recipe_preferences(
    db: AsyncSession, household_id: UUID
) -> HouseholdRecipePreferencesOut:
    from app.modules.recipes.preferences import (
        PERSONALISATION_RECIPE_THRESHOLD,
        count_distinct_completed_recipes,
    )

    row = await db.get(HouseholdRecipePreference, household_id)
    distinct_count = await count_distinct_completed_recipes(db, household_id)
    if row is None:
        return HouseholdRecipePreferencesOut(
            household_id=household_id,
            distinct_completed_recipes=distinct_count,
            should_show_personalisation_prompt=distinct_count >= PERSONALISATION_RECIPE_THRESHOLD,
            personalisation_prompt_dismissed=False,
        )
    dismissed = row.personalisation_prompt_dismissed_at is not None
    mode = (
        row.recommendation_mode
        if row.recommendation_mode in {"enjoyed", "new", "balanced"}
        else "balanced"
    )
    return HouseholdRecipePreferencesOut(
        household_id=row.household_id,
        timezone=row.timezone,
        location_name=row.location_name,
        diet_type=row.diet_type,
        dietary_restrictions=list(row.dietary_restrictions or []),
        preferred_cuisines=list(row.preferred_cuisines or []),
        preferred_meal_types=list(row.preferred_meal_types or []),
        max_prep_minutes=row.max_prep_minutes,
        recommendation_mode=mode,  # type: ignore[arg-type]
        distinct_completed_recipes=distinct_count,
        should_show_personalisation_prompt=(
            distinct_count >= PERSONALISATION_RECIPE_THRESHOLD and not dismissed
        ),
        personalisation_prompt_dismissed=dismissed,
    )


async def upsert_household_recipe_preferences(
    db: AsyncSession,
    household_id: UUID,
    payload: HouseholdRecipePreferencesIn,
) -> HouseholdRecipePreferencesOut:
    row = await db.get(HouseholdRecipePreference, household_id)
    if row is None:
        row = HouseholdRecipePreference(household_id=household_id)
        db.add(row)
    row.timezone = payload.timezone
    row.location_name = payload.location_name
    row.diet_type = payload.diet_type
    row.dietary_restrictions = list(payload.dietary_restrictions)
    row.preferred_cuisines = list(payload.preferred_cuisines)
    row.preferred_meal_types = list(payload.preferred_meal_types)
    row.max_prep_minutes = payload.max_prep_minutes
    if payload.recommendation_mode is not None:
        row.recommendation_mode = payload.recommendation_mode
        if row.personalisation_prompt_dismissed_at is None:
            row.personalisation_prompt_dismissed_at = datetime.now(UTC)
    if payload.dismiss_personalisation_prompt:
        row.personalisation_prompt_dismissed_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(row)
    invalidate_recipe_recommendations_cache(household_id)
    return await get_household_recipe_preferences(db, household_id)


async def update_recommendation_mode(
    db: AsyncSession,
    household_id: UUID,
    *,
    recommendation_mode: str | None = None,
    dismiss_personalisation_prompt: bool | None = None,
) -> HouseholdRecipePreferencesOut:
    row = await db.get(HouseholdRecipePreference, household_id)
    if row is None:
        row = HouseholdRecipePreference(household_id=household_id)
        db.add(row)
    if recommendation_mode is not None:
        row.recommendation_mode = recommendation_mode
        if row.personalisation_prompt_dismissed_at is None:
            row.personalisation_prompt_dismissed_at = datetime.now(UTC)
    if dismiss_personalisation_prompt:
        row.personalisation_prompt_dismissed_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(row)
    invalidate_recipe_recommendations_cache(household_id)
    return await get_household_recipe_preferences(db, household_id)


async def record_recipe_feedback(
    db: AsyncSession,
    household_id: UUID,
    payload: RecipeFeedbackIn,
) -> RecipeFeedbackOut:
    fb = RecipeFeedback(
        household_id=household_id,
        recipe_id=payload.recipe_id,
        recipe_title=payload.recipe_title,
        action=payload.action,
    )
    db.add(fb)
    await db.commit()
    await db.refresh(fb)
    invalidate_recipe_recommendations_cache(household_id)
    return RecipeFeedbackOut(
        id=fb.id,
        household_id=fb.household_id,
        recipe_id=fb.recipe_id,
        recipe_title=fb.recipe_title,
        action=fb.action,
    )


async def list_recipe_feedback(db: AsyncSession, household_id: UUID) -> list[RecipeFeedbackOut]:
    rows = (
        (
            await db.execute(
                select(RecipeFeedback)
                .where(RecipeFeedback.household_id == household_id)
                .order_by(RecipeFeedback.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        RecipeFeedbackOut(
            id=r.id,
            household_id=r.household_id,
            recipe_id=r.recipe_id,
            recipe_title=r.recipe_title,
            action=r.action,
        )
        for r in rows
    ]


def _compute_rec_fingerprints(
    inventory_payload: list[dict[str, Any]],
    preference_profile: dict[str, Any],
) -> tuple[str, str]:
    import hashlib
    import json

    inv_sig = json.dumps(
        sorted(
            (
                str(i.get("canonical_item_id")),
                round(float(i.get("qty") or 0), 3),
                str(i.get("base_unit")),
                str(i.get("earliest_expires_on")),
            )
            for i in inventory_payload
        )
    )
    hist_sig = json.dumps(
        {
            "total": preference_profile.get("total_completed_meals", 0),
            "recent": preference_profile.get("recent_completed_meals", [])[:3],
        },
        default=str,
    )
    return (
        hashlib.sha256(inv_sig.encode()).hexdigest()[:16],
        hashlib.sha256(hist_sig.encode()).hexdigest()[:16],
    )


async def get_recipe_recommendations(
    db: AsyncSession,
    *,
    household_id: UUID,
    settings: Settings,
    limit: int = 10,
    include_references: bool = True,
    force_refresh: bool = False,
    mode: str = "progressive",
    at_time: datetime | None = None,
    timezone_override: str | None = None,
    temperature_c: float | None = None,
    rainfall_mm: float | None = None,
) -> RecipeRecommendationsResponse:
    import hashlib
    import json
    import time

    from app.modules.recipes.context_engine import (
        classify_meal_context,
        classify_season_context,
        score_and_select_contextual_recipes,
    )
    from app.modules.recipes.preferences import compute_household_preference_profile

    explicit_prefs_out = await get_household_recipe_preferences(db, household_id)
    explicit_prefs = explicit_prefs_out.model_dump()
    meal_ctx = classify_meal_context(at_time, timezone_override or explicit_prefs["timezone"])
    season_ctx = classify_season_context(
        meal_ctx.local_date,
        temperature_c=temperature_c,
        rainfall_mm=rainfall_mm,
    )

    inventory_payload = await _load_active_inventory_payload(db, household_id)
    if not inventory_payload:
        return RecipeRecommendationsResponse(
            empty_inventory=True,
            items=[],
            is_refined=True,
            refinement_pending=False,
            greeting=meal_ctx.greeting,
            meal_window=meal_ctx.meal_window,
            meal_window_label=meal_ctx.meal_window_label,
            season=season_ctx.season,
            weather_summary=season_ctx.weather_summary,
        )

    preference_profile = await compute_household_preference_profile(db, household_id)
    recent_recipes = await _load_recent_cooking_history(db, household_id)
    fb_rows = (
        (
            await db.execute(
                select(RecipeFeedback)
                .where(RecipeFeedback.household_id == household_id)
                .order_by(RecipeFeedback.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    feedback_map: dict[str, str] = {r.recipe_id: r.action for r in fb_rows}

    inv_fp, hist_fp = _compute_rec_fingerprints(inventory_payload, preference_profile)
    pref_fp = hashlib.sha256(
        json.dumps(
            {"prefs": explicit_prefs, "fb": feedback_map}, sort_keys=True, default=str
        ).encode()
    ).hexdigest()[:12]
    cache_key = (
        str(household_id),
        inv_fp,
        hist_fp,
        pref_fp,
        meal_ctx.meal_window,
        season_ctx.season,
        limit,
        include_references,
        mode,
    )
    now_ts = time.monotonic()
    if not force_refresh and at_time is None and cache_key in _RECOMMENDATIONS_CACHE:
        cached_at, cached_res = _RECOMMENDATIONS_CACHE[cache_key]
        if now_ts - cached_at < _REC_CACHE_TTL_SECONDS:
            return cached_res

    is_refined = True
    refinement_pending = False
    if mode in ("progressive", "fast"):
        raw_recipes = score_and_select_contextual_recipes(
            inventory_payload=inventory_payload,
            meal_ctx=meal_ctx,
            season_ctx=season_ctx,
            explicit_prefs=explicit_prefs,
            learned_prefs=preference_profile,
            recent_recipes=recent_recipes,
            feedback_map=feedback_map,
            limit=limit,
        )
    else:
        provider = _build_provider(settings)
        raw_recipes, _ = await _invoke_provider(
            provider,
            inventory_payload,
            limit=limit,
            recent_recipes=recent_recipes,
            meal_type=meal_ctx.meal_window,
            preference_profile=preference_profile,
        )

    from app.modules.recipes.grounding import (
        classify_recommendation_type,
        reconcile_ingredient,
    )

    items_out: list[RecipeRecommendationOut] = []
    confirmed_only = [i for i in inventory_payload if i.get("confirmed_ingredient")]
    matching_inventory = (
        confirmed_only
        if confirmed_only
        else [i for i in inventory_payload if not i.get("unconfirmed_mapping")]
    )

    for rec in raw_recipes[:limit]:
        title = str(rec.get("title") or "Chef Special")
        ingredients_out: list[RecipeIngredientOut] = []
        reconciled_list: list[dict[str, Any]] = []
        matched_count = 0
        missing_names: list[str] = []
        insufficient_names: list[str] = []
        verified_expiring = False

        raw_ing_list = list(rec.get("ingredients") or [])
        if not raw_ing_list:
            for ing in rec.get("available_ingredients") or []:
                raw_ing_list.append({**ing, "is_optional": False})
            for ing in rec.get("partial_ingredients") or []:
                raw_ing_list.append({**ing, "is_optional": False})
            for ing in rec.get("missing_ingredients") or []:
                raw_ing_list.append({**ing, "is_optional": False})
            for ing in rec.get("optional_ingredients") or []:
                raw_ing_list.append({**ing, "is_optional": True})

        for ing in raw_ing_list:
            is_opt = bool(ing.get("is_optional", False))
            r_ing = reconcile_ingredient(ing, matching_inventory, is_optional=is_opt)
            if r_ing is None:
                continue
            reconciled_list.append(r_ing)
            if r_ing["status"] == "available":
                matched_count += 1
                if r_ing["expiring_soon"]:
                    verified_expiring = True
            elif r_ing["status"] == "partial":
                if not is_opt:
                    insufficient_names.append(r_ing["name"])
                if r_ing["expiring_soon"]:
                    verified_expiring = True
            else:
                if not is_opt:
                    missing_names.append(r_ing["name"])

            cid = r_ing["canonical_item_id"]
            ingredients_out.append(
                RecipeIngredientOut(
                    canonical_item_id=UUID(str(cid)) if cid else None,
                    name=r_ing["name"],
                    required_qty=Decimal(r_ing["quantity"]),
                    base_unit=r_ing["base_unit"],
                    is_optional=is_opt,
                    essential=r_ing["essential"],
                    status=r_ing["status"],
                    available_qty=r_ing["available_qty"],
                    missing_qty=r_ing["missing_qty"],
                    unit_compatible=bool(r_ing.get("unit_compatible", True)),
                )
            )

        total_req = len(ingredients_out)
        substitutions_list = [str(s) for s in (rec.get("substitutions") or [])]
        rec_type = classify_recommendation_type(
            reconciled_list, substitutions_list, matching_inventory
        )
        refs = (
            await _invoke_discover_references(
                title,
                limit=2,
                key_ingredients=[i.name for i in ingredients_out[:5]],
            )
            if include_references
            else []
        )
        steps_list = [
            str(s) for s in (rec.get("steps") or rec.get("instructions") or []) if str(s).strip()
        ]
        if not steps_list:
            steps_list = [
                f"Prepare and measure the ingredients for {title}.",
                "Cook in a pan or pot over medium heat until done, adjusting seasoning to taste.",
                "Serve warm.",
            ]
        raw_structured = rec.get("structured_steps") or []
        structured_steps_out = [
            RecipeStepOut(
                step_number=int(st.get("step_number") or idx),
                title=str(st.get("title") or f"Step {idx}"),
                instruction=str(st.get("instruction") or ""),
                ingredients_used=[str(x) for x in (st.get("ingredients_used") or [])],
                duration_minutes=st.get("duration_minutes"),
                heat_level=st.get("heat_level"),
                technique=st.get("technique"),
                completion_cue=str(st.get("completion_cue") or ""),
            )
            for idx, st in enumerate(raw_structured, start=1)
            if isinstance(st, dict)
        ]
        from app.modules.recipes.quality_validator import validate_recipe_quality

        q_res = validate_recipe_quality(
            {
                **rec,
                "ingredients": [
                    {"name": i.name, "is_optional": i.is_optional} for i in ingredients_out
                ],
                "steps": steps_list,
                "structured_steps": raw_structured,
            }
        )
        has_verified_ref = any(r.verified and r.url for r in refs)
        src_type = "external_reference" if has_verified_ref else "ai_generated"
        src_label = (
            f"Externally referenced: {refs[0].source or refs[0].title}"
            if has_verified_ref
            else "AI-generated recipe"
        )
        avail_names = [i.name for i in ingredients_out if i.status != "missing"][:3]
        why_rec = rec.get("why_recommended") or (
            f"Uses expiring pantry items ({', '.join(avail_names)})"
            if verified_expiring and avail_names
            else f"Uses {', '.join(avail_names)} from your pantry"
            if avail_names
            else "Suggested based on your household profile"
        )
        items_out.append(
            RecipeRecommendationOut(
                recipe_id=str(rec.get("recipe_id") or uuid4()),
                title=title,
                description=rec.get("description"),
                meal_type=rec.get("meal_type"),
                cuisine=rec.get("cuisine"),
                servings=int(rec.get("servings") or 2),
                prep_minutes=int(rec.get("prep_minutes") or 15),
                cook_minutes=rec.get("cook_minutes"),
                difficulty=rec.get("difficulty"),
                source_type=src_type,
                source_label=src_label,
                recommendation_type=rec_type,
                why_recommended=str(why_rec),
                match_score=(matched_count / total_req if total_req > 0 else 0.0),
                matched_ingredients_count=matched_count,
                total_required_ingredients_count=total_req,
                uses_expiring_soon=verified_expiring,
                missing_ingredients=missing_names,
                insufficient_ingredients=insufficient_names,
                ingredients=ingredients_out,
                instructions=steps_list,
                structured_steps=structured_steps_out,
                quality_warnings=q_res.issues,
                substitutions=substitutions_list,
                cooking_tips=[str(s) for s in (rec.get("cooking_tips") or [])],
                food_safety_notes=[str(s) for s in (rec.get("food_safety_notes") or [])],
                references=refs,
            )
        )

    if mode not in ("progressive", "fast"):
        _rank_order = {"COOK_NOW": 0, "COOK_WITH_SUBSTITUTIONS": 1, "REQUIRES_SHOPPING": 2}
        items_out.sort(key=lambda r: (_rank_order.get(r.recommendation_type, 2), -r.match_score))
    result = RecipeRecommendationsResponse(
        empty_inventory=False,
        items=items_out,
        is_refined=is_refined,
        refinement_pending=refinement_pending,
        greeting=meal_ctx.greeting,
        meal_window=meal_ctx.meal_window,
        meal_window_label=meal_ctx.meal_window_label,
        season=season_ctx.season,
        weather_summary=season_ctx.weather_summary,
    )
    if at_time is None and is_refined:
        _RECOMMENDATIONS_CACHE[cache_key] = (now_ts, result)
    return result


async def chat_recipes(
    db: AsyncSession,
    *,
    household_id: UUID,
    settings: Settings,
    payload: RecipeChatRequest,
) -> RecipeChatResponse:
    import time as _time

    req_started = _time.monotonic()
    timing_ms: dict[str, int] = {}

    t_ctx_start = _time.monotonic()
    inventory_payload = await _load_active_inventory_payload(db, household_id)
    if not inventory_payload:
        return RecipeChatResponse(
            reply=(
                "Your pantry is currently empty. Add or scan groceries first so I can "
                "suggest recipes using what you have."
            ),
            provider_status="live",
            provider_message=None,
            empty_inventory=True,
            follow_up_questions=[],
            suggestions=[],
            external_links=[],
        )

    dietary = payload.dietary
    if not dietary and payload.dietary_constraints:
        dietary = ", ".join(payload.dietary_constraints)

    recent_recipes = await _load_recent_cooking_history(db, household_id)
    history_payload = [
        {"role": turn.role, "content": turn.content}
        for turn in (payload.conversation_history or [])[-8:]
    ]

    from app.modules.recipes.preferences import compute_household_preference_profile

    preference_profile = await compute_household_preference_profile(db, household_id)
    pref_row = await db.get(HouseholdRecipePreference, household_id) if db is not None else None
    active_rec_mode = payload.recommendation_mode or (
        pref_row.recommendation_mode
        if pref_row and pref_row.recommendation_mode in {"enjoyed", "new", "balanced"}
        else "balanced"
    )
    preference_profile["recommendation_mode"] = active_rec_mode
    timing_ms["pantry_context_ms"] = round((_time.monotonic() - t_ctx_start) * 1000)

    # Coherent Ask Chef budget inside the 65s frontend timeout:
    # Single-shot request (all context pre-loaded in user_payload; no multi-round tool calls).
    ask_chef_timeout = min(55.0, max(float(settings.HF_TIMEOUT_SECONDS or 20.0), 50.0))

    provider = _build_provider(settings)
    t_init_start = _time.monotonic()
    raw_recipes, meta = await _invoke_provider(
        provider,
        inventory_payload,
        limit=5,
        message=payload.message,
        servings=payload.servings,
        max_prep_minutes=payload.max_prep_minutes,
        dietary=dietary,
        conversation_history=history_payload,
        recent_recipes=recent_recipes,
        equipment=payload.equipment,
        meal_type=payload.meal_type,
        hunger_level=payload.hunger_level,
        preference_tags=payload.preference_tags,
        preference_profile=preference_profile,
        timeout_seconds=ask_chef_timeout,
    )
    timing_ms["initial_provider_ms"] = round((_time.monotonic() - t_init_start) * 1000)

    from app.modules.recipes.quality_validator import validate_recipe_quality

    # Validate before deciding on repair; derive steps from structured_steps first so
    # complete recipes are not falsely flagged for repair.
    t_val_start = _time.monotonic()
    repair_ms = 0
    if meta.get("provider_status") in {"live_hf", "live"} and raw_recipes:
        failed_issues: list[str] = []
        for rec in raw_recipes:
            raw_ings = list(rec.get("ingredients") or [])
            if not raw_ings:
                raw_ings = [
                    {**i, "is_optional": False}
                    for i in (
                        list(rec.get("available_ingredients") or [])
                        + list(rec.get("partial_ingredients") or [])
                        + list(rec.get("missing_ingredients") or [])
                    )
                ] + [{**i, "is_optional": True} for i in (rec.get("optional_ingredients") or [])]
            steps_for_val = list(rec.get("steps") or rec.get("instructions") or [])
            if not steps_for_val and rec.get("structured_steps"):
                steps_for_val = [
                    str(st.get("instruction") or "")
                    for st in (rec.get("structured_steps") or [])
                    if isinstance(st, dict) and str(st.get("instruction") or "").strip()
                ]
            q_check = validate_recipe_quality(
                {**rec, "ingredients": raw_ings, "steps": steps_for_val},
                strict_ai_generated=True,
            )
            if not q_check.is_valid:
                failed_issues.extend(q_check.issues[:3])
        timing_ms["quality_validation_ms"] = round((_time.monotonic() - t_val_start) * 1000)

        elapsed_so_far = _time.monotonic() - req_started
        if failed_issues and elapsed_so_far < 30.0:
            t_repair_start = _time.monotonic()
            repair_timeout = max(10.0, 55.0 - elapsed_so_far)
            repair_msg = (
                f"{payload.message}\n\n[QUALITY REPAIR]: Previous draft had incomplete execution "
                f"details ({'; '.join(failed_issues[:4])}). Regenerate with complete quantities, "
                "units, prep/cook times, difficulty, equipment_required, preparation_steps, and "
                "detailed sequential structured_steps (heat_level, duration_minutes, cue)."
            )
            retry_recipes, retry_meta = await _invoke_provider(
                provider,
                inventory_payload,
                limit=5,
                message=repair_msg,
                servings=payload.servings,
                max_prep_minutes=payload.max_prep_minutes,
                dietary=dietary,
                conversation_history=history_payload,
                recent_recipes=recent_recipes,
                equipment=payload.equipment,
                meal_type=payload.meal_type,
                hunger_level=payload.hunger_level,
                preference_tags=payload.preference_tags,
                preference_profile=preference_profile,
                timeout_seconds=repair_timeout,
            )
            repair_ms = round((_time.monotonic() - t_repair_start) * 1000)
            if retry_recipes:
                raw_recipes = retry_recipes
                meta = retry_meta
    else:
        timing_ms["quality_validation_ms"] = round((_time.monotonic() - t_val_start) * 1000)
    timing_ms["repair_provider_ms"] = repair_ms

    from app.modules.recipes.grounding import (
        classify_recommendation_type,
        reconcile_ingredient,
    )

    suggestions: list[RecipeChatSuggestionOut] = []
    all_links: list[RecipeChatExternalLinkOut] = []

    def _to_chat_ing(r_ing: dict[str, Any]) -> RecipeChatIngredientOut:
        cid = r_ing["canonical_item_id"]
        return RecipeChatIngredientOut(
            name=r_ing["name"],
            canonical_item_id=UUID(str(cid)) if cid else None,
            quantity=r_ing["quantity"],
            base_unit=r_ing["base_unit"],
            essential=r_ing["essential"],
            status=r_ing["status"],
            available=r_ing["available"],
            available_qty=r_ing["available_qty"],
            missing_qty=r_ing["missing_qty"],
        )

    for rec in raw_recipes:
        title = str(rec.get("title") or "Chef Special")
        available_ings: list[RecipeChatIngredientOut] = []
        partial_ings: list[RecipeChatIngredientOut] = []
        missing_ings: list[RecipeChatIngredientOut] = []
        optional_ings: list[RecipeChatIngredientOut] = []
        reconciled_list: list[dict[str, Any]] = []
        verified_expiring_names: list[str] = []

        raw_ing_pairs: list[tuple[dict[str, Any], bool]] = []
        if rec.get("ingredients"):
            for ing in rec.get("ingredients") or []:
                raw_ing_pairs.append((ing, bool(ing.get("is_optional", False))))
        else:
            for ing in rec.get("available_ingredients") or []:
                raw_ing_pairs.append((ing, False))
            for ing in rec.get("partial_ingredients") or []:
                raw_ing_pairs.append((ing, False))
            for ing in rec.get("missing_ingredients") or []:
                raw_ing_pairs.append((ing, False))
            for ing in rec.get("optional_ingredients") or []:
                raw_ing_pairs.append((ing, True))

        for ing, is_opt in raw_ing_pairs:
            r_ing = reconcile_ingredient(ing, inventory_payload, is_optional=is_opt)
            if r_ing is None:
                continue
            reconciled_list.append(r_ing)
            ing_out = _to_chat_ing(r_ing)
            if is_opt and r_ing["status"] == "missing":
                optional_ings.append(ing_out)
            elif r_ing["status"] == "available":
                available_ings.append(ing_out)
                if r_ing["expiring_soon"] and r_ing["name"] not in verified_expiring_names:
                    verified_expiring_names.append(r_ing["name"])
            elif r_ing["status"] == "partial":
                partial_ings.append(ing_out)
                if r_ing["expiring_soon"] and r_ing["name"] not in verified_expiring_names:
                    verified_expiring_names.append(r_ing["name"])
            else:
                missing_ings.append(ing_out)

        substitutions_list = [str(s) for s in (rec.get("substitutions") or [])]
        rec_type = classify_recommendation_type(
            reconciled_list, substitutions_list, inventory_payload
        )

        refs = await _invoke_discover_references(
            title,
            limit=2,
            key_ingredients=[i.name for i in available_ings[:5]],
        )
        links = [
            RecipeChatExternalLinkOut(
                title=r.title,
                url=r.url,
                source=r.source,
                link_type=r.link_type,
                verified=r.verified,
            )
            for r in refs
            if r.url
        ]
        all_links.extend(links)

        raw_structured = rec.get("structured_steps") or []
        structured_steps_out = [
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
            for idx, st in enumerate(raw_structured, start=1)
            if isinstance(st, dict)
        ]
        steps_list = [
            str(s) for s in (rec.get("steps") or rec.get("instructions") or []) if str(s).strip()
        ]
        if not steps_list and structured_steps_out:
            steps_list = [
                st.instruction.strip() for st in structured_steps_out if st.instruction.strip()
            ]
        if not steps_list:
            steps_list = [
                f"Prepare and measure the ingredients for {title}.",
                "Cook over medium heat until done, adjusting seasoning to taste.",
                "Serve warm.",
            ]
        q_res = validate_recipe_quality(
            {
                **rec,
                "ingredients": [
                    {
                        "name": i.name,
                        "quantity": (
                            f"{i.quantity} {i.base_unit}".strip()
                            if i.base_unit and i.base_unit not in str(i.quantity)
                            else i.quantity
                        ),
                        "is_optional": not i.essential,
                    }
                    for i in (available_ings + partial_ings + missing_ings + optional_ings)
                ],
                "steps": steps_list,
                "structured_steps": raw_structured,
            },
            strict_ai_generated=True,
        )
        if not q_res.is_valid and rec_type == "COOK_NOW":
            rec_type = "COOK_WITH_SUBSTITUTIONS"
        has_verified_ref = any(lnk.verified and lnk.url for lnk in links)
        src_type = "external_reference" if has_verified_ref else "ai_generated"
        src_label = (
            f"Externally referenced: {links[0].source or links[0].title}"
            if has_verified_ref
            else "AI-generated recipe"
        )

        rec_id = str(rec.get("recipe_id") or uuid4())
        suggestions.append(
            RecipeChatSuggestionOut(
                id=rec_id,
                recipe_id=rec_id,
                title=title,
                meal_type=rec.get("meal_type"),
                servings=int(rec.get("servings") or payload.servings),
                prep_minutes=rec.get("prep_minutes"),
                cook_minutes=rec.get("cook_minutes"),
                difficulty=rec.get("difficulty"),
                source_type=src_type,
                source_label=src_label,
                recommendation_type=rec_type,
                why_recommended=rec.get("why_recommended"),
                summary=rec.get("summary") or rec.get("description"),
                available_ingredients=available_ings,
                partial_ingredients=partial_ings,
                missing_ingredients=missing_ings,
                optional_ingredients=optional_ings,
                substitutions=substitutions_list,
                cooking_tips=[str(s) for s in (rec.get("cooking_tips") or [])],
                troubleshooting_tips=[str(s) for s in (rec.get("troubleshooting_tips") or [])],
                serving_result=rec.get("serving_result"),
                food_safety_notes=[str(s) for s in (rec.get("food_safety_notes") or [])],
                uses_expiring_items=verified_expiring_names,
                equipment_required=[str(s) for s in (rec.get("equipment_required") or [])],
                preparation_steps=[str(s) for s in (rec.get("preparation_steps") or [])],
                instructions=steps_list,
                structured_steps=structured_steps_out,
                quality_warnings=q_res.issues,
                external_links=links,
            )
        )

    import re

    _rank_order = {"COOK_NOW": 0, "COOK_WITH_SUBSTITUTIONS": 1, "REQUIRES_SHOPPING": 2}
    msg_tokens = {
        t
        for t in re.sub(r"[^a-z0-9\s]", " ", payload.message.lower()).split()
        if len(t) >= 3 and t not in {"with", "and", "for", "make", "cook", "some", "recipe"}
    }

    def _suggestion_sort_key(
        idx_and_sug: tuple[int, RecipeChatSuggestionOut],
    ) -> tuple[int, int, int]:
        idx, sug = idx_and_sug
        title_tokens = {
            t
            for t in re.sub(r"[^a-z0-9\s]", " ", sug.title.lower()).split()
            if len(t) >= 3
        }
        overlap = len(msg_tokens & title_tokens)
        relevance_tier = 2 if overlap >= 2 else (1 if overlap == 1 else 0)
        return (-relevance_tier, _rank_order.get(sug.recommendation_type, 2), idx)

    suggestions = [s for _, s in sorted(enumerate(suggestions), key=_suggestion_sort_key)]

    reply = meta.get("reply") or (
        f"Here are {len(suggestions)} recipe ideas based on your current pantry."
        if suggestions
        else "I couldn't find matching recipes for your current inventory."
    )
    response_obj = RecipeChatResponse(
        reply=str(reply),
        provider_status=str(meta.get("provider_status") or "live"),
        provider_message=meta.get("provider_message"),
        model=meta.get("model") or settings.HF_MODEL_ID,
        empty_inventory=False,
        follow_up_questions=list(meta.get("follow_up_questions") or []),
        suggestions=suggestions,
        external_links=all_links[:4],
    )
    timing_ms["total_backend_ms"] = round((_time.monotonic() - req_started) * 1000)
    logger.info(
        "Ask Chef completed: status=%s suggestions=%d mode=%s timings=%s",
        response_obj.provider_status,
        len(suggestions),
        active_rec_mode,
        timing_ms,
    )
    return response_obj


def get_catalogue_recipe_detail(recipe_id: str) -> RecipeRecommendationOut | None:
    """Return full structured instructions and steps for a curated catalogue recipe_id."""
    from app.modules.recipes.catalogue_data import EXPANDED_RECIPE_CATALOG

    match = next((r for r in EXPANDED_RECIPE_CATALOG if r.get("recipe_id") == recipe_id), None)
    if match is None:
        return None

    ingredients_out = [
        RecipeIngredientOut(
            name=str(ing.get("name") or ""),
            required_qty=str(ing.get("quantity") or 0),
            base_unit=str(ing.get("unit") or "g"),
            is_optional=bool(ing.get("is_optional", False)),
            status="available",
            available_qty=None,
            available_unit=None,
        )
        for ing in (match.get("ingredients") or [])
    ]
    raw_structured = list(match.get("structured_steps") or [])
    structured_steps_out = [RecipeStepOut(**st) for st in raw_structured]
    steps_list = [str(s) for s in (match.get("steps") or [])]

    return RecipeRecommendationOut(
        recipe_id=str(match["recipe_id"]),
        title=str(match["title"]),
        description=match.get("description"),
        meal_type=match.get("meal_type"),
        cuisine=match.get("cuisine"),
        servings=int(match.get("servings") or 2),
        prep_minutes=int(match.get("prep_minutes") or 15),
        cook_minutes=match.get("cook_minutes"),
        difficulty=match.get("difficulty"),
        source_type="ai_generated",
        source_label="Curated recipe",
        recommendation_type="COOK_NOW",
        why_recommended="Saved cooking plan recipe",
        match_score=1.0,
        matched_ingredients_count=len(ingredients_out),
        total_required_ingredients_count=len(
            [i for i in ingredients_out if not i.is_optional]
        ),
        uses_expiring_soon=False,
        missing_ingredients=[],
        insufficient_ingredients=[],
        ingredients=ingredients_out,
        instructions=steps_list,
        structured_steps=structured_steps_out,
        quality_warnings=[],
        substitutions=[],
        cooking_tips=[str(t) for t in (match.get("cooking_tips") or [])],
        food_safety_notes=[str(n) for n in (match.get("food_safety_notes") or [])],
        external_links=[],
    )
