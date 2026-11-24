"""Tests for executable AI recipe quality, flatbread/workflow rules, and regeneration."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.config import Settings
from app.infra.llm.hf_provider import (
    _DETERMINISTIC_TEMPLATES,
    _build_deterministic_recipes_with_context,
)
from app.modules.recipes.catalogue_data import EXPANDED_RECIPE_CATALOG
from app.modules.recipes.quality_validator import validate_recipe_quality
from app.modules.recipes.schemas import RecipeChatRequest
from app.modules.recipes.service import chat_recipes


def test_expanded_catalogue_passes_quality_validator() -> None:
    """All 64 catalogue recipes must remain valid without regressions."""
    for recipe in EXPANDED_RECIPE_CATALOG:
        res = validate_recipe_quality(recipe)
        assert res.is_valid, f"{recipe.get('title')} failed: {res.issues}"


def test_all_deterministic_templates_pass_strict_validation() -> None:
    """Every deterministic template (including flatbreads, dal, sabzi, curry, rice) is valid."""
    required_titles = {
        "Plain Whole Wheat Chapati",
        "Puffed Phulka",
        "Spiced Missi Roti",
        "Bajra Roti",
        "Aloo Stuffed Paratha",
        "South Indian Dal Tadka & Rice Bowl",
        "Aloo Matar Dry Sabzi",
        "Homestyle Paneer Masala Curry",
        "Jeera Steamed Rice",
    }
    present_titles = {tpl["title"] for tpl in _DETERMINISTIC_TEMPLATES}
    assert required_titles.issubset(present_titles)

    for tpl in _DETERMINISTIC_TEMPLATES:
        ings = [
            {"name": name, "quantity": qty, "is_optional": False}
            for name, qty in tpl["ingredients"]
        ]
        res = validate_recipe_quality(
            {"servings": 2, **tpl, "ingredients": ings},
            strict_ai_generated=True,
        )
        assert res.is_valid, f"{tpl['title']} failed strict validation: {res.issues}"


def test_deterministic_context_builder_returns_executable_recipes() -> None:
    recipes, _ = _build_deterministic_recipes_with_context(
        message="Make phulka or paneer curry",
        inventory_items=[
            {"id": "1", "name": "whole wheat flour", "quantity": 500, "unit": "g"},
            {"id": "2", "name": "paneer", "quantity": 250, "unit": "g"},
            {"id": "3", "name": "tomato", "quantity": 4, "unit": "pcs"},
            {"id": "4", "name": "onion", "quantity": 3, "unit": "pcs"},
        ],
        servings=2,
        max_prep_minutes=None,
        dietary=None,
    )
    assert recipes
    for r in recipes:
        dumped = r.model_dump()
        assert dumped["equipment_required"]
        assert dumped["preparation_steps"]
        assert dumped["structured_steps"]
        ings = [
            {"name": i["name"], "quantity": i["quantity"], "is_optional": False}
            for i in (
                dumped["available_ingredients"]
                + dumped["partial_ingredients"]
                + dumped["missing_ingredients"]
            )
        ]
        res = validate_recipe_quality({**dumped, "ingredients": ings}, strict_ai_generated=True)
        assert res.is_valid, f"{dumped['title']} failed strict validation: {res.issues}"


def test_strict_validator_rejects_generic_placeholders_seasoning_and_qty_mismatch() -> None:
    bad_recipe = {
        "title": "Spiced Paneer Curry",
        "servings": 2,
        "prep_minutes": 10,
        "cook_minutes": 15,
        "difficulty": "easy",
        "ingredients": [
            {"name": "Paneer", "quantity": "200 g", "is_optional": False},
            {"name": "Salt", "quantity": "to taste", "is_optional": False},
            {"name": "Cumin seeds", "quantity": "2 g", "is_optional": False},
        ],
        "structured_steps": [
            {
                "step_number": 1,
                "title": "Prepare Gravy",
                "instruction": "Add spices to taste and fry properly with 350 g paneer.",
                "ingredients_used": ["Paneer"],
                "ingredient_quantities": {"Paneer": "350 g"},
                "heat_level": None,
                "duration_minutes": None,
                "completion_cue": "Done",
            },
            {
                "step_number": 3,
                "title": "Finish",
                "instruction": "Cook as required and deduct stock from inventory lot.",
                "ingredients_used": [],
                "completion_cue": "Ready to serve",
            },
        ],
    }
    res = validate_recipe_quality(bad_recipe, strict_ai_generated=True)
    assert not res.is_valid
    joined = " ".join(res.issues).lower()
    assert "vague instruction" in joined
    assert "seasoning" in joined
    assert "exceed ingredient list" in joined
    assert "missing heat_level" in joined
    assert "missing approximate duration" in joined
    assert "bookkeeping" in joined
    assert "out-of-order" in joined
    assert "cumin seeds" in joined


def test_flatbread_differentiation_rejects_wrong_technique() -> None:
    # A Phulka recipe that uses plain chapati / generic steps (no direct flame puffing) must fail
    fake_phulka = {
        "title": "Puffed Phulka",
        "servings": 2,
        "prep_minutes": 15,
        "cook_minutes": 10,
        "difficulty": "easy",
        "ingredients": [
            {"name": "Whole wheat flour", "quantity": "150 g", "is_optional": False},
            {"name": "Water", "quantity": "95 ml", "is_optional": False},
            {"name": "Salt", "quantity": "2 g", "is_optional": False},
        ],
        "structured_steps": [
            {
                "step_number": 1,
                "title": "Mix Flour",
                "instruction": (
                    "Mix 150 g whole wheat flour, 2 g salt, and 95 ml water; knead and rest 15 min."
                ),
                "ingredients_used": ["Whole wheat flour", "Salt", "Water"],
                "completion_cue": "Smooth soft dough forms cleanly.",
            },
            {
                "step_number": 2,
                "title": "Roll Circles",
                "instruction": "Roll the dough portions into thin 15 cm circles on a board.",
                "ingredients_used": ["Whole wheat flour"],
                "completion_cue": "Even thin round circles.",
            },
            {
                "step_number": 3,
                "title": "Bake in Oven",
                "instruction": (
                    "Bake the rolled dough on a tray in an oven for 5 minutes until dry."
                ),
                "ingredients_used": ["Whole wheat flour"],
                "heat_level": "medium",
                "duration_minutes": 5,
                "completion_cue": "Surface looks dry and lightly browned.",
            },
        ],
    }
    res = validate_recipe_quality(fake_phulka, strict_ai_generated=True)
    assert not res.is_valid
    assert any("phulka" in issue.lower() for issue in res.issues)

    # A Bajra Roti that omits warm water / patting technique must fail
    fake_bajra = {
        "title": "Bajra Roti",
        "servings": 2,
        "prep_minutes": 15,
        "cook_minutes": 10,
        "difficulty": "medium",
        "ingredients": [
            {"name": "Bajra flour", "quantity": "150 g", "is_optional": False},
            {"name": "Water", "quantity": "110 ml", "is_optional": False},
            {"name": "Salt", "quantity": "2 g", "is_optional": False},
        ],
        "structured_steps": [
            {
                "step_number": 1,
                "title": "Knead Flour",
                "instruction": (
                    "Mix 150 g bajra flour with 2 g salt and 110 ml cold water to knead."
                ),
                "ingredients_used": ["Bajra flour", "Salt", "Water"],
                "completion_cue": "Dough holds together.",
            },
            {
                "step_number": 2,
                "title": "Roll Thin",
                "instruction": "Roll with a pin into a thin circle on a floured board.",
                "ingredients_used": ["Bajra flour"],
                "completion_cue": "Thin circle formed.",
            },
            {
                "step_number": 3,
                "title": "Cook on Tawa",
                "instruction": "Cook on a hot tawa over medium heat for 3 minutes until brown.",
                "ingredients_used": ["Bajra flour"],
                "heat_level": "medium",
                "duration_minutes": 3,
                "completion_cue": "Brown spots appear on both sides.",
            },
        ],
    }
    res_bajra = validate_recipe_quality(fake_bajra, strict_ai_generated=True)
    assert not res_bajra.is_valid
    assert any("bajra roti" in issue.lower() for issue in res_bajra.issues)


def test_missing_essential_ingredients_and_workflows_fail_deterministically() -> None:
    # 1. Potato sabzi missing salt
    aloo_no_salt = {
        "title": "Aloo Matar Sabzi",
        "servings": 2,
        "prep_minutes": 10,
        "cook_minutes": 15,
        "difficulty": "easy",
        "ingredients": [
            {"name": "Potato", "quantity": "250 g", "is_optional": False},
            {"name": "Green peas", "quantity": "100 g", "is_optional": False},
            {"name": "Oil", "quantity": "15 ml", "is_optional": False},
            {"name": "Cumin seeds", "quantity": "2 g", "is_optional": False},
            {"name": "Turmeric", "quantity": "1 g", "is_optional": False},
        ],
        "structured_steps": [
            {
                "step_number": 1,
                "title": "Chop Vegetables",
                "instruction": "Peel and dice 250 g potatoes into 1.5 cm cubes and rinse peas.",
                "ingredients_used": ["Potato", "Green peas"],
                "completion_cue": "Uniform cubes ready for cooking.",
            },
            {
                "step_number": 2,
                "title": "Temper Cumin & Turmeric",
                "instruction": (
                    "Heat 15 ml oil over medium heat, crackle 2 g cumin seeds, and add "
                    "1 g turmeric."
                ),
                "ingredients_used": ["Oil", "Cumin seeds", "Turmeric"],
                "heat_level": "medium",
                "duration_minutes": 2,
                "completion_cue": "Spices are fragrant without scorching.",
            },
            {
                "step_number": 3,
                "title": "Cook Potatoes & Peas",
                "instruction": (
                    "Toss in potatoes and peas, cover, and cook on low heat 12 minutes "
                    "until tender."
                ),
                "ingredients_used": ["Potato", "Green peas"],
                "heat_level": "low",
                "duration_minutes": 12,
                "completion_cue": "Potatoes pierce easily with a fork.",
            },
        ],
    }
    res_aloo = validate_recipe_quality(aloo_no_salt, strict_ai_generated=True)
    assert not res_aloo.is_valid
    assert any("salt" in i.lower() for i in res_aloo.issues)

    # 2. Dal missing its seasoning (no turmeric, cumin, or salt)
    dal_no_seasoning = {
        "title": "Simple Dal Tadka",
        "servings": 2,
        "prep_minutes": 10,
        "cook_minutes": 20,
        "difficulty": "easy",
        "ingredients": [
            {"name": "Toor dal", "quantity": "150 g", "is_optional": False},
            {"name": "Water", "quantity": "500 ml", "is_optional": False},
            {"name": "Ghee", "quantity": "10 ml", "is_optional": False},
        ],
        "structured_steps": [
            {
                "step_number": 1,
                "title": "Rinse & Pressure Cook Dal",
                "instruction": (
                    "Rinse 150 g toor dal and pressure cook with 500 ml water for 12 "
                    "minutes."
                ),
                "ingredients_used": ["Toor dal", "Water"],
                "heat_level": "medium",
                "duration_minutes": 12,
                "completion_cue": "Dal mashes easily between fingers.",
            },
            {
                "step_number": 2,
                "title": "Warm Ghee",
                "instruction": "Heat 10 ml ghee in a small pan over medium heat for 1 minute.",
                "ingredients_used": ["Ghee"],
                "heat_level": "medium",
                "duration_minutes": 1,
                "completion_cue": "Ghee shimmers gently.",
            },
            {
                "step_number": 3,
                "title": "Pour & Simmer",
                "instruction": "Pour warm ghee into dal and simmer on low heat for 3 minutes.",
                "ingredients_used": ["Toor dal", "Ghee"],
                "heat_level": "low",
                "duration_minutes": 3,
                "completion_cue": "Dal is smooth and glossy.",
            },
        ],
    }
    res_dal = validate_recipe_quality(dal_no_seasoning, strict_ai_generated=True)
    assert not res_dal.is_valid
    assert any("turmeric" in i.lower() for i in res_dal.issues)
    assert any("salt" in i.lower() for i in res_dal.issues)

    # 3. Paneer curry with no gravy preparation
    paneer_no_gravy = {
        "title": "Homestyle Paneer Masala Curry",
        "servings": 2,
        "prep_minutes": 10,
        "cook_minutes": 10,
        "difficulty": "easy",
        "ingredients": [
            {"name": "Paneer", "quantity": "200 g", "is_optional": False},
            {"name": "Oil", "quantity": "10 ml", "is_optional": False},
            {"name": "Salt", "quantity": "3 g", "is_optional": False},
            {"name": "Turmeric", "quantity": "1 g", "is_optional": False},
        ],
        "structured_steps": [
            {
                "step_number": 1,
                "title": "Cube Paneer",
                "instruction": "Cut 200 g paneer into 2 cm cubes and set aside on a plate.",
                "ingredients_used": ["Paneer"],
                "completion_cue": "Even paneer cubes ready.",
            },
            {
                "step_number": 2,
                "title": "Toss Paneer",
                "instruction": (
                    "Heat 10 ml oil over medium heat, add 1 g turmeric, 3 g salt, and "
                    "paneer 4 minutes."
                ),
                "ingredients_used": ["Oil", "Turmeric", "Salt", "Paneer"],
                "heat_level": "medium",
                "duration_minutes": 4,
                "completion_cue": "Paneer is lightly golden.",
            },
            {
                "step_number": 3,
                "title": "Serve Warm",
                "instruction": "Transfer paneer to serving bowls and serve warm immediately.",
                "ingredients_used": ["Paneer"],
                "completion_cue": "Paneer is warm and coated.",
            },
        ],
    }
    res_paneer = validate_recipe_quality(paneer_no_gravy, strict_ai_generated=True)
    assert not res_paneer.is_valid
    assert any("gravy" in i.lower() for i in res_paneer.issues)

    # 4. Missi roti without besan
    missi_no_besan = {
        "title": "Spiced Missi Roti",
        "servings": 2,
        "prep_minutes": 20,
        "cook_minutes": 10,
        "difficulty": "easy",
        "ingredients": [
            {"name": "Whole wheat flour", "quantity": "150 g", "is_optional": False},
            {"name": "Water", "quantity": "95 ml", "is_optional": False},
            {"name": "Salt", "quantity": "3 g", "is_optional": False},
            {"name": "Ajwain", "quantity": "1 g", "is_optional": False},
        ],
        "structured_steps": [
            {
                "step_number": 1,
                "title": "Knead Dough",
                "instruction": (
                    "Knead 150 g whole wheat flour, 3 g salt, 1 g ajwain, and 95 ml "
                    "water; rest 15 min."
                ),
                "ingredients_used": ["Whole wheat flour", "Salt", "Ajwain", "Water"],
                "completion_cue": "Smooth firm dough forms.",
            },
            {
                "step_number": 2,
                "title": "Roll Discs",
                "instruction": "Roll into 14 cm circles on a floured board with a rolling pin.",
                "ingredients_used": ["Whole wheat flour"],
                "completion_cue": "Even circles formed.",
            },
            {
                "step_number": 3,
                "title": "Roast on Tawa",
                "instruction": (
                    "Roast on a hot tawa over medium heat for 45 seconds per side "
                    "until brown."
                ),
                "ingredients_used": ["Whole wheat flour"],
                "heat_level": "medium",
                "duration_minutes": 8,
                "completion_cue": "Golden brown spots appear.",
            },
        ],
    }
    res_missi = validate_recipe_quality(missi_no_besan, strict_ai_generated=True)
    assert not res_missi.is_valid
    assert any("besan" in i.lower() for i in res_missi.issues)

    # 5. Rice without a defined cooking method (no boil/simmer/pressure/steam)
    rice_no_method = {
        "title": "Steamed Rice",
        "servings": 2,
        "prep_minutes": 10,
        "cook_minutes": 15,
        "difficulty": "easy",
        "ingredients": [
            {"name": "Rice", "quantity": "200 g", "is_optional": False},
            {"name": "Water", "quantity": "400 ml", "is_optional": False},
        ],
        "structured_steps": [
            {
                "step_number": 1,
                "title": "Rinse Rice",
                "instruction": "Rinse 200 g rice twice in cool water and drain cleanly.",
                "ingredients_used": ["Rice"],
                "completion_cue": "Water runs clear.",
            },
            {
                "step_number": 2,
                "title": "Cook Rice",
                "instruction": (
                    "Put 200 g rice and 400 ml water in a pot over medium heat for 12 "
                    "minutes."
                ),
                "ingredients_used": ["Rice", "Water"],
                "heat_level": "medium",
                "duration_minutes": 12,
                "completion_cue": "Rice looks soft.",
            },
            {
                "step_number": 3,
                "title": "Rest & Fluff",
                "instruction": "Rest 5 minutes and fluff gently with a fork before serving.",
                "ingredients_used": ["Rice"],
                "completion_cue": "Grains are separate.",
            },
        ],
    }
    res_rice = validate_recipe_quality(rice_no_method, strict_ai_generated=True)
    assert not res_rice.is_valid
    assert any("cooking method" in i.lower() for i in res_rice.issues)


def test_valid_dish_variations_pass_without_forced_spices() -> None:
    # Mild Dry Aloo Sabzi without chilli powder, onion, garlic, or sugar
    mild_dry_aloo = {
        "title": "Mild Dry Aloo Sabzi",
        "servings": 2,
        "prep_minutes": 10,
        "cook_minutes": 15,
        "difficulty": "easy",
        "ingredients": [
            {"name": "Potato", "quantity": "250 g", "is_optional": False},
            {"name": "Oil", "quantity": "15 ml", "is_optional": False},
            {"name": "Cumin seeds", "quantity": "2 g", "is_optional": False},
            {"name": "Turmeric powder", "quantity": "1 g", "is_optional": False},
            {"name": "Salt", "quantity": "4 g", "is_optional": False},
        ],
        "structured_steps": [
            {
                "step_number": 1,
                "title": "Dice Potatoes",
                "instruction": "Peel and dice 250 g potatoes into uniform 1.5 cm cubes.",
                "ingredients_used": ["Potato"],
                "completion_cue": "Uniform potato cubes ready.",
            },
            {
                "step_number": 2,
                "title": "Temper Cumin & Turmeric",
                "instruction": (
                    "Heat 15 ml oil over medium heat, crackle 2 g cumin seeds 20 seconds, "
                    "and stir in 1 g turmeric powder."
                ),
                "ingredients_used": ["Oil", "Cumin seeds", "Turmeric powder"],
                "heat_level": "medium",
                "duration_minutes": 1,
                "completion_cue": "Cumin is fragrant and golden.",
            },
            {
                "step_number": 3,
                "title": "Cover & Cook Potatoes",
                "instruction": (
                    "Add 250 g potato and 4 g salt, cover, and cook over low heat for 12 minutes "
                    "until fork-tender."
                ),
                "ingredients_used": ["Potato", "Salt"],
                "heat_level": "low",
                "duration_minutes": 12,
                "completion_cue": "Potatoes pierce easily with crisp edges.",
            },
        ],
    }
    res = validate_recipe_quality(mild_dry_aloo, strict_ai_generated=True)
    assert res.is_valid, f"Valid mild dry aloo sabzi failed: {res.issues}"


def test_non_measurable_step_quantities_and_optional_garnish_rules() -> None:
    # "Water: As needed" must fail strict validation
    missi_as_needed = {
        "title": "Missi Roti",
        "servings": 2,
        "prep_minutes": 15,
        "cook_minutes": 10,
        "difficulty": "easy",
        "ingredients": [
            {"name": "Besan", "quantity": "80 g", "is_optional": False},
            {"name": "Whole wheat flour", "quantity": "70 g", "is_optional": False},
            {"name": "Water", "quantity": "90-100 ml", "is_optional": False},
            {"name": "Salt", "quantity": "3 g", "is_optional": False},
            {"name": "Ajwain", "quantity": "1 g", "is_optional": False},
        ],
        "structured_steps": [
            {
                "step_number": 1,
                "title": "Knead Dough",
                "instruction": (
                    "Mix 80 g besan, 70 g whole wheat flour, 3 g salt, and 1 g ajwain; "
                    "add water and knead 5 minutes, then rest 15 minutes."
                ),
                "ingredients_used": ["Besan", "Whole wheat flour", "Salt", "Ajwain", "Water"],
                "ingredient_quantities": {"Water": "As needed"},
                "completion_cue": "Soft, pliable dough forms.",
            },
            {
                "step_number": 2,
                "title": "Roll Circles",
                "instruction": "Divide into 4 balls and roll into 14 cm circles on a board.",
                "ingredients_used": ["Whole wheat flour"],
                "completion_cue": "Even circles formed.",
            },
            {
                "step_number": 3,
                "title": "Roast on Tawa",
                "instruction": "Roast on hot tawa over medium heat for 45 seconds per side.",
                "ingredients_used": ["Whole wheat flour"],
                "heat_level": "medium",
                "duration_minutes": 8,
                "completion_cue": "Golden brown spots appear.",
            },
        ],
    }
    res_bad = validate_recipe_quality(missi_as_needed, strict_ai_generated=True)
    assert not res_bad.is_valid
    assert any("non-measurable quantity" in i.lower() for i in res_bad.issues)

    # Measurable water range ("90–100 ml") + descriptive optional garnish passes
    missi_range = {
        **missi_as_needed,
        "ingredients": [
            *missi_as_needed["ingredients"],
            {"name": "Fresh coriander leaves", "quantity": "a handful", "is_optional": True},
        ],
        "structured_steps": [
            {
                **missi_as_needed["structured_steps"][0],
                "ingredient_quantities": {"Water": "90–100 ml"},
            },
            missi_as_needed["structured_steps"][1],
            {
                **missi_as_needed["structured_steps"][2],
                "ingredient_quantities": {"Fresh coriander leaves": "a handful"},
            },
        ],
    }
    res_good = validate_recipe_quality(missi_range, strict_ai_generated=True)
    assert res_good.is_valid, f"Expected range + optional garnish to pass: {res_good.issues}"


def test_combo_prompts_do_not_inject_dishes_and_preserve_relevant_fallback() -> None:
    inventory = [
        {"id": "1", "name": "whole wheat flour", "quantity": 500, "unit": "g"},
        {"id": "2", "name": "potato", "quantity": 500, "unit": "g"},
        {"id": "3", "name": "green peas", "quantity": 200, "unit": "g"},
        {"id": "4", "name": "paneer", "quantity": 250, "unit": "g"},
        {"id": "5", "name": "oil", "quantity": 250, "unit": "ml"},
        {"id": "6", "name": "salt", "quantity": 250, "unit": "g"},
    ]
    recipes, _ = _build_deterministic_recipes_with_context(
        message="Phulka with aloo matar sabzi",
        inventory_items=inventory,
        servings=2,
        max_prep_minutes=None,
        dietary=None,
    )
    titles = [r.title for r in recipes]
    assert titles[0] in {"Puffed Phulka", "Aloo Matar Dry Sabzi"}
    assert "Homestyle Paneer Masala Curry" not in titles
    all_ing_names = {
        ing.name.lower()
        for r in recipes
        for ing in (r.available_ingredients + r.partial_ingredients + r.missing_ingredients)
    }
    assert "aloo matar sabzi" not in all_ing_names

    # Curd / jeera rice / plain chapati must never be injected as ingredients
    for combo in (
        "Dal tadka with plain chapati",
        "Paneer butter masala with jeera rice",
        "Stuffed aloo paratha with curd",
    ):
        recs, _ = _build_deterministic_recipes_with_context(
            message=combo,
            inventory_items=inventory,
            servings=2,
            max_prep_minutes=None,
            dietary=None,
        )
        ing_set = {
            ing.name.lower()
            for r in recs
            for ing in (r.available_ingredients + r.partial_ingredients + r.missing_ingredients)
        }
        assert "plain chapati" not in ing_set
        assert "jeera rice" not in ing_set
        assert "curd" not in ing_set


@pytest.mark.asyncio
async def test_hf_provider_handles_truncated_json_retry_and_honest_failure() -> None:
    import json

    import httpx

    from app.infra.llm.hf_provider import HuggingFaceRecipeProvider

    valid_payload = json.dumps(
        {
            "reply": "Here is Missi Roti.",
            "recipes": [
                {
                    "title": "Missi Roti",
                    "description": "Spiced besan and whole wheat flatbread.",
                    "servings": 2,
                    "prep_minutes": 15,
                    "cook_minutes": 10,
                    "difficulty": "easy",
                    "equipment_required": ["mixing bowl", "rolling pin", "tawa"],
                    "available_ingredients": [
                        {"name": "Besan", "quantity": "80 g", "status": "available"},
                        {"name": "Whole wheat flour", "quantity": "70 g", "status": "available"},
                        {"name": "Water", "quantity": "90-100 ml", "status": "available"},
                        {"name": "Salt", "quantity": "3 g", "status": "available"},
                        {"name": "Ajwain", "quantity": "1 g", "status": "available"},
                    ],
                    "preparation_steps": ["Measure flours and 90-100 ml water."],
                    "structured_steps": [
                        {
                            "step_number": 1,
                            "title": "Knead Dough",
                            "instruction": (
                                "Mix 80 g besan, 70 g whole wheat flour, 3 g salt, and 1 g ajwain; "
                                "knead with 90-100 ml water and rest 15 minutes."
                            ),
                            "ingredients_used": [
                                "Besan",
                                "Whole wheat flour",
                                "Salt",
                                "Ajwain",
                                "Water",
                            ],
                            "ingredient_quantities": {"Water": "90-100 ml"},
                            "completion_cue": "Soft dough forms.",
                        },
                        {
                            "step_number": 2,
                            "title": "Roll Discs",
                            "instruction": "Roll into 14 cm circles on a lightly floured board.",
                            "ingredients_used": ["Whole wheat flour"],
                            "completion_cue": "Even circles formed.",
                        },
                        {
                            "step_number": 3,
                            "title": "Cook on Tawa",
                            "instruction": (
                                "Roast on a hot tawa over medium heat for 45 seconds per side."
                            ),
                            "ingredients_used": ["Whole wheat flour"],
                            "heat_level": "medium",
                            "duration_minutes": 8,
                            "completion_cue": "Golden brown spots appear.",
                        },
                    ],
                    "steps": ["Knead dough.", "Roll discs.", "Cook on tawa."],
                }
            ],
            "follow_up_questions": [],
        }
    )

    # Case 1: Truncated first attempt -> compact retry succeeds
    call_count = 0

    def _handler_recover(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {"content": '{"reply": "trunc", "recipes": [{"title": '},
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": valid_payload}}
                ]
            },
        )

    provider_ok = HuggingFaceRecipeProvider(
        hf_token="test-token",
        transport=httpx.MockTransport(_handler_recover),
    )
    res_ok = await provider_ok.generate_recipes(
        message="Missi roti",
        inventory_items=[{"id": "1", "name": "Besan", "quantity": 500, "unit": "g"}],
    )
    assert call_count == 2
    assert res_ok.provider_status == "live_hf"
    assert len(res_ok.recipes) == 1
    assert res_ok.recipes[0].title == "Missi Roti"

    # Case 2: Both attempts truncated -> honest fallback_error, never live_hf
    def _handler_always_truncated(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": '{"reply": "broken'},
                    }
                ]
            },
        )

    provider_fail = HuggingFaceRecipeProvider(
        hf_token="test-token",
        transport=httpx.MockTransport(_handler_always_truncated),
    )
    res_fail = await provider_fail.generate_recipes(
        message="Phulka with aloo matar sabzi",
        inventory_items=[{"id": "1", "name": "Whole wheat flour", "quantity": 500, "unit": "g"}],
    )
    assert res_fail.provider_status == "fallback_error"
    assert "Truncated" in res_fail.provider_message or "ValueError" in res_fail.provider_message


@pytest.mark.asyncio
async def test_exact_requested_dish_ranks_above_unrelated_fallback(monkeypatch) -> None:
    async def _fake_inv(*args, **kwargs):
        return [{"id": str(uuid4()), "name": "Potato", "quantity": 500.0, "unit": "g"}]

    async def _fake_history(*args, **kwargs):
        return []

    async def _fake_pref(*args, **kwargs):
        return {}

    async def _fake_refs(*args, **kwargs):
        return []

    async def _fake_invoke(*args, **kwargs):
        return (
            [
                {
                    "title": "Homestyle Paneer Masala Curry",
                    "servings": 2,
                    "prep_minutes": 15,
                    "cook_minutes": 20,
                    "difficulty": "easy",
                    "available_ingredients": [{"name": "Potato", "quantity": "250 g"}],
                    "steps": ["Step 1", "Step 2", "Step 3"],
                },
                {
                    "title": "Aloo Matar Dry Sabzi",
                    "servings": 2,
                    "prep_minutes": 12,
                    "cook_minutes": 18,
                    "difficulty": "easy",
                    "missing_ingredients": [{"name": "Green peas", "quantity": "100 g"}],
                    "steps": ["Step 1", "Step 2", "Step 3"],
                },
            ],
            {"provider_status": "fallback_error", "provider_message": "fallback", "model": "test"},
        )

    monkeypatch.setattr("app.modules.recipes.service._load_active_inventory_payload", _fake_inv)
    monkeypatch.setattr("app.modules.recipes.service._load_recent_cooking_history", _fake_history)
    monkeypatch.setattr(
        "app.modules.recipes.preferences.compute_household_preference_profile", _fake_pref
    )
    monkeypatch.setattr("app.modules.recipes.service._invoke_discover_references", _fake_refs)
    monkeypatch.setattr("app.modules.recipes.service._invoke_provider", _fake_invoke)

    res = await chat_recipes(
        None,  # type: ignore[arg-type]
        household_id=uuid4(),
        settings=Settings(),
        payload=RecipeChatRequest(message="Aloo matar sabzi", servings=2),
    )
    assert res.suggestions[0].title == "Aloo Matar Dry Sabzi"


@pytest.mark.asyncio
async def test_chat_recipes_regenerates_once_on_invalid_llm_output(monkeypatch) -> None:
    calls: list[str] = []

    async def _fake_inv(*args, **kwargs):
        return [
            {
                "id": str(uuid4()),
                "name": "Tomato",
                "quantity": 4.0,
                "unit": "pcs",
                "expiring_soon": False,
            }
        ]

    async def _fake_history(*args, **kwargs):
        return []

    async def _fake_pref(*args, **kwargs):
        return {}

    async def _fake_refs(*args, **kwargs):
        return []

    class _RegenProvider:
        def generate_recipes(self, *, message: str, **kwargs):
            calls.append(message)
            if len(calls) == 1:
                return {
                    "provider_status": "live_hf",
                    "reply": "Draft",
                    "recipes": [
                        {
                            "title": "Quick Tomato Dish",
                            "description": "450 kcal per serving.",
                            "servings": 2,
                            "prep_minutes": 10,
                            "available_ingredients": [{"name": "Tomato", "quantity": "2 pcs"}],
                            "steps": ["Cook until done."],
                            "structured_steps": [],
                        }
                    ],
                }
            return {
                "provider_status": "live_hf",
                "reply": "Repaired executable recipe",
                "recipes": [
                    {
                        "title": "Garlic Roasted Tomato Toast",
                        "description": "Savory roasted tomatoes over crisp toast.",
                        "servings": 2,
                        "prep_minutes": 10,
                        "cook_minutes": 12,
                        "difficulty": "easy",
                        "equipment_required": ["skillet"],
                        "available_ingredients": [{"name": "Tomato", "quantity": "2 pcs"}],
                        "preparation_steps": ["Slice 2 tomatoes into thick rounds."],
                        "structured_steps": [
                            {
                                "step_number": 1,
                                "title": "Warm Skillet",
                                "instruction": (
                                    "Heat the skillet over medium heat for 1 minute until warm."
                                ),
                                "ingredients_used": [],
                                "heat_level": "medium",
                                "duration_minutes": 1,
                                "equipment": ["skillet"],
                                "completion_cue": (
                                    "Pan feels evenly warm when holding palm above it."
                                ),
                            },
                            {
                                "step_number": 2,
                                "title": "Sear Tomatoes",
                                "instruction": (
                                    "Place tomato slices cut-side down and sear 4 minutes "
                                    "per side until blistered."
                                ),
                                "ingredients_used": ["Tomato"],
                                "ingredient_quantities": {"Tomato": "2 pcs"},
                                "heat_level": "medium",
                                "duration_minutes": 8,
                                "equipment": ["skillet"],
                                "completion_cue": (
                                    "Tomatoes are softened with caramelized dark edges."
                                ),
                            },
                            {
                                "step_number": 3,
                                "title": "Plate and Serve",
                                "instruction": (
                                    "Transfer blistered tomatoes onto serving plates and "
                                    "season with salt."
                                ),
                                "ingredients_used": ["Tomato"],
                                "ingredient_quantities": {"Tomato": "2 pcs"},
                                "heat_level": None,
                                "duration_minutes": 1,
                                "equipment": ["plate"],
                                "completion_cue": (
                                    "Tomatoes hold their shape and glisten with juices."
                                ),
                            },
                        ],
                        "steps": [
                            "Warm skillet over medium heat.",
                            "Sear tomato slices 4 minutes per side until blistered.",
                            "Plate and serve warm.",
                        ],
                        "troubleshooting_tips": ["Lower heat if tomatoes darken too quickly."],
                        "serving_result": "Juicy, caramelized tomato slices.",
                    }
                ],
            }

    monkeypatch.setattr("app.modules.recipes.service._load_active_inventory_payload", _fake_inv)
    monkeypatch.setattr("app.modules.recipes.service._load_recent_cooking_history", _fake_history)
    monkeypatch.setattr(
        "app.modules.recipes.preferences.compute_household_preference_profile", _fake_pref
    )
    monkeypatch.setattr("app.modules.recipes.service._invoke_discover_references", _fake_refs)
    monkeypatch.setattr(
        "app.modules.recipes.service._build_provider", lambda settings: _RegenProvider()
    )

    res = await chat_recipes(
        None,  # type: ignore[arg-type]
        household_id=uuid4(),
        settings=Settings(),
        payload=RecipeChatRequest(message="Make something with tomatoes", servings=2),
    )
    assert len(calls) == 2
    assert "[QUALITY REPAIR]" in calls[1]
    assert len(res.suggestions) == 1
    assert res.suggestions[0].title == "Garlic Roasted Tomato Toast"
    assert res.suggestions[0].quality_warnings == []
    assert res.suggestions[0].equipment_required == ["skillet"]
    assert res.suggestions[0].serving_result == "Juicy, caramelized tomato slices."
