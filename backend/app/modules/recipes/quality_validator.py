"""Recipe instruction quality and ingredient-consistency validator (Phase 7.6)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_VAGUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bprepare\s+spiced\s+tadka\b", re.IGNORECASE),
    re.compile(r"\bcook\s+until\s+done\b", re.IGNORECASE),
    re.compile(r"\badd\s+seasoning\b", re.IGNORECASE),
    re.compile(r"\bprepare\s+the\s+gravy\b", re.IGNORECASE),
    re.compile(r"\bmix\s+everything\s+and\s+cook\b", re.IGNORECASE),
    re.compile(r"\bcook\s+as\s+usual\b", re.IGNORECASE),
    re.compile(r"\bserve\s+hot\s+with\s+fresh\s+rotis?\b", re.IGNORECASE),
)

# Distinct culinary ingredients that must NOT be invented in instructions if absent from
# recipe["ingredients"].
_TRACKED_CULINARY_INGREDIENTS: dict[str, tuple[str, ...]] = {
    "cumin": ("cumin", "jeera"),
    "mustard": ("mustard", "rai"),
    "turmeric": ("turmeric", "haldi"),
    "ginger": ("ginger", "adrak"),
    "garlic": ("garlic", "lahsun"),
    "green chilli": ("green chilli", "green chili", "hari mirch"),
    "hing": ("hing", "asafoetida"),
    "garam masala": ("garam masala",),
    "curry leaves": ("curry leaves", "kadi patta"),
    "cardamom": ("cardamom", "elaichi"),
    "cinnamon": ("cinnamon", "dalchini"),
    "cloves": ("cloves", "laung"),
    "paneer": ("paneer",),
    "tofu": ("tofu",),
    "onion": ("onion", "pyaz"),
    "tomato": ("tomato", "tamatar"),
    "potato": ("potato", "aloo"),
    "spinach": ("spinach", "palak"),
    "peas": ("peas", "matar"),
    "cauliflower": ("cauliflower", "gobi"),
    "carrot": ("carrot", "gajar"),
    "capsicum": ("capsicum", "bell pepper", "shimla"),
    "peanuts": ("peanut", "peanuts", "moongphali"),
    "almonds": ("almond", "almonds", "badam"),
    "cashews": ("cashew", "cashews", "kaju"),
    "saffron": ("saffron", "kesar"),
    "coconut": ("coconut", "nariyal"),
    "tamarind": ("tamarind", "imli"),
    "curd": ("curd", "yogurt", "dahi"),
    "milk": ("milk", "doodh"),
    "butter": ("butter", "makhan"),
    "ghee": ("ghee",),
    "jaggery": ("jaggery", "gud"),
    "sugar": ("sugar", "cheeni"),
    "lemon": ("lemon", "lime", "nimbu"),
    "coriander": ("coriander", "dhania", "cilantro"),
    "egg": ("egg", "eggs", "anda"),
    "chicken": ("chicken",),
}


@dataclass
class RecipeQualityResult:
    recipe_id: str
    title: str
    is_valid: bool
    issues: list[str] = field(default_factory=list)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", text.lower())).strip()


def _ingredient_matches_token(ing_name: str, token_aliases: tuple[str, ...]) -> bool:
    norm = _normalize_text(ing_name)
    return any(alias in norm for alias in token_aliases)


def _is_multi_component(recipe: dict[str, Any]) -> bool:
    family = str(recipe.get("dish_family") or "")
    return family in {
        "roti_dal",
        "rice_dal",
        "curry_rice",
        "sabzi_roti",
        "roti_sabzi",
        "dal_rice",
        "curry_roti",
        "idli_dosa_combo",
    }


def validate_recipe_quality(recipe: dict[str, Any]) -> RecipeQualityResult:
    recipe_id = str(recipe.get("recipe_id") or "")
    title = str(recipe.get("title") or "")
    ingredients = list(recipe.get("ingredients") or [])
    structured_steps = list(recipe.get("structured_steps") or [])
    plain_steps = list(recipe.get("steps") or recipe.get("instructions") or [])
    issues: list[str] = []

    is_multi = _is_multi_component(recipe)
    min_steps = 4 if is_multi else 3

    if structured_steps:
        if len(structured_steps) < min_steps:
            issues.append(
                f"Expected at least {min_steps} structured steps for "
                f"{'multi-component ' if is_multi else ''}recipe, found "
                f"{len(structured_steps)}."
            )
        step_texts: list[str] = []
        used_ing_names: set[str] = set()
        for idx, st in enumerate(structured_steps, start=1):
            st_title = str(st.get("title") or "").strip()
            st_inst = str(st.get("instruction") or "").strip()
            st_cue = str(st.get("completion_cue") or "").strip()
            st_ings = [str(x).strip() for x in (st.get("ingredients_used") or []) if str(x).strip()]
            if not st_title:
                issues.append(f"Step {idx} is missing a descriptive title.")
            if len(st_inst) < 25:
                issues.append(f"Step {idx} instruction is too brief ({len(st_inst)} chars).")
            if len(st_cue) < 10:
                issues.append(f"Step {idx} is missing a meaningful completion cue.")
            step_texts.append(f"{st_title} {st_inst} {st_cue}")
            for x in st_ings:
                used_ing_names.add(_normalize_text(x))
        combined_steps_text = " ".join(step_texts)
    else:
        issues.append("Missing structured_steps metadata.")
        if len(plain_steps) < min_steps:
            issues.append(
                f"Expected at least {min_steps} actionable steps, found {len(plain_steps)}."
            )
        combined_steps_text = " ".join(str(s) for s in plain_steps)
        used_ing_names = set()

    # Check vague phrases
    for pat in _VAGUE_PATTERNS:
        if pat.search(combined_steps_text):
            issues.append(f"Contains vague instruction phrase matching '{pat.pattern}'.")

    # Check ingredients referenced in steps exist in recipe["ingredients"]
    ing_names = [str(ing.get("name") or "") for ing in ingredients if ing.get("name")]
    norm_combined = _normalize_text(combined_steps_text)
    for key, aliases in _TRACKED_CULINARY_INGREDIENTS.items():
        mentioned = any(re.search(rf"\b{re.escape(a)}\b", norm_combined) for a in aliases)
        if not mentioned:
            continue
        present_in_recipe = any(_ingredient_matches_token(n, aliases) for n in ing_names)
        if not present_in_recipe:
            # Allow "ghee" only if recipe has "Oil" or "Butter" as optional fat fallback? No, keep
            # strict unless "oil or ghee" isn't used.
            issues.append(f"Step references '{key}' which is not listed in recipe ingredients.")

    # Check every essential ingredient is used in at least one step
    for ing in ingredients:
        if ing.get("is_optional"):
            continue
        name = str(ing.get("name") or "").strip()
        if not name:
            continue
        norm_name = _normalize_text(name)
        tokens = [t for t in norm_name.split() if len(t) >= 3]
        in_used_list = any(norm_name in u or u in norm_name for u in used_ing_names)
        in_text = norm_name in norm_combined or any(
            re.search(rf"\b{re.escape(t)}\b", norm_combined) for t in tokens
        )
        if not (in_used_list or in_text):
            issues.append(f"Essential ingredient '{name}' is not used in any cooking step.")

    # Multi-component title check: e.g. "Whole Wheat Roti & Urad Dal Tadka"
    if "&" in title:
        parts = [p.strip() for p in title.split("&", 1)]
        for part in parts:
            part_tokens = [
                t
                for t in _normalize_text(part).split()
                if len(t) >= 3
                and t not in {"with", "and", "whole", "wheat", "simple", "crispy", "fresh"}
            ]
            if part_tokens and not any(
                re.search(rf"\b{re.escape(t)}\b", norm_combined) for t in part_tokens
            ):
                issues.append(f"Multi-component recipe does not explain preparation of '{part}'.")

    return RecipeQualityResult(
        recipe_id=recipe_id,
        title=title,
        is_valid=len(issues) == 0,
        issues=issues,
    )


def audit_recipe_catalogue(
    recipes: list[dict[str, Any]],
) -> tuple[int, int, list[RecipeQualityResult]]:
    results = [validate_recipe_quality(r) for r in recipes]
    failed = [r for r in results if not r.is_valid]
    return len(results), len(failed), failed
