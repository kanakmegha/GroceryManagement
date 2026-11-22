"""Structured, ingredient-consistent cooking steps for the 64-recipe catalogue (Phase 7.6)."""

from __future__ import annotations

from typing import Any


def step(
    step_number: int,
    title: str,
    instruction: str,
    ingredients_used: list[str],
    *,
    duration_minutes: int | None = None,
    heat_level: str | None = None,
    technique: str | None = None,
    completion_cue: str,
) -> dict[str, Any]:
    return {
        "step_number": step_number,
        "title": title,
        "instruction": instruction,
        "ingredients_used": ingredients_used,
        "duration_minutes": duration_minutes,
        "heat_level": heat_level,
        "technique": technique,
        "completion_cue": completion_cue,
    }


# Explicit extra canonical ingredients needed so steps never reference unrepresented spices/fats.
EXTRA_RECIPE_INGREDIENTS: dict[str, list[tuple[str, float, str, bool]]] = {
    "cat-kanda-poha": [("Turmeric Powder", 3, "g", True)],
    "cat-moong-chilla": [("Ginger", 5, "g", True), ("Green Chilli", 4, "g", True)],
    "cat-egg-bhurji": [("Turmeric Powder", 2, "g", True)],
    "cat-aloo-paratha": [("Whole Cumin", 3, "g", True)],
    "cat-roti-urad-dal": [
        ("Whole Cumin", 4, "g", True),
        ("Turmeric Powder", 3, "g", True),
        ("Garlic", 6, "g", True),
        ("Ginger", 5, "g", True),
        ("Green Chilli", 4, "g", True),
        ("Asafoetida", 1, "g", True),
    ],
    "cat-roti-aloo-sabzi": [
        ("Whole Cumin", 4, "g", True),
        ("Turmeric Powder", 3, "g", True),
    ],
    "cat-apple-almond-bowl": [("Cinnamon", 2, "g", True)],
    "cat-paneer-spinach": [("Garlic", 6, "g", True)],
}

