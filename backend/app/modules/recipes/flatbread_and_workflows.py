"""Structured flatbread classification and dish-category workflow specs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FlatbreadSpec:
    key: str
    label: str
    aliases: tuple[str, ...]
    dough_cues: tuple[str, ...]
    shaping_cues: tuple[str, ...]
    cooking_cues: tuple[str, ...]


FLATBREAD_SPECS: tuple[FlatbreadSpec, ...] = (
    FlatbreadSpec(
        key="phulka",
        label="Phulka",
        aliases=("phulka", "phulkas", "fulka"),
        dough_cues=("knead", "soft", "rest"),
        shaping_cues=("roll", "thin", "circle"),
        cooking_cues=("tawa", "flip", "flame", "puff"),
    ),
    FlatbreadSpec(
        key="tandoori_roti",
        label="Tandoori Roti",
        aliases=("tandoori roti", "tandoori rotis"),
        dough_cues=("knead", "rest", "dough"),
        shaping_cues=("roll", "oval", "circle", "thick"),
        cooking_cues=("tawa", "water", "invert", "tandoor", "char", "broil", "flame"),
    ),
    FlatbreadSpec(
        key="missi_roti",
        label="Missi Roti",
        aliases=("missi roti", "missi rotis"),
        dough_cues=("besan", "gram flour", "knead", "rest"),
        shaping_cues=("roll", "circle"),
        cooking_cues=("tawa", "ghee", "oil", "flip", "brown"),
    ),
    FlatbreadSpec(
        key="bajra_roti",
        label="Bajra Roti",
        aliases=("bajra roti", "bajra rotis", "pearl millet roti"),
        dough_cues=("bajra", "warm water", "knead"),
        shaping_cues=("pat", "palm", "hand", "press", "parchment"),
        cooking_cues=("tawa", "water", "flip", "puff", "brown"),
    ),
    FlatbreadSpec(
        key="jowar_roti",
        label="Jowar Roti",
        aliases=("jowar roti", "jowar rotis", "sorghum roti", "jolada rotti"),
        dough_cues=("jowar", "warm water", "hot water", "knead"),
        shaping_cues=("pat", "palm", "hand", "press", "parchment"),
        cooking_cues=("tawa", "water", "flip", "brown"),
    ),
    FlatbreadSpec(
        key="makki_ki_roti",
        label="Makki ki Roti",
        aliases=("makki ki roti", "makki roti", "cornmeal roti"),
        dough_cues=("makki", "cornmeal", "maize", "warm water", "knead"),
        shaping_cues=("pat", "palm", "hand", "press", "parchment"),
        cooking_cues=("tawa", "ghee", "oil", "flip", "golden"),
    ),
    FlatbreadSpec(
        key="lachha_paratha",
        label="Lachha Paratha",
        aliases=("lachha paratha", "laccha paratha", "layered paratha"),
        dough_cues=("knead", "rest", "dough"),
        shaping_cues=("pleat", "fold", "coil", "spiral", "layer", "roll"),
        cooking_cues=("tawa", "ghee", "oil", "flip", "crisp", "golden"),
    ),
    FlatbreadSpec(
        key="stuffed_paratha",
        label="Stuffed Paratha",
        aliases=(
            "stuffed paratha",
            "aloo paratha",
            "paneer paratha",
            "gobhi paratha",
            "gobi paratha",
            "mooli paratha",
        ),
        dough_cues=("knead", "rest", "dough"),
        shaping_cues=("stuff", "filling", "seal", "pinch", "roll"),
        cooking_cues=("tawa", "ghee", "oil", "flip", "golden"),
    ),
    FlatbreadSpec(
        key="plain_paratha",
        label="Plain Paratha",
        aliases=("plain paratha", "triangle paratha", "square paratha", "paratha"),
        dough_cues=("knead", "rest", "dough"),
        shaping_cues=("fold", "roll", "triangle", "square", "layer"),
        cooking_cues=("tawa", "ghee", "oil", "flip", "golden"),
    ),
    FlatbreadSpec(
        key="plain_chapati",
        label="Plain Chapati / Roti",
        aliases=("plain chapati", "chapati", "chapatis", "whole wheat roti", "roti", "rotis"),
        dough_cues=("knead", "soft", "rest", "dough"),
        shaping_cues=("roll", "circle", "dry flour"),
        cooking_cues=("tawa", "flip", "press", "spots"),
    ),
)


@dataclass(frozen=True)
class WorkflowSpec:
    category: str
    keywords: tuple[str, ...]
    stage_groups: tuple[tuple[str, tuple[str, ...]], ...]


WORKFLOW_SPECS: tuple[WorkflowSpec, ...] = (
    WorkflowSpec(
        category="chilla_pancake",
        keywords=(
            "chilla",
            "chillas",
            "cheela",
            "cheelas",
            "besan chilla",
            "moong dal chilla",
            "uttapam",
            "uttapams",
            "pancake",
            "pancakes",
        ),
        stage_groups=(
            ("batter", ("batter", "blend", "whisk", "grind", "soak", "mix")),
            ("spread/cook", ("tawa", "pan", "skillet", "pour", "spread", "ladle")),
            ("flip/crisp", ("flip", "turn", "golden", "crisp", "oil", "ghee")),
        ),
    ),
    WorkflowSpec(
        category="smoothie_beverage",
        keywords=(
            "smoothie",
            "smoothies",
            "milkshake",
            "milkshakes",
            "lassi",
            "chaas",
            "buttermilk",
            "shake",
            "shakes",
        ),
        stage_groups=(
            ("prep/combine", ("peel", "slice", "chop", "combine", "add", "blender")),
            ("blend/serve", ("blend", "whisk", "puree", "smooth", "pour", "serve")),
        ),
    ),
    WorkflowSpec(
        category="salad",
        keywords=("salad", "salads", "kosambari", "kachumber", "sprouts salad", "chaat"),
        stage_groups=(
            ("prep/chop", ("wash", "rinse", "chop", "dice", "slice", "grate", "drain", "cool")),
            ("toss/season", ("toss", "mix", "combine", "season", "salt", "lemon", "drizzle")),
        ),
    ),
    WorkflowSpec(
        category="dal",
        keywords=("dal", "lentil", "sambar", "rasam"),
        stage_groups=(
            ("wash/soak", ("rinse", "wash", "soak")),
            ("cook lentils", ("pressure", "boil", "simmer", "whistle", "soft")),
            ("tempering", ("tadka", "temper", "crackle", "saute", "sauté")),
        ),
    ),
    WorkflowSpec(
        category="paneer_curry",
        keywords=("paneer curry", "paneer masala", "paneer butter", "kadai paneer", "matar paneer"),
        stage_groups=(
            ("gravy base", ("onion", "tomato", "puree", "gravy", "saute", "sauté")),
            ("spice cooking", ("turmeric", "chilli", "coriander", "garam masala", "cumin")),
            ("paneer addition", ("paneer", "simmer", "fold", "cube")),
        ),
    ),
    WorkflowSpec(
        category="curry",
        keywords=("curry", "korma", "makhani", "gravy"),
        stage_groups=(
            ("base", ("onion", "tomato", "puree", "gravy", "saute", "sauté")),
            ("spices", ("turmeric", "chilli", "coriander", "garam masala", "cumin")),
            ("simmer", ("simmer", "boil", "cover", "thicken", "cook")),
        ),
    ),
    WorkflowSpec(
        category="sabzi",
        keywords=("sabzi", "bhurji", "stir-fry", "skillet", "aloo matar", "bhindi", "gobi"),
        stage_groups=(
            ("prep", ("chop", "dice", "slice", "cube", "wash")),
            ("temper/saute", ("heat", "oil", "cumin", "mustard", "saute", "sauté")),
            ("cook/moisture", ("cover", "simmer", "stir", "tender", "wilt", "cook")),
        ),
    ),
    WorkflowSpec(
        category="rice",
        keywords=("jeera rice", "steamed rice", "pulao", "khichdi", "biryani", "rice bowl"),
        stage_groups=(
            ("wash/soak", ("rinse", "wash", "soak")),
            ("cooking method", ("boil", "simmer", "pressure", "steam", "absorption")),
            ("rest/fluff", ("rest", "fluff", "fork")),
        ),
    ),
    WorkflowSpec(
        category="poha",
        keywords=("poha", "kanda poha", "flattened rice"),
        stage_groups=(
            ("rinse/drain", ("rinse", "drain", "colander", "soften")),
            ("temper", ("mustard", "curry leaves", "onion", "turmeric")),
            ("gentle mix", ("fold", "toss", "gently", "steam")),
        ),
    ),
)


def detect_flatbread_spec(recipe: dict[str, Any]) -> FlatbreadSpec | None:
    """Identify the most specific flatbread type referenced by the recipe."""
    title = str(recipe.get("title") or "").lower()
    steps_text = " ".join(
        str(s.get("title") or "") + " " + str(s.get("instruction") or "")
        for s in (recipe.get("structured_steps") or [])
        if isinstance(s, dict)
    ).lower()
    plain_text = " ".join(str(s) for s in (recipe.get("steps") or recipe.get("instructions") or []))
    haystack = f"{title} {steps_text} {plain_text.lower()}"

    # Check most specific aliases first (order in FLATBREAD_SPECS is specific -> generic).
    for spec in FLATBREAD_SPECS:
        for alias in spec.aliases:
            if re.search(rf"\b{re.escape(alias)}\b", title):
                return spec
    for spec in FLATBREAD_SPECS:
        for alias in spec.aliases:
            if re.search(rf"\b{re.escape(alias)}\b", haystack):
                return spec
    return None


def detect_dish_workflow(recipe: dict[str, Any]) -> WorkflowSpec | None:
    """Identify the dish-category workflow based on recipe title."""
    title = str(recipe.get("title") or "").lower()
    is_combo = "&" in title
    has_flatbread_title = any(
        re.search(rf"\b{re.escape(alias)}\b", title)
        for spec in FLATBREAD_SPECS
        for alias in spec.aliases
    )
    for spec in WORKFLOW_SPECS:
        # A stuffed/plain flatbread containing dal or paneer (e.g. "Stuffed Paratha with Moong Dal")
        # is governed by its flatbread spec, not dal/curry/sabzi workflow, unless it is an explicit
        # multi-component "&" combo (e.g. "Whole Wheat Roti & Urad Dal Tadka").
        if (
            has_flatbread_title
            and not is_combo
            and spec.category in {"dal", "curry", "paneer_curry", "sabzi"}
        ):
            continue
        if any(re.search(rf"\b{re.escape(kw)}\b", title) for kw in spec.keywords):
            return spec
    return None
