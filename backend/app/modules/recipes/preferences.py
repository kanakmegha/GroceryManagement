"""Confidence- and frequency-weighted household preference learning from completed meal history."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cooking.models import MealHistory

PERSONALISATION_RECIPE_THRESHOLD = 10


def _recipe_identity_key(recipe_id: str | None, recipe_title: str) -> str:
    rid = (recipe_id or "").strip().lower()
    if rid:
        return f"id:{rid}"
    return f"title:{' '.join(recipe_title.strip().lower().split())}"


async def count_distinct_completed_recipes(db: AsyncSession, household_id: UUID) -> int:
    """Count distinct recipes completed through cooking workflow (never views/generations)."""
    rows = (
        await db.execute(
            select(MealHistory.recipe_id, MealHistory.recipe_title).where(
                MealHistory.household_id == household_id
            )
        )
    ).all()
    keys = {
        _recipe_identity_key(rid, title)
        for rid, title in rows
        if (rid and rid.strip()) or (title and title.strip())
    }
    return len(keys)


async def compute_household_preference_profile(
    db: AsyncSession, household_id: UUID
) -> dict[str, Any]:
    """Derive a household-specific culinary preference profile from completed meals and feedback.

    Invariants:
    - Never infers strong ingredient/cuisine preferences from a single rating.
    - Explicit user dislikes (rating <= 2 or would_make_again=False) and explicit wants
      (would_make_again=True / rating == 5) take precedence over weak inferred patterns.
    """
    rows = (
        (
            await db.execute(
                select(MealHistory)
                .where(MealHistory.household_id == household_id)
                .order_by(MealHistory.cooked_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )

    if not rows:
        return {
            "total_completed_meals": 0,
            "distinct_completed_recipes": 0,
            "frequently_cooked": [],
            "favourite_dishes": [],
            "disliked_dishes": [],
            "want_to_cook_again": [],
            "preferred_cuisines": [],
            "explored_cuisines": [],
            "cooked_dishes_history": [],
            "preferred_meal_types": [],
            "favourite_ingredients": [],
            "disliked_ingredients": [],
            "repeated_substitutions": [],
            "taste_and_time_signals": [],
            "preferred_max_cook_minutes": None,
            "recent_completed_meals": [],
        }

    dish_counts: Counter[str] = Counter()
    dish_ratings: dict[str, list[int]] = defaultdict(list)
    cuisine_pos_counts: Counter[str] = Counter()
    meal_type_counts: Counter[str] = Counter()
    ing_pos_counts: Counter[str] = Counter()
    ing_neg_counts: Counter[str] = Counter()
    sub_counts: Counter[str] = Counter()
    tag_counts: Counter[str] = Counter()

    want_again: list[str] = []
    disliked_dishes: list[str] = []
    positive_cook_times: list[int] = []

    distinct_keys: set[str] = set()
    explored_cuisines_set: list[str] = []
    cooked_titles_set: list[str] = []

    for r in rows:
        title = r.recipe_title.strip()
        distinct_keys.add(_recipe_identity_key(r.recipe_id, title))
        if title and title not in cooked_titles_set:
            cooked_titles_set.append(title)
        if r.cuisine and r.cuisine.strip() and r.cuisine.strip() not in explored_cuisines_set:
            explored_cuisines_set.append(r.cuisine.strip())
        dish_counts[title] += 1
        if r.rating is not None:
            dish_ratings[title].append(int(r.rating))
        if r.meal_type:
            meal_type_counts[r.meal_type.strip().lower()] += 1
        for sub in r.substitutions or []:
            if sub and str(sub).strip():
                sub_counts[str(sub).strip()] += 1
        for tag in r.feedback_tags or []:
            if tag and str(tag).strip():
                tag_counts[str(tag).strip().lower()] += 1
        if r.feedback_comment:
            c_low = r.feedback_comment.lower()
            for kw in (
                "too spicy",
                "not spicy enough",
                "too oily",
                "too time-consuming",
                "make it again",
            ):
                if kw in c_low:
                    tag_counts[kw] += 1

        is_explicit_like = (r.rating is not None and r.rating >= 4) or r.would_make_again is True
        is_explicit_dislike = (
            r.rating is not None and r.rating <= 2
        ) or r.would_make_again is False

        if r.would_make_again is True and title not in want_again:
            want_again.append(title)
        if is_explicit_dislike and title not in disliked_dishes:
            disliked_dishes.append(title)

        if is_explicit_like:
            if r.cuisine:
                cuisine_pos_counts[r.cuisine.strip()] += 1
            total_t = (r.prep_minutes or 0) + (r.cook_minutes or 0)
            if total_t > 0:
                positive_cook_times.append(total_t)
            for ing in r.ingredients_used or []:
                name = str(ing.get("name") or "").strip().lower()
                if name:
                    ing_pos_counts[name] += 1
        elif is_explicit_dislike:
            for ing in r.ingredients_used or []:
                name = str(ing.get("name") or "").strip().lower()
                if name:
                    ing_neg_counts[name] += 1

    frequently_cooked = [t for t, c in dish_counts.most_common(10) if c >= 2]
    favourite_dishes: list[dict[str, Any]] = []
    for title, count in dish_counts.most_common():
        ratings = dish_ratings.get(title, [])
        avg = (sum(ratings) / len(ratings)) if ratings else None
        if (avg is not None and avg >= 4.0 and count >= 2) or (avg == 5.0) or (title in want_again):
            confidence = "high" if count >= 2 else "explicit"
            favourite_dishes.append(
                {
                    "title": title,
                    "cook_count": count,
                    "avg_rating": round(avg, 2) if avg is not None else None,
                    "confidence": confidence,
                }
            )

    # Require >= 2 occurrences to infer ingredient or cuisine preference
    preferred_cuisines = [c for c, n in cuisine_pos_counts.most_common(5) if n >= 2]
    preferred_meal_types = [m for m, n in meal_type_counts.most_common(4) if n >= 2]
    favourite_ingredients = [i for i, n in ing_pos_counts.most_common(10) if n >= 2]
    disliked_ingredients = [
        i for i, n in ing_neg_counts.most_common(10) if n >= 2 and ing_pos_counts[i] < n
    ]
    repeated_substitutions = [s for s, n in sub_counts.most_common(5) if n >= 2]

    signals: list[str] = []
    if tag_counts["too spicy"] >= 1:
        signals.append("prefers_less_spicy")
    if tag_counts["not spicy enough"] >= 1:
        signals.append("prefers_more_spicy")
    if tag_counts["too oily"] >= 1:
        signals.append("prefers_less_oil")
    if tag_counts["too time-consuming"] >= 1:
        signals.append("prefers_quick_recipes")

    preferred_max_cook_minutes: int | None = None
    if "prefers_quick_recipes" in signals:
        preferred_max_cook_minutes = 25
    elif len(positive_cook_times) >= 2:
        preferred_max_cook_minutes = round(sum(positive_cook_times) / len(positive_cook_times))

    recent_completed = [
        {
            "recipe_title": r.recipe_title,
            "meal_type": r.meal_type,
            "cuisine": r.cuisine,
            "cooked_at": r.cooked_at.isoformat(),
            "rating": r.rating,
            "feedback_tags": list(r.feedback_tags or []),
            "feedback_comment": r.feedback_comment,
            "would_make_again": r.would_make_again,
        }
        for r in rows[:8]
    ]

    return {
        "total_completed_meals": len(rows),
        "distinct_completed_recipes": len(distinct_keys),
        "frequently_cooked": frequently_cooked,
        "favourite_dishes": favourite_dishes[:10],
        "disliked_dishes": disliked_dishes[:10],
        "want_to_cook_again": want_again[:10],
        "preferred_cuisines": preferred_cuisines,
        "explored_cuisines": explored_cuisines_set[:15],
        "cooked_dishes_history": cooked_titles_set[:30],
        "preferred_meal_types": preferred_meal_types,
        "favourite_ingredients": favourite_ingredients,
        "disliked_ingredients": disliked_ingredients,
        "repeated_substitutions": repeated_substitutions,
        "taste_and_time_signals": signals,
        "preferred_max_cook_minutes": preferred_max_cook_minutes,
        "recent_completed_meals": recent_completed,
    }
