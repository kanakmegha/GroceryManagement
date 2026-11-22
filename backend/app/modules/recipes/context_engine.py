"""Context-aware meal-time, seasonal/weather, and pantry-grounded recipe ranking engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.modules.recipes.catalogue_data import EXPANDED_RECIPE_CATALOG
from app.modules.recipes.grounding import (
    classify_recommendation_type,
    reconcile_ingredient,
)

CONTEXTUAL_RECIPE_CATALOG = EXPANDED_RECIPE_CATALOG

MealWindow = Literal["breakfast", "lunch", "snack", "dinner", "flexible"]
SeasonName = Literal["summer", "monsoon", "winter", "transitional"]


@dataclass(frozen=True)
class MealContext:
    meal_window: MealWindow
    greeting: str
    meal_window_label: str
    local_hour: int
    local_date: date
    timezone: str


@dataclass(frozen=True)
class SeasonContext:
    season: SeasonName
    weather_used: bool
    weather_summary: str


def classify_meal_context(
    now_utc: datetime | None = None,
    timezone_name: str | None = "Asia/Kolkata",
) -> MealContext:
    """Classify local time into Breakfast (6-11), Lunch (11-15), Snack (15-19), Dinner (19-23), or
    Flexible.
    """
    tz_str = (timezone_name or "Asia/Kolkata").strip() or "Asia/Kolkata"
    try:
        tz = ZoneInfo(tz_str)
    except Exception:
        tz_str = "Asia/Kolkata"
        tz = ZoneInfo(tz_str)

    ref_utc = now_utc or datetime.now(UTC)
    if ref_utc.tzinfo is None:
        ref_utc = ref_utc.replace(tzinfo=UTC)
    local_dt = ref_utc.astimezone(tz)
    hour = local_dt.hour

    if 6 <= hour < 11:
        window: MealWindow = "breakfast"
        greeting = "Good morning"
        label = "Breakfast Ideas"
    elif 11 <= hour < 15:
        window = "lunch"
        greeting = "Good afternoon"
        label = "Lunch Ideas"
    elif 15 <= hour < 19:
        window = "snack"
        greeting = "Good afternoon"
        label = "Evening Snacks"
    elif 19 <= hour < 23:
        window = "dinner"
        greeting = "Good evening"
        label = "Dinner Ideas"
    else:
        window = "flexible"
        greeting = "Hello"
        label = "Light & Anytime Meals"

    return MealContext(
        meal_window=window,
        greeting=greeting,
        meal_window_label=label,
        local_hour=hour,
        local_date=local_dt.date(),
        timezone=tz_str,
    )


def classify_season_context(
    local_date: date,
    *,
    temperature_c: float | None = None,
    rainfall_mm: float | None = None,
) -> SeasonContext:
    """Determine season from local date, refined by optional real weather metrics (never
    invented).
    """
    month = local_date.month
    if month in (3, 4, 5):
        season: SeasonName = "summer"
    elif month in (6, 7, 8, 9):
        season = "monsoon"
    elif month in (12, 1, 2):
        season = "winter"
    else:
        season = "transitional"

    weather_used = temperature_c is not None or rainfall_mm is not None
    if rainfall_mm is not None and rainfall_mm >= 2.0:
        season = "monsoon"
    elif temperature_c is not None and temperature_c >= 32.0:
        season = "summer"
    elif temperature_c is not None and temperature_c <= 18.0:
        season = "winter"

    if weather_used:
        parts: list[str] = [season.capitalize()]
        if temperature_c is not None:
            parts.append(f"{round(temperature_c)}°C")
        if rainfall_mm is not None and rainfall_mm > 0:
            parts.append(f"{rainfall_mm:.1f} mm rain")
        summary = " · ".join(parts)
    else:
        summary = f"{season.capitalize()} season"

    return SeasonContext(season=season, weather_used=weather_used, weather_summary=summary)


CONTEXTUAL_RECIPE_CATALOG: list[dict[str, Any]] = [
    # --- BREAKFAST ---
    {
        "recipe_id": "ctx-poha",
        "title": "Kanda Batata Poha",
        "description": (
            "Light flattened rice tempered with mustard seeds, onion, potato, turmeric, and "
            "peanuts."
        ),
        "meal_types": ["breakfast", "snack", "flexible"],
        "cuisine": "Indian",
        "diet_tags": ["vegetarian", "vegan"],
        "season_tags": ["summer", "monsoon", "winter", "transitional"],
        "prep_minutes": 10,
        "cook_minutes": 12,
        "difficulty": "easy",
        "ingredients": [
            {"name": "Poha", "qty": 150, "unit": "g", "is_optional": False},
            {"name": "Onion", "qty": 1, "unit": "pcs", "is_optional": False},
            {"name": "Mustard Seeds", "qty": 5, "unit": "g", "is_optional": False},
            {"name": "Oil", "qty": 15, "unit": "ml", "is_optional": False},
            {"name": "Peanuts", "qty": 30, "unit": "g", "is_optional": True},
        ],
        "steps": [
            "Rinse poha gently in a colander and drain excess water.",
            "Temper mustard seeds, chopped onion, and peanuts in oil until golden.",
            "Toss in turmeric and soaked poha; steam covered for 3 minutes and serve warm.",
        ],
    },
    {
        "recipe_id": "ctx-upma",
        "title": "South Indian Vegetable Upma",
        "description": (
            "Warm roasted semolina cooked with tempered mustard seeds, onion, and spices."
        ),
        "meal_types": ["breakfast", "flexible"],
        "cuisine": "South Indian",
        "diet_tags": ["vegetarian", "vegan"],
        "season_tags": ["monsoon", "winter", "transitional"],
        "prep_minutes": 8,
        "cook_minutes": 12,
        "difficulty": "easy",
        "ingredients": [
            {"name": "Semolina", "qty": 150, "unit": "g", "is_optional": False},
            {"name": "Onion", "qty": 1, "unit": "pcs", "is_optional": False},
            {"name": "Mustard Seeds", "qty": 5, "unit": "g", "is_optional": False},
            {"name": "Oil", "qty": 15, "unit": "ml", "is_optional": False},
        ],
        "steps": [
            "Dry-roast semolina for 3 minutes until fragrant and set aside.",
            "Saute mustard seeds and onion in oil, add 2.5x boiling water, and stir in semolina.",
            "Cover and cook on low heat until fluffy.",
        ],
    },
    {
        "recipe_id": "ctx-moong-chilla",
        "title": "Savory Moong Dal Chilla",
        "description": "Protein-rich golden lentil crepes spiced with chilli and cumin.",
        "meal_types": ["breakfast", "snack", "dinner"],
        "cuisine": "North Indian",
        "diet_tags": ["vegetarian", "vegan"],
        "season_tags": ["monsoon", "winter", "transitional"],
        "prep_minutes": 15,
        "cook_minutes": 12,
        "difficulty": "easy",
        "ingredients": [
            {"name": "Moong Dal", "qty": 150, "unit": "g", "is_optional": False},
            {"name": "Onion", "qty": 1, "unit": "pcs", "is_optional": True},
            {"name": "Oil", "qty": 15, "unit": "ml", "is_optional": False},
            {"name": "Red Chilli Powder", "qty": 2, "unit": "g", "is_optional": True},
        ],
        "steps": [
            "Blend soaked moong dal into a smooth pourable batter.",
            "Spread thinly on a hot greased tawa, top with chopped onion, and crisp both sides.",
        ],
    },
    {
        "recipe_id": "ctx-egg-bhurji",
        "title": "Masala Egg Bhurji",
        "description": "Quick spiced scrambled eggs with onion, tomato, and turmeric.",
        "meal_types": ["breakfast", "dinner", "flexible"],
        "cuisine": "Indian",
        "diet_tags": ["eggetarian", "non_vegetarian"],
        "season_tags": ["summer", "monsoon", "winter", "transitional"],
        "prep_minutes": 5,
        "cook_minutes": 8,
        "difficulty": "easy",
        "ingredients": [
            {"name": "Eggs", "qty": 3, "unit": "pcs", "is_optional": False},
            {"name": "Onion", "qty": 1, "unit": "pcs", "is_optional": False},
            {"name": "Tomato", "qty": 1, "unit": "pcs", "is_optional": False},
            {"name": "Oil", "qty": 15, "unit": "ml", "is_optional": False},
        ],
        "steps": [
            "Saute onion and tomato in oil with turmeric and chilli powder.",
            "Pour in beaten eggs and scramble gently until softly cooked.",
        ],
    },
    {
        "recipe_id": "ctx-oats-porridge",
        "title": "Warm Oats & Almond Milk Porridge",
        "description": (
            "Creamy rolled oats simmered with milk, toasted almonds, and a touch of sweetness."
        ),
        "meal_types": ["breakfast", "flexible"],
        "cuisine": "Quick",
        "diet_tags": ["vegetarian"],
        "season_tags": ["winter", "monsoon", "transitional"],
        "prep_minutes": 5,
        "cook_minutes": 8,
        "difficulty": "easy",
        "ingredients": [
            {"name": "Rolled Oats", "qty": 80, "unit": "g", "is_optional": False},
            {"name": "Milk", "qty": 300, "unit": "ml", "is_optional": False},
            {"name": "Almonds", "qty": 20, "unit": "g", "is_optional": True},
            {"name": "Sugar", "qty": 10, "unit": "g", "is_optional": True},
        ],
        "steps": [
            "Simmer rolled oats in milk for 5 minutes until creamy.",
            "Top with sliced almonds and serve warm.",
        ],
    },
    # --- LUNCH ---
    {
        "recipe_id": "ctx-dal-rice",
        "title": "Toor Dal Tadka & Steamed Rice",
        "description": "Classic comforting yellow lentil tadka served with steamed rice.",
        "meal_types": ["lunch", "dinner"],
        "cuisine": "Indian",
        "diet_tags": ["vegetarian", "vegan"],
        "season_tags": ["summer", "monsoon", "winter", "transitional"],
        "prep_minutes": 10,
        "cook_minutes": 25,
        "difficulty": "easy",
        "ingredients": [
            {"name": "Toor Dal", "qty": 150, "unit": "g", "is_optional": False},
            {"name": "Basmati Rice", "qty": 200, "unit": "g", "is_optional": False},
            {"name": "Oil", "qty": 15, "unit": "ml", "is_optional": False},
            {"name": "Red Chilli Powder", "qty": 3, "unit": "g", "is_optional": True},
        ],
        "steps": [
            "Pressure-cook rinsed toor dal with turmeric until soft.",
            "Prepare a warm tadka with oil and spices, pour over dal, and serve with steamed rice.",
        ],
    },
    {
        "recipe_id": "ctx-chana-dal-tadka",
        "title": "Homestyle Chana Dal Tadka",
        "description": "Hearty split Bengal gram simmered with tomato, cumin, and aromatic spices.",
        "meal_types": ["lunch", "dinner"],
        "cuisine": "North Indian",
        "diet_tags": ["vegetarian", "vegan"],
        "season_tags": ["monsoon", "winter", "transitional"],
        "prep_minutes": 10,
        "cook_minutes": 25,
        "difficulty": "easy",
        "ingredients": [
            {"name": "Chana Dal", "qty": 180, "unit": "g", "is_optional": False},
            {"name": "Oil", "qty": 15, "unit": "ml", "is_optional": False},
            {"name": "Red Chilli Powder", "qty": 3, "unit": "g", "is_optional": True},
            {"name": "Coriander Seeds", "qty": 5, "unit": "g", "is_optional": True},
        ],
        "steps": [
            "Soak and pressure-cook chana dal until tender yet holding shape.",
            "Simmer with spiced oil tadka and serve hot with rice or roti.",
        ],
    },
    {
        "recipe_id": "ctx-chole-rice",
        "title": "Spiced Chickpea Chole & Rice",
        "description": (
            "Hearty chickpea curry simmered in roasted spices, paired with steamed rice."
        ),
        "meal_types": ["lunch", "dinner"],
        "cuisine": "North Indian",
        "diet_tags": ["vegetarian", "vegan"],
        "season_tags": ["monsoon", "winter", "transitional"],
        "prep_minutes": 15,
        "cook_minutes": 25,
        "difficulty": "medium",
        "ingredients": [
            {"name": "Chickpeas (Kabuli Chana)", "qty": 200, "unit": "g", "is_optional": False},
            {"name": "Basmati Rice", "qty": 180, "unit": "g", "is_optional": False},
            {"name": "Oil", "qty": 20, "unit": "ml", "is_optional": False},
            {"name": "Red Chilli Powder", "qty": 4, "unit": "g", "is_optional": True},
        ],
        "steps": [
            "Pressure-cook soaked chickpeas until tender.",
            "Simmer in spiced gravy for 10 minutes and serve with steamed rice.",
        ],
    },
    {
        "recipe_id": "ctx-sambar-rice",
        "title": "South Indian Sambar Rice",
        "description": "Wholesome one-pot lentil and rice simmered with mustard seeds and hing.",
        "meal_types": ["lunch", "dinner"],
        "cuisine": "South Indian",
        "diet_tags": ["vegetarian", "vegan"],
        "season_tags": ["summer", "monsoon", "winter", "transitional"],
        "prep_minutes": 10,
        "cook_minutes": 25,
        "difficulty": "easy",
        "ingredients": [
            {"name": "Toor Dal", "qty": 120, "unit": "g", "is_optional": False},
            {"name": "Sona Masoori Rice", "qty": 180, "unit": "g", "is_optional": False},
            {"name": "Oil", "qty": 15, "unit": "ml", "is_optional": False},
        ],
        "steps": [
            "Cook toor dal and rice together until soft.",
            "Finish with a fragrant tempering of mustard seeds, hing, and chilli.",
        ],
    },
    {
        "recipe_id": "ctx-paneer-spinach",
        "title": "Garlic Paneer & Spinach Stir-Fry",
        "description": "Quick high-protein paneer tossed with wilted spinach and garlic.",
        "meal_types": ["lunch", "dinner", "flexible"],
        "cuisine": "North Indian",
        "diet_tags": ["vegetarian"],
        "season_tags": ["winter", "transitional"],
        "prep_minutes": 10,
        "cook_minutes": 12,
        "difficulty": "easy",
        "ingredients": [
            {"name": "Paneer", "qty": 200, "unit": "g", "is_optional": False},
            {"name": "Spinach", "qty": 150, "unit": "g", "is_optional": False},
            {"name": "Oil", "qty": 15, "unit": "ml", "is_optional": False},
        ],
        "steps": [
            "Sear paneer cubes lightly in oil.",
            "Toss in chopped spinach and garlic until wilted and serve warm.",
        ],
    },
    # --- EVENING SNACKS ---
    {
        "recipe_id": "ctx-roasted-nuts",
        "title": "Spiced Roasted Almonds & Walnuts",
        "description": "Crunchy pan-roasted nuts lightly tossed with red chilli and warm spices.",
        "meal_types": ["snack", "flexible"],
        "cuisine": "Quick",
        "diet_tags": ["vegetarian", "vegan"],
        "season_tags": ["monsoon", "winter", "transitional"],
        "prep_minutes": 3,
        "cook_minutes": 6,
        "difficulty": "easy",
        "ingredients": [
            {"name": "Almonds", "qty": 50, "unit": "g", "is_optional": False},
            {"name": "Walnuts", "qty": 50, "unit": "g", "is_optional": False},
            {"name": "Red Chilli Powder", "qty": 1, "unit": "g", "is_optional": True},
        ],
        "steps": [
            "Dry-roast almonds and walnuts on low heat for 5 minutes until crisp.",
            "Toss with a pinch of salt and red chilli powder; cool before serving.",
        ],
    },
    {
        "recipe_id": "ctx-roasted-chana-snack",
        "title": "Crispy Spiced Chana Snack",
        "description": "Crunchy pan-toasted chickpeas/chana tossed with chilli and lemon.",
        "meal_types": ["snack", "flexible"],
        "cuisine": "Indian",
        "diet_tags": ["vegetarian", "vegan"],
        "season_tags": ["monsoon", "winter", "transitional", "summer"],
        "prep_minutes": 5,
        "cook_minutes": 10,
        "difficulty": "easy",
        "ingredients": [
            {"name": "Chickpeas (Kabuli Chana)", "qty": 150, "unit": "g", "is_optional": False},
            {"name": "Oil", "qty": 10, "unit": "ml", "is_optional": False},
            {"name": "Red Chilli Powder", "qty": 2, "unit": "g", "is_optional": True},
        ],
        "steps": [
            "Pat cooked chickpeas dry and roast in a pan with oil until crisp.",
            "Dust with chilli powder and serve as an evening tea-time snack.",
        ],
    },
    {
        "recipe_id": "ctx-spiced-milk",
        "title": "Warm Badam & Turmeric Milk",
        "description": "Soothing warm milk infused with crushed almonds and a touch of sweetness.",
        "meal_types": ["snack", "breakfast", "flexible"],
        "cuisine": "Indian",
        "diet_tags": ["vegetarian"],
        "season_tags": ["monsoon", "winter", "transitional"],
        "prep_minutes": 5,
        "cook_minutes": 6,
        "difficulty": "easy",
        "ingredients": [
            {"name": "Milk", "qty": 300, "unit": "ml", "is_optional": False},
            {"name": "Almonds", "qty": 15, "unit": "g", "is_optional": False},
            {"name": "Sugar", "qty": 10, "unit": "g", "is_optional": True},
        ],
        "steps": [
            "Simmer milk with crushed almonds for 5 minutes.",
            "Sweeten lightly and serve warm.",
        ],
    },
    # --- DINNER ---
    {
        "recipe_id": "ctx-khichdi",
        "title": "Comforting Moong Dal Khichdi",
        "description": "Gentle, nourishing one-pot moong dal and rice khichdi for an easy dinner.",
        "meal_types": ["dinner", "lunch", "flexible"],
        "cuisine": "Indian",
        "diet_tags": ["vegetarian", "vegan"],
        "season_tags": ["monsoon", "winter", "transitional", "summer"],
        "prep_minutes": 10,
        "cook_minutes": 20,
        "difficulty": "easy",
        "ingredients": [
            {"name": "Moong Dal", "qty": 120, "unit": "g", "is_optional": False},
            {"name": "Basmati Rice", "qty": 150, "unit": "g", "is_optional": False},
            {"name": "Oil", "qty": 10, "unit": "ml", "is_optional": False},
        ],
        "steps": [
            "Rinse moong dal and rice together and pressure-cook with 4x water until soft.",
            "Top with warm tempered oil or ghee and serve hot.",
        ],
    },
    {
        "recipe_id": "ctx-roti-dal",
        "title": "Whole Wheat Roti & Urad Dal Tadka",
        "description": "Soft whole wheat rotis served alongside creamy spiced urad dal.",
        "meal_types": ["dinner", "lunch"],
        "cuisine": "North Indian",
        "diet_tags": ["vegetarian", "vegan"],
        "season_tags": ["winter", "monsoon", "transitional"],
        "prep_minutes": 15,
        "cook_minutes": 25,
        "difficulty": "medium",
        "ingredients": [
            {"name": "Whole Wheat Flour (Atta)", "qty": 200, "unit": "g", "is_optional": False},
            {"name": "Urad Dal", "qty": 150, "unit": "g", "is_optional": False},
            {"name": "Oil", "qty": 15, "unit": "ml", "is_optional": False},
        ],
        "steps": [
            "Knead whole wheat flour into a soft dough and rest 10 minutes.",
            "Simmer urad dal with spiced tadka and serve hot with fresh rotis.",
        ],
    },
    {
        "recipe_id": "ctx-dosa",
        "title": "Crispy Urad & Rice Dosa",
        "description": "Light fermented-style urad dal and rice crepes cooked golden.",
        "meal_types": ["dinner", "breakfast"],
        "cuisine": "South Indian",
        "diet_tags": ["vegetarian", "vegan"],
        "season_tags": ["summer", "monsoon", "winter", "transitional"],
        "prep_minutes": 15,
        "cook_minutes": 15,
        "difficulty": "medium",
        "ingredients": [
            {"name": "Urad Dal", "qty": 100, "unit": "g", "is_optional": False},
            {"name": "Idli Rice", "qty": 200, "unit": "g", "is_optional": False},
            {"name": "Oil", "qty": 15, "unit": "ml", "is_optional": False},
        ],
        "steps": [
            "Grind soaked urad dal and rice into a smooth batter.",
            "Spread thinly on a hot griddle with a drizzle of oil until crisp.",
        ],
    },
]


def _passes_dietary_filter(
    recipe: dict[str, Any],
    diet_type: str,
    dietary_restrictions: list[str],
) -> bool:
    diet_tags = set(recipe.get("diet_tags") or [])
    dt = (diet_type or "any").strip().lower()
    if dt == "vegetarian" and "vegetarian" not in diet_tags:
        return False
    if dt == "vegan" and "vegan" not in diet_tags:
        return False
    if dt == "eggetarian" and not ({"vegetarian", "eggetarian"} & diet_tags):
        return False

    restr_low = {r.strip().lower() for r in dietary_restrictions if r.strip()}
    if not restr_low:
        return True
    ing_names = " ".join(str(i["name"]).lower() for i in recipe.get("ingredients") or [])
    if ("nut_free" in restr_low or "no_nuts" in restr_low) and any(
        n in ing_names for n in ("almond", "walnut", "peanut", "cashew")
    ):
        return False
    return not (
        ("dairy_free" in restr_low or "lactose_free" in restr_low)
        and any(d in ing_names for d in ("milk", "paneer", "curd", "ghee", "butter", "cheese"))
    )


def score_and_select_contextual_recipes(
    *,
    inventory_payload: list[dict[str, Any]],
    meal_ctx: MealContext,
    season_ctx: SeasonContext,
    explicit_prefs: dict[str, Any],
    learned_prefs: dict[str, Any],
    recent_recipes: list[str],
    feedback_map: dict[str, str] | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Rank and classify recipes using confirmed pantry stock, meal window, season, feedback, and
    user preferences.
    """
    if not inventory_payload:
        return []

    confirmed_only = [i for i in inventory_payload if i.get("confirmed_ingredient")]
    unmapped_fallback = [i for i in inventory_payload if not i.get("unconfirmed_mapping")]
    matching_inventory = confirmed_only if confirmed_only else unmapped_fallback
    if not matching_inventory:
        return []

    diet_type = str(explicit_prefs.get("diet_type") or "any")
    restrictions = list(explicit_prefs.get("dietary_restrictions") or [])
    pref_cuisines = {
        c.strip().lower()
        for c in (
            explicit_prefs.get("preferred_cuisines")
            or learned_prefs.get("preferred_cuisines")
            or []
        )
        if str(c).strip()
    }
    pref_meals = {
        m.strip().lower()
        for m in (explicit_prefs.get("preferred_meal_types") or [])
        if str(m).strip()
    }
    max_prep = explicit_prefs.get("max_prep_minutes") or learned_prefs.get(
        "preferred_max_cook_minutes"
    )
    recent_low = {r.strip().lower() for r in recent_recipes if r.strip()}
    disliked_low = {
        r.strip().lower() for r in (learned_prefs.get("disliked_dishes") or []) if str(r).strip()
    }
    fb_by_id = feedback_map or {}

    scored: list[tuple[float, dict[str, Any]]] = []

    for rec in EXPANDED_RECIPE_CATALOG:
        rid = str(rec.get("recipe_id") or "")
        fb_action = fb_by_id.get(rid)
        if fb_action == "hide":
            continue
        if not _passes_dietary_filter(rec, diet_type, restrictions):
            continue
        title_low = rec["title"].lower()
        if title_low in disliked_low:
            continue

        total_time = int(rec.get("prep_minutes", 10)) + int(rec.get("cook_minutes") or 0)
        if max_prep is not None and int(rec.get("prep_minutes", 10)) > int(max_prep):
            continue

        reconciled = []
        for ing in rec["ingredients"]:
            r_ing = reconcile_ingredient(
                ing,
                matching_inventory,
                is_optional=bool(ing.get("is_optional", False)),
            )
            if r_ing is not None:
                reconciled.append(r_ing)

        essential_ings = [r for r in reconciled if r["essential"]]
        matched_essential = [r for r in essential_ings if r["status"] != "missing"]
        if not matched_essential:
            continue

        rec_type = classify_recommendation_type(reconciled, [], matching_inventory)
        avail_essential = [r for r in essential_ings if r["status"] == "available"]
        match_ratio = len(avail_essential) / max(1, len(essential_ings))
        partial_ratio = len(matched_essential) / max(1, len(essential_ings))
        expiring_bonus = 0.15 if any(r.get("expiring_soon") for r in matched_essential) else 0.0

        meal_types = [m.lower() for m in rec.get("meal_types", [])]
        primary_meal = str(rec.get("primary_meal") or "").lower()
        if meal_ctx.meal_window == primary_meal:
            meal_score = 0.42
        elif meal_ctx.meal_window in meal_types or meal_ctx.meal_window == "flexible":
            meal_score = 0.15
        else:
            meal_score = -0.30

        cuisine_low = str(rec.get("cuisine") or "").lower()
        pref_score = 0.0
        if pref_cuisines and cuisine_low in pref_cuisines:
            pref_score += 0.15
        if pref_meals and (set(meal_types) & pref_meals):
            pref_score += 0.10

        season_tags = set(rec.get("season_tags") or [])
        season_score = 0.12 if season_ctx.season in season_tags else 0.0
        time_score = 0.08 if total_time <= 25 else 0.0
        repeat_penalty = -0.35 if title_low in recent_low else 0.0

        fb_score = 0.0
        if fb_action == "like":
            fb_score = 0.20
        elif fb_action == "dislike":
            fb_score = -0.80
        elif fb_action == "cooked":
            fb_score = -0.30

        total_score = (
            (match_ratio * 0.40)
            + (partial_ratio * 0.15)
            + expiring_bonus
            + meal_score
            + pref_score
            + season_score
            + time_score
            + repeat_penalty
            + fb_score
        )

        # Build concise user-facing reason without exposing internal scores
        reasons: list[str] = []
        if meal_ctx.meal_window == primary_meal or meal_ctx.meal_window in meal_types:
            reasons.append(f"Great for {meal_ctx.meal_window}")
        if rec_type == "COOK_NOW":
            reasons.append("all essential ingredients in pantry")
        elif rec_type == "COOK_WITH_SUBSTITUTIONS":
            missing_cnt = len(essential_ings) - len(avail_essential)
            reasons.append(
                f"only {missing_cnt} ingredient{'s' if missing_cnt != 1 else ''} missing or partial"
            )
        if any(r.get("expiring_soon") for r in matched_essential):
            reasons.append("uses items expiring soon")

        out_rec = dict(rec)
        out_rec["meal_type"] = (
            meal_ctx.meal_window
            if meal_ctx.meal_window in meal_types
            else (meal_types[0] if meal_types else None)
        )
        out_rec["recommendation_type"] = rec_type
        out_rec["why_recommended"] = (
            " · ".join(reasons).capitalize() if reasons else "Matches your pantry"
        )
        out_rec["_score"] = total_score
        scored.append((total_score, out_rec))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Enforce cuisine AND dish_family diversity while picking top `limit`
    selected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_cuisines: dict[str, int] = {}
    seen_families: dict[str, int] = {}
    for _, rec in scored:
        rid = str(rec.get("recipe_id") or "")
        c = str(rec.get("cuisine") or "Other")
        fam = str(rec.get("dish_family") or rid)
        if seen_families.get(fam, 0) >= 1 and len(scored) > limit:
            continue
        if seen_cuisines.get(c, 0) >= 2 and len(scored) > limit:
            continue
        seen_ids.add(rid)
        seen_cuisines[c] = seen_cuisines.get(c, 0) + 1
        seen_families[fam] = seen_families.get(fam, 0) + 1
        selected.append(rec)
        if len(selected) >= limit:
            break

    # Backfill if diversity filters left fewer than `limit`
    if len(selected) < limit:
        for _, rec in scored:
            rid = str(rec.get("recipe_id") or "")
            if rid in seen_ids:
                continue
            seen_ids.add(rid)
            selected.append(rec)
            if len(selected) >= limit:
                break

    return selected