# Hand-crafted multi-step instructions for flagship & multi-component recipes.
EXPLICIT_STRUCTURED_STEPS: dict[str, list[dict[str, Any]]] = {
    "cat-roti-urad-dal": [
        step(
            1,
            "Rinse and pressure-cook the urad dal",
            (
                "Rinse Urad Dal thoroughly in 2–3 changes of water, then soak for 15 minutes if "
                "time permits. Transfer to a pressure cooker with 3 cups water, Turmeric Powder "
                "(if using), and salt. Cook on medium heat for 3–4 whistles until the dal is "
                "soft and creamy when pressed between your fingers."
            ),
            ["Urad Dal", "Turmeric Powder"],
            duration_minutes=15,
            heat_level="medium",
            technique="pressure-cooking",
            completion_cue="Dal grains mash effortlessly and the broth looks lightly thickened.",
        ),
        step(
            2,
            "Prepare the spiced tadka",
            (
                "Heat Oil in a small tadka pan over medium-low heat. Add Whole Cumin and "
                "Asafoetida (if using) and let the cumin splutter for 15 seconds. Stir in "
                "finely chopped Garlic, Ginger, and Green Chilli (if using) and sauté for 45–60 "
                "seconds without browning or burning the spices."
            ),
            ["Oil", "Whole Cumin", "Asafoetida", "Garlic", "Ginger", "Green Chilli"],
            duration_minutes=3,
            heat_level="medium-low",
            technique="tempering",
            completion_cue="Cumin seeds crackle and garlic turns pale golden and fragrant.",
        ),
        step(
            3,
            "Combine tadka and simmer the dal",
            (
                "Pour the hot tadka directly over the cooked Urad Dal. Stir well, add 2–3 "
                "tablespoons warm water if overly thick, and simmer gently on low heat for 4–5 "
                "minutes so the tempering flavours infuse into the dal."
            ),
            ["Urad Dal", "Oil"],
            duration_minutes=5,
            heat_level="low",
            technique="simmering",
            completion_cue=(
                "Dal bubbles gently with glossy oil streaks and a rich roasted-cumin aroma."
            ),
        ),
        step(
            4,
            "Knead and rest the roti dough",
            (
                "In a mixing bowl, combine Whole Wheat Flour (Atta) with a pinch of salt and "
                "gradually add warm water while bringing the flour together. Knead for 5–6 "
                "minutes until smooth and pliable, cover with a damp cloth, and rest for 10 "
                "minutes."
            ),
            ["Whole Wheat Flour (Atta)"],
            duration_minutes=10,
            heat_level=None,
            technique="kneading",
            completion_cue="Dough feels soft, elastic, and springs back lightly when pressed.",
        ),
        step(
            5,
            "Roll and cook rotis on a hot tawa",
            (
                "Divide the rested dough into 6 equal balls, dust lightly with Whole Wheat "
                "Flour (Atta), and roll each into a thin 6-inch circle. Place on a preheated "
                "tawa over medium-high heat; flip once small bubbles appear, then press gently "
                "with a clean cloth or puff directly over flame until brown spots form on both "
                "sides."
            ),
            ["Whole Wheat Flour (Atta)"],
            duration_minutes=8,
            heat_level="medium-high",
            technique="dry-roasting",
            completion_cue=(
                "Each roti puffs up evenly with light golden-brown blisters and no raw flour "
                "patches."
            ),
        ),
        step(
            6,
            "Finish and serve hot",
            (
                "Check salt on the simmered Urad Dal, ladle into serving bowls, and serve "
                "immediately alongside the warm Whole Wheat Flour (Atta) rotis."
            ),
            ["Urad Dal", "Whole Wheat Flour (Atta)"],
            duration_minutes=1,
            heat_level=None,
            technique="plating",
            completion_cue="Rotis are soft and warm, paired with steaming tempered dal.",
        ),
    ],
    "cat-kanda-poha": [
        step(
            1,
            "Rinse and hydrate the poha",
            (
                "Place Poha in a mesh colander and rinse under cool running water for 15–20 "
                "seconds while tossing gently. Leave it in the colander for 5 minutes so each "
                "flake softens without turning mushy."
            ),
            ["Poha"],
            duration_minutes=5,
            heat_level=None,
            technique="rinsing",
            completion_cue="Flakes are pliable and separate easily without clumping.",
        ),
        step(
            2,
            "Temper mustard seeds and roast peanuts",
            (
                "Heat Oil in a wide kadai over medium heat. Add Mustard Seeds (Rai) and let "
                "them pop, then add Peanuts (if using) and sauté for 2 minutes until crisp and "
                "golden."
            ),
            ["Oil", "Mustard Seeds (Rai)", "Peanuts"],
            duration_minutes=3,
            heat_level="medium",
            technique="tempering",
            completion_cue="Mustard seeds finish crackling and peanuts smell nutty.",
        ),
        step(
            3,
            "Cook onion and potato until tender",
            (
                "Add finely chopped Onion and small-diced Potato (if using) with a pinch of "
                "salt. Sauté on medium heat for 5–6 minutes, covering briefly if needed, until "
                "the potato cubes are fork-tender and onions turn translucent."
            ),
            ["Onion", "Potato"],
            duration_minutes=6,
            heat_level="medium",
            technique="sautéing",
            completion_cue="Onions are soft and potato pieces cut cleanly with a spatula.",
        ),
        step(
            4,
            "Fold in poha, steam, and serve",
            (
                "Sprinkle Turmeric Powder (if using) and salt into the pan, then fold in the "
                "hydrated Poha gently. Cover with a lid on low heat for 2–3 minutes to warm "
                "through, fluff with a fork, and serve hot."
            ),
            ["Poha", "Turmeric Powder"],
            duration_minutes=3,
            heat_level="low",
            technique="steaming",
            completion_cue="Poha is evenly yellow, steaming hot, and light.",
        ),
    ],
}


def _classify_ingredients(ingredients: list[dict[str, Any]]) -> dict[str, list[str]]:
    fats: list[str] = []
    spices: list[str] = []
    aromatics: list[str] = []
    bases: list[str] = []
    for ing in ingredients:
        name = str(ing.get("name") or "").strip()
        if not name:
            continue
        low = name.lower()
        if any(k in low for k in ("oil", "ghee", "butter")):
            fats.append(name)
        elif any(
            k in low
            for k in (
                "mustard",
                "cumin",
                "turmeric",
                "chilli powder",
                "masala",
                "hing",
                "asafoetida",
                "cardamom",
                "cinnamon",
                "clove",
                "pepper",
                "fenugreek",
                "sesame",
                "ajwain",
                "curry leaves",
            )
        ):
            spices.append(name)
        elif any(
            k in low for k in ("onion", "garlic", "ginger", "green chilli", "tomato", "coriander")
        ):
            aromatics.append(name)
        else:
            bases.append(name)
    return {"fats": fats, "spices": spices, "aromatics": aromatics, "bases": bases}


