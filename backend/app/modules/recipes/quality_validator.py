"""Recipe instruction quality and ingredient-consistency validator (Phase 7.6)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.modules.recipes.flatbread_and_workflows import (
    detect_dish_workflow,
    detect_flatbread_spec,
)
from app.modules.recipes.ingredient_knowledge import check_ingredient_completeness

_VAGUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bprepare\s+spiced\s+tadka\b", re.IGNORECASE),
    re.compile(r"\bcook\s+until\s+done\b", re.IGNORECASE),
    re.compile(r"\badd\s+seasoning\b", re.IGNORECASE),
    re.compile(r"\bprepare\s+the\s+gravy\b", re.IGNORECASE),
    re.compile(r"\bmix\s+everything\s+and\s+cook\b", re.IGNORECASE),
    re.compile(r"\bcook\s+as\s+usual\b", re.IGNORECASE),
    re.compile(r"\bserve\s+hot\s+with\s+fresh\s+rotis?\b", re.IGNORECASE),
    re.compile(r"\bprepare\s+the\s+dough\b", re.IGNORECASE),
    re.compile(r"\bmake\s+(?:the\s+)?(?:rotis?|chapatis?|phulkas?)\b", re.IGNORECASE),
    re.compile(r"\badd\s+spices(?:\s+to\s+taste)?\b", re.IGNORECASE),
    re.compile(r"\badd\s+salt\s+to\s+taste\b", re.IGNORECASE),
    re.compile(r"\bfry\s+properly\b", re.IGNORECASE),
    re.compile(r"\bcook\s+as\s+required\b", re.IGNORECASE),
)

_ALLOWED_HEAT_LEVELS = {"low", "medium-low", "medium", "medium-high", "high"}

_ACTIVE_COOKING_VERB_RE = re.compile(
    r"\b(?:heat|cook|simmer|saut[eé]|roast|fry|boil|sear|temper|toast|puff|bake|steam|pressure)\b",
    re.IGNORECASE,
)

_MEANINGFUL_ACTION_VERB_RE = re.compile(
    r"\b(?:heat|cook|simmer|saut[eé]|roast|fry|boil|sear|temper|toast|puff|bake|steam|pressure|"
    r"mix|stir|fold|pour|top|garnish|serve|toss|drizzle|sprinkle|mash|whisk|drain|rinse|soak|"
    r"blend|slice|chop|peel|cool|rest|layer|spread|flip|grind)\b",
    re.IGNORECASE,
)


def _is_meaningful_concise_instruction(text: str) -> bool:
    """Allow concise 12-24 char steps only with a concrete culinary action and context."""
    stripped = text.strip()
    if len(stripped) >= 25:
        return True
    if len(stripped) < 12:
        return False
    if any(pat.search(stripped) for pat in _VAGUE_PATTERNS):
        return False
    words = [w for w in re.findall(r"[a-zA-Z]+", stripped) if len(w) >= 2]
    return len(words) >= 3 and bool(_MEANINGFUL_ACTION_VERB_RE.search(stripped))

_DURATION_CUE_RE = re.compile(
    r"\b\d+(?:\s*[-–]\s*\d+)?\s*(?:seconds?|secs?|minutes?|mins?|whistles?)\b",
    re.IGNORECASE,
)

_NON_MEASURABLE_QTY_RE = re.compile(
    r"^(?:as\s+needed|to\s+taste|some(?:\s+\w+)?|a\s+little|optional|needed)$",
    re.IGNORECASE,
)

_OPTIONAL_GARNISH_WORDS = ("coriander", "cilantro", "mint", "parsley", "garnish")

_SEASONING_KEYWORDS: tuple[str, ...] = (
    "salt",
    "turmeric",
    "chilli",
    "chili",
    "cumin",
    "jeera",
    "coriander",
    "dhania",
    "garam masala",
    "hing",
    "mustard",
    "pepper",
    "sugar",
    "cardamom",
    "cinnamon",
    "clove",
    "ajwain",
    "carom",
    "kasuri methi",
    "amchur",
)

_BOOKKEEPING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bconfirm\s+actual\s+quantit(?:y|ies)\b", re.IGNORECASE),
    re.compile(r"\bdeduct\s+(?:from\s+)?(?:stock|inventory|pantry)\b", re.IGNORECASE),
    re.compile(r"\bupdate\s+(?:pantry\s+)?inventory\b", re.IGNORECASE),
    re.compile(r"\badjust\s+(?:pantry|stock)\b", re.IGNORECASE),
    re.compile(r"\binventory\s+lot\b", re.IGNORECASE),
    re.compile(r"\bstock\s+ledger\b", re.IGNORECASE),
)

_NUTRITION_MACRO_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b\d+(?:\.\d+)?\s*(?:kcal|calories)\b", re.IGNORECASE),
    re.compile(
        r"\b\d+(?:\.\d+)?\s*g\s+(?:of\s+)?(?:protein|carbs|carbohydrates|fat|fiber|sugar)\b",
        re.IGNORECASE,
    ),
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


def _parse_qty_unit(text: str) -> tuple[float, str] | None:
    """Parse leading numeric/fraction/range quantity and unit from a quantity string."""
    cleaned = text.strip().lower()
    cleaned = re.sub(r"^(?:approx(?:imately)?|about|~)\s+", "", cleaned)
    # Range e.g. "90-100 ml" or "90–100 ml"
    m_range = re.match(r"^(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*([a-z]+)", cleaned)
    if m_range:
        return float(m_range.group(2)), m_range.group(3)
    # Fraction e.g. "1/2 tsp"
    m_frac = re.match(r"^(\d+)\s*/\s*(\d+)\s*([a-z]+)", cleaned)
    if m_frac and int(m_frac.group(2)) > 0:
        return int(m_frac.group(1)) / int(m_frac.group(2)), m_frac.group(3)
    # Decimal / int e.g. "150 g"
    m_num = re.match(r"^(\d+(?:\.\d+)?)\s*([a-z]+)", cleaned)
    if m_num:
        return float(m_num.group(1)), m_num.group(2)
    return None


def _is_seasoning_name(name: str) -> bool:
    norm = _normalize_text(name)
    return any(kw in norm for kw in _SEASONING_KEYWORDS)


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


def validate_recipe_quality(
    recipe: dict[str, Any], *, strict_ai_generated: bool = False
) -> RecipeQualityResult:
    recipe_id = str(recipe.get("recipe_id") or "")
    title = str(recipe.get("title") or "").strip()
    ingredients = list(recipe.get("ingredients") or [])
    structured_steps = list(recipe.get("structured_steps") or [])
    plain_steps = list(recipe.get("steps") or recipe.get("instructions") or [])
    issues: list[str] = []

    if not title:
        issues.append("Recipe is missing a title.")

    servings = recipe.get("servings")
    if servings is not None:
        try:
            if int(servings) < 1:
                issues.append("Recipe servings must be at least 1.")
        except (TypeError, ValueError):
            issues.append("Recipe servings must be a valid integer.")
    elif strict_ai_generated:
        issues.append("Recipe is missing servings count.")

    prep_minutes = recipe.get("prep_minutes")
    if prep_minutes is not None:
        try:
            if int(prep_minutes) < 1:
                issues.append("Recipe prep_minutes must be at least 1.")
        except (TypeError, ValueError):
            issues.append("Recipe prep_minutes must be a valid integer.")
    elif strict_ai_generated:
        issues.append("Recipe is missing prep_minutes.")

    if strict_ai_generated:
        cook_minutes = recipe.get("cook_minutes")
        if cook_minutes is None:
            issues.append("Recipe is missing cook_minutes.")
        else:
            try:
                if int(cook_minutes) < 0:
                    issues.append("Recipe cook_minutes cannot be negative.")
            except (TypeError, ValueError):
                issues.append("Recipe cook_minutes must be a valid integer.")
        difficulty = str(recipe.get("difficulty") or "").strip().lower()
        if difficulty not in {"easy", "medium", "hard"}:
            issues.append("Recipe difficulty must be 'easy', 'medium', or 'hard'.")

    is_multi = _is_multi_component(recipe)
    workflow_spec = detect_dish_workflow(recipe)
    if is_multi:
        min_steps = 4
    elif workflow_spec is not None and workflow_spec.category == "smoothie_beverage":
        min_steps = 2
    else:
        min_steps = 3

    if structured_steps:
        if len(structured_steps) < min_steps:
            issues.append(
                f"Expected at least {min_steps} structured steps for "
                f"{'multi-component ' if is_multi else ''}recipe, found "
                f"{len(structured_steps)}."
            )
        step_texts: list[str] = []
        used_ing_names: set[str] = set()
        step_qty_totals: dict[str, tuple[float, str]] = {}
        has_heat_or_duration = False
        for idx, st in enumerate(structured_steps, start=1):
            st_num = st.get("step_number")
            if st_num is not None and int(st_num) != idx:
                issues.append(
                    f"Structured step {idx} has out-of-order step_number {st_num}."
                )
            st_title = str(st.get("title") or "").strip()
            st_inst = str(st.get("instruction") or "").strip()
            st_cue = str(st.get("completion_cue") or "").strip()
            st_ings = [str(x).strip() for x in (st.get("ingredients_used") or []) if str(x).strip()]
            raw_heat = str(st.get("heat_level") or "").strip().lower()
            raw_dur = st.get("duration_minutes")
            if raw_heat or (raw_dur is not None and int(raw_dur or 0) > 0):
                has_heat_or_duration = True
            step_verb_text = re.sub(r"\boff[-\s]+heat\b", "", f"{st_title} {st_inst}", flags=re.I)
            if strict_ai_generated and _ACTIVE_COOKING_VERB_RE.search(step_verb_text):
                if raw_heat and raw_heat not in _ALLOWED_HEAT_LEVELS:
                    issues.append(
                        f"Step {idx} has invalid heat_level '{raw_heat}'; "
                        "expected low, medium-low, medium, medium-high, or high."
                    )
                if not raw_heat:
                    issues.append(f"Cooking step {idx} is missing heat_level.")
                has_dur = (raw_dur is not None and int(raw_dur or 0) > 0) or bool(
                    _DURATION_CUE_RE.search(st_inst)
                )
                if not has_dur:
                    issues.append(f"Cooking step {idx} is missing approximate duration.")
            if not st_title:
                issues.append(f"Step {idx} is missing a descriptive title.")
            if not _is_meaningful_concise_instruction(st_inst):
                issues.append(f"Step {idx} instruction is too brief ({len(st_inst)} chars).")
            if len(st_cue) < 10:
                issues.append(f"Step {idx} is missing a meaningful completion cue.")
            step_texts.append(f"{st_title} {st_inst} {st_cue}")
            for x in st_ings:
                used_ing_names.add(_normalize_text(x))
            for q_name, q_val in (st.get("ingredient_quantities") or {}).items():
                q_str = str(q_val).strip()
                norm_q_name = _normalize_text(str(q_name))
                if strict_ai_generated:
                    is_garnish = any(gw in norm_q_name for gw in _OPTIONAL_GARNISH_WORDS)
                    if not is_garnish and (
                        _NON_MEASURABLE_QTY_RE.match(q_str) or not re.search(r"\d", q_str)
                    ):
                        issues.append(
                            f"Step {idx} has non-measurable quantity '{q_str}' for "
                            f"essential ingredient '{q_name}'."
                        )
                parsed_q = _parse_qty_unit(q_str)
                if parsed_q:
                    prev_q = step_qty_totals.get(norm_q_name)
                    if prev_q is None:
                        step_qty_totals[norm_q_name] = parsed_q
                    elif prev_q[1] == parsed_q[1]:
                        # Use max per step because an ingredient may be carried over
                        # from cooking to simmering/plating steps.
                        step_qty_totals[norm_q_name] = (max(prev_q[0], parsed_q[0]), prev_q[1])
        if strict_ai_generated and not has_heat_or_duration:
            issues.append("Cooking steps must include heat_level or duration_minutes metadata.")
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

    # Reject inventory bookkeeping in cooking instructions
    for pat in _BOOKKEEPING_PATTERNS:
        if pat.search(combined_steps_text):
            issues.append("Cooking steps must not contain inventory bookkeeping instructions.")
            break

    # Reject fabricated exact nutrition macros
    macro_check_text = " ".join(
        [
            combined_steps_text,
            str(recipe.get("description") or ""),
            str(recipe.get("summary") or ""),
            " ".join(str(t) for t in (recipe.get("cooking_tips") or [])),
        ]
    )
    for pat in _NUTRITION_MACRO_PATTERNS:
        if pat.search(macro_check_text):
            issues.append("Recipe must not fabricate exact nutritional macro or calorie values.")
            break

    if strict_ai_generated:
        for ing in ingredients:
            if ing.get("is_optional"):
                continue
            ing_name = str(ing.get("name") or "unknown").strip()
            qty_val = ing.get("quantity") or ing.get("quantity_text") or ing.get("qty")
            if qty_val is None or not str(qty_val).strip():
                issues.append(
                    f"Essential ingredient '{ing_name}' is missing quantity/unit."
                )
            elif _NON_MEASURABLE_QTY_RE.match(str(qty_val).strip()) or not re.search(
                r"\d", str(qty_val)
            ):
                kind_label = "Seasoning" if _is_seasoning_name(ing_name) else "Essential ingredient"
                issues.append(
                    f"{kind_label} '{ing_name}' has non-measurable quantity "
                    f"'{str(qty_val).strip()}'."
                )
            elif _is_seasoning_name(ing_name) and not re.search(r"\d", str(qty_val)):
                issues.append(
                    f"Seasoning '{ing_name}' must have a measurable starting quantity."
                )

            # Quantity consistency check between ingredient list and structured_steps
            if structured_steps and qty_val is not None:
                parsed_ing = _parse_qty_unit(str(qty_val))
                norm_ing = _normalize_text(ing_name)
                if parsed_ing and norm_ing in step_qty_totals:
                    step_amt, step_unit = step_qty_totals[norm_ing]
                    if step_unit == parsed_ing[1] and step_amt > parsed_ing[0] * 1.05 + 1e-6:
                        issues.append(
                            f"Step quantities for '{ing_name}' ({step_amt:g} {step_unit}) "
                            f"exceed ingredient list ({parsed_ing[0]:g} {parsed_ing[1]})."
                        )

        # Flatbread differentiation & dough preparation check
        prep_text = " ".join(str(p) for p in (recipe.get("preparation_steps") or []))
        full_exec_text = _normalize_text(f"{prep_text} {combined_steps_text}")
        flatbread_spec = detect_flatbread_spec(recipe)
        if flatbread_spec is not None:
            if not any(cue in full_exec_text for cue in flatbread_spec.dough_cues):
                issues.append(
                    f"Flatbread '{flatbread_spec.label}' must explain dough preparation "
                    f"(e.g. {', '.join(flatbread_spec.dough_cues)})."
                )
            if not any(cue in full_exec_text for cue in flatbread_spec.shaping_cues):
                issues.append(
                    f"Flatbread '{flatbread_spec.label}' must explain shaping/rolling technique "
                    f"(e.g. {', '.join(flatbread_spec.shaping_cues)})."
                )
            cook_hits = sum(1 for cue in flatbread_spec.cooking_cues if cue in full_exec_text)
            if cook_hits < 2:
                issues.append(
                    f"Flatbread '{flatbread_spec.label}' must include its specific cooking "
                    f"technique (expected cues from {', '.join(flatbread_spec.cooking_cues)})."
                )

        # Dish-category workflow check
        if workflow_spec is not None:
            for stage_name, stage_cues in workflow_spec.stage_groups:
                if not any(cue in full_exec_text for cue in stage_cues):
                    issues.append(
                        f"{workflow_spec.category} recipe is missing '{stage_name}' workflow stage."
                    )

        # Dish & variation ingredient completeness check
        issues.extend(check_ingredient_completeness(recipe))

    # Check ingredients referenced in steps exist in recipe["ingredients"]
    ing_match_candidates: list[list[str]] = []
    for ing in ingredients:
        candidates: list[str] = []
        if ing.get("name"):
            candidates.append(str(ing["name"]))
        if ing.get("canonical_name"):
            candidates.append(str(ing["canonical_name"]))
        if ing.get("canonical_ingredient_name"):
            candidates.append(str(ing["canonical_ingredient_name"]))
        for al in ing.get("confirmed_aliases") or []:
            if al:
                candidates.append(str(al))
        if candidates:
            ing_match_candidates.append(candidates)

    norm_combined = _normalize_text(combined_steps_text)
    for key, aliases in _TRACKED_CULINARY_INGREDIENTS.items():
        if key == "cloves":
            # Avoid false positive on "2 cloves garlic" / "garlic cloves"
            cleaned_for_cloves = re.sub(
                r"\b(?:\d+\s+cloves?\s+(?:of\s+)?(?:minced\s+|crushed\s+)?garlic|garlic\s+cloves?)\b",
                "",
                norm_combined,
            )
            mentioned = any(
                re.search(rf"\b{re.escape(a)}\b", cleaned_for_cloves) for a in aliases
            )
        else:
            mentioned = any(re.search(rf"\b{re.escape(a)}\b", norm_combined) for a in aliases)
        if not mentioned:
            continue
        present_in_recipe = any(
            _ingredient_matches_token(cand, aliases)
            for cand_list in ing_match_candidates
            for cand in cand_list
        )
        if not present_in_recipe:
            issues.append(f"Step references '{key}' which is not listed in recipe ingredients.")

    # Check every essential ingredient is used in at least one step
    for ing in ingredients:
        if ing.get("is_optional"):
            continue
        name = str(ing.get("name") or "").strip()
        if not name:
            continue
        cand_names = [name]
        if ing.get("canonical_name"):
            cand_names.append(str(ing["canonical_name"]))
        if ing.get("canonical_ingredient_name"):
            cand_names.append(str(ing["canonical_ingredient_name"]))
        for al in ing.get("confirmed_aliases") or []:
            if al:
                cand_names.append(str(al))

        matched_step = False
        for cand in cand_names:
            norm_cand = _normalize_text(cand)
            tokens = [t for t in norm_cand.split() if len(t) >= 3]
            in_used_list = any(norm_cand in u or u in norm_cand for u in used_ing_names)
            in_text = norm_cand in norm_combined or any(
                re.search(rf"\b{re.escape(t)}\b", norm_combined) for t in tokens
            )
            if in_used_list or in_text:
                matched_step = True
                break
        if not matched_step:
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
                re.search(rf"\b{re.escape(t)}(?:e?s)?\b", norm_combined) for t in part_tokens
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
