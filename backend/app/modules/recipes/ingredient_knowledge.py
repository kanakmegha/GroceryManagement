"""Structured dish & variation ingredient completeness knowledge layer."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

RequirementKind = Literal["essential", "conditional", "optional"]


@dataclass(frozen=True)
class IngredientRequirement:
    label: str
    aliases: tuple[str, ...]
    kind: RequirementKind = "essential"
    condition_hint: str | None = None
    condition_keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class DishIngredientProfile:
    key: str
    label: str
    title_patterns: tuple[str, ...]
    requirements: tuple[IngredientRequirement, ...]


_FAT_ALIASES = ("oil", "olive oil", "vegetable oil", "ghee", "butter")
_DAL_ALIASES = ("toor dal", "moong dal", "masoor dal", "chana dal", "urad dal", "dal", "lentil")
_WHEAT_FLOUR_ALIASES = ("whole wheat flour", "atta", "wheat flour")
_CHILLI_ALIASES = ("red chilli", "chilli powder", "chili powder", "green chilli", "green chili")

DISH_INGREDIENT_PROFILES: tuple[DishIngredientProfile, ...] = (
    DishIngredientProfile(
        key="aloo_matar_sabzi",
        label="Aloo Matar Sabzi",
        title_patterns=(r"\baloo\s+matar\b",),
        requirements=(
            IngredientRequirement("potato", ("potato", "potatoes", "aloo"), "essential"),
            IngredientRequirement("green peas", ("green peas", "peas", "matar"), "essential"),
            IngredientRequirement("cooking fat (oil or ghee)", _FAT_ALIASES, "essential"),
            IngredientRequirement("cumin seeds", ("cumin", "jeera"), "essential"),
            IngredientRequirement("salt", ("salt", "namak"), "essential"),
            IngredientRequirement("turmeric powder", ("turmeric", "haldi"), "essential"),
            IngredientRequirement("coriander powder", ("coriander", "dhania"), "optional"),
            IngredientRequirement("red chilli powder", _CHILLI_ALIASES, "optional"),
            IngredientRequirement(
                "water",
                ("water",),
                "conditional",
                condition_hint="gravy or covered simmer variation",
                condition_keywords=("gravy", "curry", "simmer", "cover"),
            ),
        ),
    ),
    DishIngredientProfile(
        key="dry_potato_sabzi",
        label="Dry Potato Sabzi",
        title_patterns=(r"\baloo\b.*\bsabzi\b", r"\bpotato\b.*\bsabzi\b"),
        requirements=(
            IngredientRequirement("potato", ("potato", "potatoes", "aloo"), "essential"),
            IngredientRequirement("cooking fat (oil or ghee)", _FAT_ALIASES, "essential"),
            IngredientRequirement("cumin seeds", ("cumin", "jeera"), "essential"),
            IngredientRequirement("salt", ("salt", "namak"), "essential"),
            IngredientRequirement("turmeric powder", ("turmeric", "haldi"), "essential"),
            IngredientRequirement("red chilli powder", _CHILLI_ALIASES, "optional"),
        ),
    ),
    DishIngredientProfile(
        key="chilla_pancake",
        label="Chilla / Savoury Pancake",
        title_patterns=(r"\b(?:chillas?|cheelas?|pancakes?)\b",),
        requirements=(
            IngredientRequirement(
                "batter base (dal or besan/gram flour)",
                _DAL_ALIASES
                + ("besan", "gram flour", "chickpea flour", "oats", "semolina", "sooji"),
                "essential",
            ),
            IngredientRequirement("water", ("water",), "essential"),
            IngredientRequirement("salt", ("salt", "namak"), "essential"),
            IngredientRequirement("cooking fat (oil or ghee)", _FAT_ALIASES, "essential"),
        ),
    ),
    DishIngredientProfile(
        key="salad",
        label="Salad / Kosambari",
        title_patterns=(r"\b(?:salads?|kosambari|kachumber)\b",),
        requirements=(
            IngredientRequirement("salt", ("salt", "namak"), "essential"),
            IngredientRequirement(
                "lemon or dressing", ("lemon", "lime", "nimbu", "oil", "vinegar"), "optional"
            ),
        ),
    ),
    DishIngredientProfile(
        key="stuffed_paratha",
        label="Stuffed Paratha",
        title_patterns=(
            r"\b(?:stuffed|aloo|paneer|gobhi|gobi|mooli|dal|moong\s+dal)\s+paratha\b",
            r"\bstuffed\s+paratha\b",
        ),
        requirements=(
            IngredientRequirement("whole wheat flour", _WHEAT_FLOUR_ALIASES, "essential"),
            IngredientRequirement("water", ("water",), "essential"),
            IngredientRequirement("salt", ("salt", "namak"), "essential"),
            IngredientRequirement("ghee or oil", _FAT_ALIASES, "essential"),
            IngredientRequirement(
                "stuffing base (potato, paneer, cauliflower, radish, or dal)",
                (
                    "potato",
                    "aloo",
                    "paneer",
                    "cauliflower",
                    "gobhi",
                    "gobi",
                    "radish",
                    "mooli",
                    "onion",
                    "methi",
                    "fenugreek",
                    "spinach",
                    "palak",
                    "sattu",
                )
                + _DAL_ALIASES,
                "essential",
            ),
        ),
    ),
    DishIngredientProfile(
        key="lachha_paratha",
        label="Lachha Paratha",
        title_patterns=(r"\blach?ha\s+paratha\b", r"\blayered\s+paratha\b"),
        requirements=(
            IngredientRequirement("whole wheat flour", _WHEAT_FLOUR_ALIASES, "essential"),
            IngredientRequirement("water", ("water",), "essential"),
            IngredientRequirement("salt", ("salt", "namak"), "essential"),
            IngredientRequirement("ghee or oil", _FAT_ALIASES, "essential"),
        ),
    ),
    DishIngredientProfile(
        key="plain_paratha",
        label="Plain Paratha",
        title_patterns=(r"\b(?:plain|triangle|square)\s+paratha\b", r"\bparatha\b"),
        requirements=(
            IngredientRequirement("whole wheat flour", _WHEAT_FLOUR_ALIASES, "essential"),
            IngredientRequirement("water", ("water",), "essential"),
            IngredientRequirement("salt", ("salt", "namak"), "optional"),
            IngredientRequirement("ghee or oil", _FAT_ALIASES, "essential"),
        ),
    ),
    DishIngredientProfile(
        key="phulka",
        label="Phulka",
        title_patterns=(r"\bphulka\b",),
        requirements=(
            IngredientRequirement("whole wheat flour", _WHEAT_FLOUR_ALIASES, "essential"),
            IngredientRequirement("water", ("water",), "essential"),
            IngredientRequirement("salt", ("salt", "namak"), "optional"),
            IngredientRequirement("ghee", ("ghee",), "optional"),
        ),
    ),
    DishIngredientProfile(
        key="plain_chapati",
        label="Plain Chapati",
        title_patterns=(r"\bplain\b.*\bchapati\b", r"\bchapati\b", r"\bwhole\s+wheat\s+roti\b"),
        requirements=(
            IngredientRequirement("whole wheat flour", _WHEAT_FLOUR_ALIASES, "essential"),
            IngredientRequirement("water", ("water",), "essential"),
            IngredientRequirement("salt", ("salt", "namak"), "optional"),
            IngredientRequirement("ghee or oil", _FAT_ALIASES, "optional"),
        ),
    ),
    DishIngredientProfile(
        key="dal_tadka",
        label="Dal Tadka",
        title_patterns=(r"\bdal\s+tadka\b", r"\btadka\s+dal\b", r"\bdal\b"),
        requirements=(
            IngredientRequirement("specific dal variety", _DAL_ALIASES, "essential"),
            IngredientRequirement(
                "water",
                ("water",),
                "conditional",
                "boiling/pressure cooking",
                ("pressure", "boil", "water", "cook"),
            ),
            IngredientRequirement("salt", ("salt", "namak"), "essential"),
            IngredientRequirement("turmeric", ("turmeric", "haldi"), "essential"),
            IngredientRequirement("oil or ghee", _FAT_ALIASES, "essential"),
            IngredientRequirement(
                "tempering spice (cumin or mustard seeds)",
                ("cumin", "jeera", "mustard", "rai"),
                "essential",
            ),
            IngredientRequirement(
                "onion or tomato or garlic", ("onion", "tomato", "garlic"), "optional"
            ),
        ),
    ),
    DishIngredientProfile(
        key="paneer_curry",
        label="Paneer Curry",
        title_patterns=(
            r"\bpaneer\s+(?:masala|curry|butter|kadai|makhani)\b",
            r"\bmatar\s+paneer\b",
            r"\bpalak\s+paneer\b",
        ),
        requirements=(
            IngredientRequirement("paneer", ("paneer",), "essential"),
            IngredientRequirement("cooking fat (oil, ghee, or butter)", _FAT_ALIASES, "essential"),
            IngredientRequirement(
                "tomato or onion gravy base", ("tomato", "onion", "spinach", "palak"), "essential"
            ),
            IngredientRequirement("salt", ("salt", "namak"), "essential"),
            IngredientRequirement(
                "warm spices (turmeric, coriander, or garam masala)",
                ("turmeric", "coriander", "garam masala", "cumin"),
                "essential",
            ),
            IngredientRequirement("red chilli powder", _CHILLI_ALIASES, "optional"),
        ),
    ),
    DishIngredientProfile(
        key="missi_roti",
        label="Missi Roti",
        title_patterns=(r"\bmissi\s+roti\b",),
        requirements=(
            IngredientRequirement(
                "besan (gram flour)", ("besan", "gram flour", "chickpea flour"), "essential"
            ),
            IngredientRequirement("whole wheat flour", _WHEAT_FLOUR_ALIASES, "essential"),
            IngredientRequirement("water", ("water",), "essential"),
            IngredientRequirement("salt", ("salt", "namak"), "essential"),
            IngredientRequirement(
                "spices (ajwain, turmeric, or cumin)",
                ("ajwain", "carom", "turmeric", "cumin"),
                "essential",
            ),
            IngredientRequirement("ghee or oil", _FAT_ALIASES, "optional"),
        ),
    ),
    DishIngredientProfile(
        key="bajra_roti",
        label="Bajra Roti",
        title_patterns=(r"\bbajra\s+roti\b", r"\bpearl\s+millet\s+roti\b"),
        requirements=(
            IngredientRequirement("bajra flour", ("bajra", "pearl millet"), "essential"),
            IngredientRequirement("warm water", ("water", "warm water"), "essential"),
            IngredientRequirement("salt", ("salt", "namak"), "optional"),
            IngredientRequirement("ghee", ("ghee", "butter"), "optional"),
        ),
    ),
    DishIngredientProfile(
        key="jowar_roti",
        label="Jowar Roti",
        title_patterns=(r"\bjowar\s+roti\b", r"\bsorghum\s+roti\b"),
        requirements=(
            IngredientRequirement("jowar flour", ("jowar", "sorghum"), "essential"),
            IngredientRequirement("warm water", ("water", "warm water", "hot water"), "essential"),
            IngredientRequirement("salt", ("salt", "namak"), "optional"),
        ),
    ),
    DishIngredientProfile(
        key="makki_roti",
        label="Makki ki Roti",
        title_patterns=(r"\bmakki\b.*\broti\b", r"\bcornmeal\s+roti\b"),
        requirements=(
            IngredientRequirement(
                "makki (cornmeal) flour", ("makki", "cornmeal", "maize"), "essential"
            ),
            IngredientRequirement("warm water", ("water", "warm water"), "essential"),
            IngredientRequirement("salt", ("salt", "namak"), "optional"),
            IngredientRequirement("ghee", ("ghee", "butter"), "optional"),
        ),
    ),
    DishIngredientProfile(
        key="jeera_rice",
        label="Jeera Rice",
        title_patterns=(r"\bjeera\b.*\brice\b", r"\bcumin\s+rice\b"),
        requirements=(
            IngredientRequirement("rice", ("rice", "basmati"), "essential"),
            IngredientRequirement("water", ("water",), "essential"),
            IngredientRequirement("cumin seeds", ("cumin", "jeera"), "essential"),
            IngredientRequirement("ghee or oil", _FAT_ALIASES, "essential"),
            IngredientRequirement("salt", ("salt", "namak"), "essential"),
        ),
    ),
    DishIngredientProfile(
        key="steamed_rice",
        label="Steamed Rice",
        title_patterns=(r"\bsteamed\s+rice\b", r"\bplain\s+rice\b"),
        requirements=(
            IngredientRequirement("rice", ("rice", "basmati"), "essential"),
            IngredientRequirement("water", ("water",), "essential"),
            IngredientRequirement("salt", ("salt", "namak"), "optional"),
        ),
    ),
    DishIngredientProfile(
        key="poha",
        label="Poha",
        title_patterns=(r"\bpoha\b", r"\bflattened\s+rice\b"),
        requirements=(
            IngredientRequirement("poha (flattened rice)", ("poha", "flattened rice"), "essential"),
            IngredientRequirement("oil", _FAT_ALIASES, "essential"),
            IngredientRequirement(
                "mustard seeds or cumin", ("mustard", "rai", "cumin", "jeera"), "essential"
            ),
            IngredientRequirement("turmeric", ("turmeric", "haldi"), "essential"),
            IngredientRequirement("salt", ("salt", "namak"), "essential"),
        ),
    ),
)

_SAVOURY_DISH_RE = re.compile(
    r"\b(?:sabzi|curry|masala|dal|bhurji|upma|poha|paratha|biryani|pulao|khichdi|jeera\s+rice)\b",
    re.IGNORECASE,
)


def detect_dish_ingredient_profile(recipe: dict[str, Any]) -> DishIngredientProfile | None:
    """Match a recipe title to the most specific structured ingredient profile."""
    title = str(recipe.get("title") or "").lower()
    for profile in DISH_INGREDIENT_PROFILES:
        if any(re.search(pat, title) for pat in profile.title_patterns):
            return profile
    return None


def check_ingredient_completeness(recipe: dict[str, Any]) -> list[str]:
    """Return missing essential or triggered-conditional ingredient issues."""
    title = str(recipe.get("title") or "").strip()
    ingredients = list(recipe.get("ingredients") or [])
    ing_names = [str(ing.get("name") or "").lower() for ing in ingredients if ing.get("name")]

    prep_text = " ".join(str(p) for p in (recipe.get("preparation_steps") or []))
    step_text = " ".join(
        str(s.get("title") or "") + " " + str(s.get("instruction") or "")
        for s in (recipe.get("structured_steps") or [])
        if isinstance(s, dict)
    )
    plain_text = " ".join(str(s) for s in (recipe.get("steps") or recipe.get("instructions") or []))
    exec_text = f"{title} {prep_text} {step_text} {plain_text}".lower()

    issues: list[str] = []
    profile = detect_dish_ingredient_profile(recipe)
    if profile is not None:
        for req in profile.requirements:
            present = any(any(alias in name for alias in req.aliases) for name in ing_names)
            if present:
                continue
            if req.kind == "essential":
                issues.append(f"Missing essential ingredient for {profile.label}: {req.label}.")
            elif (
                req.kind == "conditional"
                and req.condition_keywords
                and any(kw in exec_text for kw in req.condition_keywords)
            ):
                issues.append(
                    f"Missing conditional ingredient for {profile.label} "
                    f"({req.condition_hint}): {req.label}."
                )
    elif _SAVOURY_DISH_RE.search(title):
        # General savoury-dish safety check when dish is outside the specific catalogue
        has_salt = any("salt" in name or "namak" in name for name in ing_names)
        if not has_salt and "salt-free" not in title.lower():
            issues.append(f"Savoury dish '{title}' is missing salt in its ingredient list.")

    return issues