def _join_names(names: list[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"


def _build_steps_for_recipe(recipe: dict[str, Any]) -> list[dict[str, Any]]:
    rid = str(recipe.get("recipe_id") or "")
    if rid in EXPLICIT_STRUCTURED_STEPS:
        return EXPLICIT_STRUCTURED_STEPS[rid]

    ingredients = list(recipe.get("ingredients") or [])
    grouped = _classify_ingredients(ingredients)
    fats = grouped["fats"]
    spices = grouped["spices"]
    aromatics = grouped["aromatics"]
    bases = grouped["bases"]
    all_names = [str(i.get("name") or "").strip() for i in ingredients if i.get("name")]
    title = str(recipe.get("title") or "the dish")
    family = str(recipe.get("dish_family") or "")
    cook_mins = int(recipe.get("cook_minutes") or 15)
    prep_mins = int(recipe.get("prep_minutes") or 10)
    is_multi = "&" in title or family in {
        "roti_dal",
        "rice_dal",
        "curry_rice",
        "sabzi_roti",
        "idli_dosa_combo",
    }

    # No-cook / cold / bowl / warm drink families
    if family in {"fruit_bowl", "porridge", "warm_drink"}:
        if family == "warm_drink":
            main_liq = [b for b in bases if "milk" in b.lower() or "water" in b.lower()] or bases[
                :1
            ]
            others = [b for b in bases if b not in main_liq] + spices + aromatics + fats
            return [
                step(
                    1,
                    "Prep the nuts and aromatics",
                    f"Chop or crush {_join_names(others or main_liq)} so they infuse "
                    "evenly without clumping.",
                    others or main_liq,
                    duration_minutes=max(2, prep_mins // 2),
                    heat_level=None,
                    technique="prepping",
                    completion_cue="Nuts and flavourings are finely prepped and ready to steep.",
                ),
                step(
                    2,
                    "Bring the milk to a gentle simmer",
                    f"Pour {_join_names(main_liq)} into a small saucepan over medium-low "
                    "heat and warm until tiny bubbles form around the edges without "
                    "boiling over.",
                    main_liq,
                    duration_minutes=max(3, cook_mins // 2),
                    heat_level="medium-low",
                    technique="simmering",
                    completion_cue=(
                        "Steam rises steadily and small bubbles ring the edge of the pan."
                    ),
                ),
                step(
                    3,
                    "Infuse, sweeten, and serve warm",
                    f"Whisk {_join_names(others or all_names)} into the warm "
                    f"{_join_names(main_liq)}, simmer on low heat for 2–3 minutes, then "
                    "pour into mugs and serve warm.",
                    all_names,
                    duration_minutes=3,
                    heat_level="low",
                    technique="infusing",
                    completion_cue="Drink is aromatic, smooth, and evenly tinted.",
                ),
            ]
        return [
            step(
                1,
                "Wash and prep the base ingredients",
                f"Measure and prep {_join_names(bases or all_names)}, slicing or soaking "
                "as needed for even texture.",
                bases or all_names,
                duration_minutes=max(3, prep_mins // 2),
                heat_level=None,
                technique="prepping",
                completion_cue="All base ingredients are cleanly portioned and bite-sized.",
            ),
            step(
                2,
                "Combine or gently cook the base",
                (
                    f"Simmer {_join_names(bases)} over low-medium heat for "
                    f"{max(4, cook_mins)} minutes until soft and creamy."
                    if cook_mins >= 5
                    else (
                        f"Toss {_join_names(bases)} together in a chilled serving bowl "
                        "until evenly coated."
                    )
                ),
                bases or all_names,
                duration_minutes=max(3, cook_mins),
                heat_level="low-medium" if cook_mins >= 5 else None,
                technique="simmering" if cook_mins >= 5 else "tossing",
                completion_cue="Base reaches a cohesive, spoonable consistency.",
            ),
            step(
                3,
                "Finish with toppings and serve",
                f"Fold in {_join_names(spices + aromatics + fats or all_names)} right "
                "before serving for fresh texture and flavour.",
                spices + aromatics + fats or all_names,
                duration_minutes=2,
                heat_level=None,
                technique="plating",
                completion_cue=f"{title} is evenly mixed and ready to eat.",
            ),
        ]

    # Multi-component or general cooked dishes: always 4+ steps if multi-component, 3-4 otherwise
    steps_out: list[dict[str, Any]] = []
    prep_targets = bases[:2] if bases else all_names[:2]
    steps_out.append(
        step(
            1,
            f"Prep {_join_names(prep_targets)}",
            f"Wash, drain, and measure {_join_names(bases or all_names)}. Chop "
            f"{_join_names(aromatics)} cleanly if included so everything cooks evenly.",
            list(dict.fromkeys(prep_targets + aromatics)),
            duration_minutes=max(3, prep_mins),
            heat_level=None,
            technique="prepping",
            completion_cue="All primary ingredients are washed, drained, and prepped within reach.",
        )
    )

    temper_items = fats + spices + aromatics
    if temper_items:
        steps_out.append(
            step(
                2,
                "Heat fat and build the aromatic base",
                (
                    f"Warm {_join_names(fats)} in a pan or cooker over medium heat. "
                    + (
                        f"Add {_join_names(spices)} and let them sizzle for 15–20 seconds "
                        "without scorching. "
                        if spices
                        else ""
                    )
                    + (
                        f"Stir in {_join_names(aromatics)} and sauté for 3–4 minutes "
                        "until softened and fragrant."
                        if aromatics
                        else "Swirl to coat the pan evenly."
                    )
                ),
                temper_items,
                duration_minutes=max(3, min(6, cook_mins // 3 or 3)),
                heat_level="medium",
                technique="tempering" if spices else "sautéing",
                completion_cue=(
                    "Spices are aromatic and onions/tomatoes (if used) are soft with oil "
                    "separating lightly."
                ),
            )
        )
    else:
        steps_out.append(
            step(
                2,
                f"Preheat pan and start {_join_names(bases[:1] or all_names[:1])}",
                "Preheat a heavy-bottomed pan over medium heat and add "
                f"{_join_names(bases[:1] or all_names[:1])} with a splash of water as "
                "needed.",
                bases[:1] or all_names[:1],
                duration_minutes=max(3, cook_mins // 3 or 3),
                heat_level="medium",
                technique="heating",
                completion_cue="Pan is evenly heated and base begins cooking smoothly.",
            )
        )

    main_cook_ings = list(dict.fromkeys(bases + spices + aromatics))
    steps_out.append(
        step(
            3,
            f"Cook {_join_names(bases or all_names)} through",
            f"Add {_join_names(bases or all_names)} with a pinch of salt and appropriate "
            "water (for dals/curries/rice) or gentle stirring (for stir-fries/skillets). "
            f"Cook over medium-low heat for {max(5, cook_mins - 5)} minutes until tender "
            "and well combined.",
            main_cook_ings,
            duration_minutes=max(5, cook_mins - 5),
            heat_level="medium-low",
            technique="simmering"
            if any(k in family for k in ("dal", "curry", "soup", "khichdi"))
            else "cooking",
            completion_cue=(
                "Primary ingredients are cooked tender and flavours have melded cleanly."
            ),
        )
    )

    if is_multi and "&" in title:
        _, comp2 = [p.strip() for p in title.split("&", 1)]
        comp2_ings = (
            [
                b
                for b in bases
                if any(tok in b.lower() for tok in comp2.lower().split() if len(tok) >= 3)
            ]
            or bases[-1:]
            or all_names[-1:]
        )
        steps_out.append(
            step(
                4,
                f"Prepare and cook {comp2}",
                f"Prepare {comp2} using {_join_names(comp2_ings)}—cooking or tempering "
                "separately over medium heat for 5–8 minutes so both components are "
                "fresh and hot together.",
                comp2_ings,
                duration_minutes=6,
                heat_level="medium",
                technique="griddle-cooking"
                if any(k in comp2.lower() for k in ("roti", "paratha", "dosa"))
                else "simmering",
                completion_cue=f"{comp2} is cooked through with golden edges or smooth texture.",
            )
        )

    steps_out.append(
        step(
            len(steps_out) + 1,
            "Adjust seasoning and serve",
            "Taste and adjust salt, fold in any remaining "
            f"{_join_names(fats + aromatics[-1:] if aromatics else fats or all_names[:1])}, "
            f"and serve {title} warm.",
            all_names[:2],
            duration_minutes=2,
            heat_level=None,
            technique="plating",
            completion_cue=f"{title} is balanced in seasoning and served hot.",
        )
    )
    return steps_out


def enrich_catalogue_recipe(recipe: dict[str, Any]) -> dict[str, Any]:
    rid = str(recipe.get("recipe_id") or "")
    existing_ings = list(recipe.get("ingredients") or [])
    existing_names = {str(i.get("name") or "").strip().lower() for i in existing_ings}
    for name, qty, unit, is_opt in EXTRA_RECIPE_INGREDIENTS.get(rid, []):
        if name.lower() not in existing_names:
            existing_ings.append({"name": name, "qty": qty, "unit": unit, "is_optional": is_opt})
            existing_names.add(name.lower())

    enriched = {**recipe, "ingredients": existing_ings}
    structured = _build_steps_for_recipe(enriched)
    plain_steps = [
        f"{st['title']}: {st['instruction']} ({st['completion_cue']})" for st in structured
    ]
    return {
        **enriched,
        "structured_steps": structured,
        "steps": plain_steps,
    }
