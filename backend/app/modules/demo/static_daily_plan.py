# ruff: noqa: E501
"""Curated static 8-recipe daily meal plan for the isolated public demo household.

Every recipe is deterministic, contains zero PII, requires zero live LLM calls, and is
validated through the application's actual ``validate_recipe_quality(..., strict_ai_generated=True)``
and ``compute_recipe_availability(...)`` functions at build time.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid5

from app.modules.discovery.daily_generator import (
    DAILY_CATEGORIES,
    _slugify,
    compute_recipe_availability,
)
from app.modules.recipes.quality_validator import validate_recipe_quality

DEMO_STATIC_PLAN_NAMESPACE = UUID("8b7f5a3e-6f7b-4f9e-a12a-4c3e89f2e101")


def _curated_eight_recipes() -> list[dict[str, Any]]:
    """Return 8 complete, validated recipes (2 per meal category)."""
    return [
        # --- BREAKFAST (2) ---
        {
            "meal_category": "breakfast",
            "position": 1,
            "title": "Vegetable Poha with Peanuts",
            "description": "Light flattened rice tempered with mustard seeds, turmeric, onion, and roasted peanuts.",
            "servings": 2,
            "prep_minutes": 10,
            "cook_minutes": 12,
            "difficulty": "easy",
            "cuisine": "Indian",
            "dietary_tags": ["vegetarian", "quick"],
            "equipment_required": ["kadhai", "strainer", "spatula"],
            "ingredients": [
                {
                    "name": "Poha",
                    "quantity": "150 g",
                    "is_optional": False,
                    "used_in_steps": [1, 3],
                },
                {"name": "Onion", "quantity": "80 g", "is_optional": False, "used_in_steps": [2]},
                {"name": "Peanuts", "quantity": "30 g", "is_optional": False, "used_in_steps": [2]},
                {
                    "name": "Cooking Oil",
                    "quantity": "15 ml",
                    "is_optional": False,
                    "used_in_steps": [2],
                },
                {
                    "name": "Mustard Seeds",
                    "quantity": "3 g",
                    "is_optional": False,
                    "used_in_steps": [2],
                },
                {
                    "name": "Turmeric Powder",
                    "quantity": "2 g",
                    "is_optional": False,
                    "used_in_steps": [2],
                },
                {"name": "Salt", "quantity": "4 g", "is_optional": False, "used_in_steps": [3]},
                {
                    "name": "Lemon Juice",
                    "quantity": "10 ml",
                    "is_optional": False,
                    "used_in_steps": [3],
                },
            ],
            "preparation_steps": [
                "Finely chop 80 g onion and measure all spices before heating the pan.",
            ],
            "structured_steps": [
                {
                    "step_number": 1,
                    "title": "Rinse and soften poha",
                    "instruction": "Rinse 150 g poha in a strainer under cool water for 20 seconds, drain thoroughly, and let it rest for 5 minutes until grains separate.",
                    "ingredients_used": ["Poha"],
                    "ingredient_quantities": {"Poha": "150 g"},
                    "duration_minutes": 5,
                    "heat_level": None,
                    "technique": "rinse",
                    "equipment": ["strainer"],
                    "completion_cue": "Poha grains are soft yet separate without clumping.",
                    "caution": "Do not soak poha in standing water or it will turn mushy.",
                    "next_action": "Heat oil in the kadhai for tempering.",
                },
                {
                    "step_number": 2,
                    "title": "Temper mustard, peanuts, and onion",
                    "instruction": "Heat 15 ml cooking oil in a kadhai over medium heat, fry 30 g peanuts for 2 minutes, splutter 3 g mustard seeds, then saute 80 g onion with 2 g turmeric powder for 3 minutes.",
                    "ingredients_used": [
                        "Cooking Oil",
                        "Peanuts",
                        "Mustard Seeds",
                        "Onion",
                        "Turmeric Powder",
                    ],
                    "ingredient_quantities": {
                        "Cooking Oil": "15 ml",
                        "Peanuts": "30 g",
                        "Mustard Seeds": "3 g",
                        "Onion": "80 g",
                        "Turmeric Powder": "2 g",
                    },
                    "duration_minutes": 5,
                    "heat_level": "medium",
                    "technique": "temper",
                    "equipment": ["kadhai", "spatula"],
                    "completion_cue": "Onions turn translucent and peanuts look golden and crisp.",
                    "caution": "Keep heat at medium so turmeric powder does not scorch.",
                    "next_action": "Fold in the rinsed poha.",
                },
                {
                    "step_number": 3,
                    "title": "Fold poha and finish with lemon",
                    "instruction": "Add the softened 150 g poha and 4 g salt, stir gently on low heat for 2 minutes until warmed through, then drizzle 10 ml lemon juice off heat and serve warm.",
                    "ingredients_used": ["Poha", "Salt", "Lemon Juice"],
                    "ingredient_quantities": {
                        "Poha": "150 g",
                        "Salt": "4 g",
                        "Lemon Juice": "10 ml",
                    },
                    "duration_minutes": 2,
                    "heat_level": "low",
                    "technique": "fold",
                    "equipment": ["kadhai", "spatula"],
                    "completion_cue": "Every grain is evenly tinted yellow and steaming hot.",
                    "caution": "Turn off heat before adding lemon juice to preserve bright flavor.",
                    "next_action": "Serve immediately in warm bowls.",
                },
            ],
            "substitutions": ["Swap peanuts with roasted cashews if preferred."],
            "cooking_tips": ["Use medium-thick poha for fluffy non-sticky texture."],
            "troubleshooting_tips": ["Sprinkle 10 ml water and cover 1 minute if poha feels dry."],
            "serving_result": "Fluffy, golden turmeric poha with crunchy peanuts.",
            "food_safety_notes": [],
        },
        {
            "meal_category": "breakfast",
            "position": 2,
            "title": "Besan Chilla with Onion",
            "description": "Savory spiced chickpea flour pancakes cooked crisp on a lightly oiled tawa.",
            "servings": 2,
            "prep_minutes": 10,
            "cook_minutes": 12,
            "difficulty": "easy",
            "cuisine": "Indian",
            "dietary_tags": ["vegetarian", "high-protein"],
            "equipment_required": ["mixing bowl", "whisk", "tawa", "spatula"],
            "ingredients": [
                {"name": "Besan", "quantity": "120 g", "is_optional": False, "used_in_steps": [1]},
                {"name": "Water", "quantity": "180 ml", "is_optional": False, "used_in_steps": [1]},
                {"name": "Onion", "quantity": "60 g", "is_optional": False, "used_in_steps": [1]},
                {
                    "name": "Turmeric Powder",
                    "quantity": "2 g",
                    "is_optional": False,
                    "used_in_steps": [1],
                },
                {"name": "Salt", "quantity": "4 g", "is_optional": False, "used_in_steps": [1]},
                {
                    "name": "Cooking Oil",
                    "quantity": "15 ml",
                    "is_optional": False,
                    "used_in_steps": [2, 3],
                },
            ],
            "preparation_steps": [
                "Finely dice 60 g onion so the chilla spreads evenly on the tawa.",
            ],
            "structured_steps": [
                {
                    "step_number": 1,
                    "title": "Whisk smooth besan batter",
                    "instruction": "Whisk 120 g besan, 180 ml water, 2 g turmeric powder, 4 g salt, and 60 g finely diced onion in a mixing bowl until a smooth pourable batter forms, then rest for 5 minutes.",
                    "ingredients_used": ["Besan", "Water", "Turmeric Powder", "Salt", "Onion"],
                    "ingredient_quantities": {
                        "Besan": "120 g",
                        "Water": "180 ml",
                        "Turmeric Powder": "2 g",
                        "Salt": "4 g",
                        "Onion": "60 g",
                    },
                    "duration_minutes": 5,
                    "heat_level": None,
                    "technique": "whisk",
                    "equipment": ["mixing bowl", "whisk"],
                    "completion_cue": "Batter is lump-free and coats the back of a ladle.",
                    "caution": "Add water gradually so the batter does not become too thin.",
                    "next_action": "Preheat the tawa over medium heat.",
                },
                {
                    "step_number": 2,
                    "title": "Pour and spread batter on tawa",
                    "instruction": "Brush 8 ml cooking oil onto a hot tawa over medium heat, ladle half the batter onto the center, spread into a thin circle, and cook for 3 minutes until edges lift.",
                    "ingredients_used": ["Cooking Oil"],
                    "ingredient_quantities": {"Cooking Oil": "8 ml"},
                    "duration_minutes": 3,
                    "heat_level": "medium",
                    "technique": "spread",
                    "equipment": ["tawa", "spatula"],
                    "completion_cue": "Top surface looks matte and edges release cleanly from the tawa.",
                    "caution": "Keep heat moderate so the center cooks without burning the base.",
                    "next_action": "Flip the chilla to crisp the second side.",
                },
                {
                    "step_number": 3,
                    "title": "Flip and crisp both sides",
                    "instruction": "Drizzle the remaining 7 ml cooking oil around the edges, flip the chilla with a spatula, and cook over medium heat for 2 minutes until golden brown spots appear on both sides.",
                    "ingredients_used": ["Cooking Oil"],
                    "ingredient_quantities": {"Cooking Oil": "7 ml"},
                    "duration_minutes": 2,
                    "heat_level": "medium",
                    "technique": "flip",
                    "equipment": ["tawa", "spatula"],
                    "completion_cue": "Both sides are crisp, golden brown, and cooked through.",
                    "caution": "Press lightly with the spatula so the onion-filled center cooks evenly.",
                    "next_action": "Repeat with remaining batter and serve hot.",
                },
            ],
            "substitutions": ["Use moong dal batter in place of besan for a lighter chilla."],
            "cooking_tips": ["Resting the batter for 5 minutes prevents raw chickpea flavor."],
            "troubleshooting_tips": [
                "If batter sticks, wipe the tawa with a drop of oil and lower heat slightly."
            ],
            "serving_result": "Crisp golden savory besan chillas with sweet caramelized onion.",
            "food_safety_notes": [],
        },
        # --- LUNCH (2) ---
        {
            "meal_category": "lunch",
            "position": 1,
            "title": "Yellow Moong Dal Tadka",
            "description": "Comforting pressure-cooked yellow moong dal finished with aromatic cumin and garlic ghee tempering.",
            "servings": 2,
            "prep_minutes": 10,
            "cook_minutes": 20,
            "difficulty": "easy",
            "cuisine": "Indian",
            "dietary_tags": ["vegetarian", "gluten-free"],
            "equipment_required": ["pressure cooker", "tadka pan", "ladle"],
            "ingredients": [
                {
                    "name": "Moong Dal",
                    "quantity": "150 g",
                    "is_optional": False,
                    "used_in_steps": [1, 3],
                },
                {"name": "Water", "quantity": "450 ml", "is_optional": False, "used_in_steps": [1]},
                {
                    "name": "Turmeric Powder",
                    "quantity": "2 g",
                    "is_optional": False,
                    "used_in_steps": [1],
                },
                {"name": "Salt", "quantity": "5 g", "is_optional": False, "used_in_steps": [1]},
                {"name": "Ghee", "quantity": "15 ml", "is_optional": False, "used_in_steps": [2]},
                {
                    "name": "Cumin Seeds",
                    "quantity": "4 g",
                    "is_optional": False,
                    "used_in_steps": [2],
                },
                {"name": "Garlic", "quantity": "10 g", "is_optional": False, "used_in_steps": [2]},
                {"name": "Tomato", "quantity": "80 g", "is_optional": False, "used_in_steps": [2]},
            ],
            "preparation_steps": [
                "Rinse 150 g moong dal twice in cool water, mince 10 g garlic, and chop 80 g tomato.",
            ],
            "structured_steps": [
                {
                    "step_number": 1,
                    "title": "Pressure cook moong dal",
                    "instruction": "Combine rinsed 150 g moong dal, 450 ml water, 2 g turmeric powder, and 5 g salt in a pressure cooker and cook on medium-high heat for 10 minutes until 2 whistles sound, then release pressure naturally.",
                    "ingredients_used": ["Moong Dal", "Water", "Turmeric Powder", "Salt"],
                    "ingredient_quantities": {
                        "Moong Dal": "150 g",
                        "Water": "450 ml",
                        "Turmeric Powder": "2 g",
                        "Salt": "5 g",
                    },
                    "duration_minutes": 10,
                    "heat_level": "medium-high",
                    "technique": "pressure",
                    "equipment": ["pressure cooker"],
                    "completion_cue": "Dal is creamy and mashes easily against the side of the pot.",
                    "caution": "Allow steam to subside fully before opening the pressure cooker lid.",
                    "next_action": "Prepare the ghee tempering in a tadka pan.",
                },
                {
                    "step_number": 2,
                    "title": "Prepare cumin and garlic tempering",
                    "instruction": "Heat 15 ml ghee in a tadka pan over medium heat, temper 4 g cumin seeds for 30 seconds until fragrant, saute 10 g minced garlic for 1 minute, then cook 80 g chopped tomato for 4 minutes until soft.",
                    "ingredients_used": ["Ghee", "Cumin Seeds", "Garlic", "Tomato"],
                    "ingredient_quantities": {
                        "Ghee": "15 ml",
                        "Cumin Seeds": "4 g",
                        "Garlic": "10 g",
                        "Tomato": "80 g",
                    },
                    "duration_minutes": 5,
                    "heat_level": "medium",
                    "technique": "temper",
                    "equipment": ["tadka pan", "ladle"],
                    "completion_cue": "Tomatoes soften into a glossy spiced base and garlic is golden.",
                    "caution": "Do not let garlic turn dark brown or the tadka will taste bitter.",
                    "next_action": "Pour the tempering into the cooked dal.",
                },
                {
                    "step_number": 3,
                    "title": "Simmer tempered dal together",
                    "instruction": "Pour the hot tempering over the cooked 150 g moong dal and simmer on low heat for 4 minutes, stirring with a ladle until smooth and aromatic.",
                    "ingredients_used": ["Moong Dal"],
                    "ingredient_quantities": {"Moong Dal": "150 g"},
                    "duration_minutes": 4,
                    "heat_level": "low",
                    "technique": "simmer",
                    "equipment": ["pressure cooker", "ladle"],
                    "completion_cue": "Dal coats the ladle lightly with visible golden ghee swirls.",
                    "caution": "Stir occasionally so the thick dal does not stick to the base.",
                    "next_action": "Serve hot with steamed rice or chapati.",
                },
            ],
            "substitutions": ["Use cooking oil instead of ghee for a vegan dal tadka."],
            "cooking_tips": ["Add 50 ml warm water if the dal thickens while resting."],
            "troubleshooting_tips": [
                "Whisk gently with a ladle if lentils look separated from broth."
            ],
            "serving_result": "Velvety yellow moong dal fragrant with roasted cumin and garlic.",
            "food_safety_notes": [],
        },
        {
            "meal_category": "lunch",
            "position": 2,
            "title": "Aloo Matar Sabzi",
            "description": "Classic home-style spiced potato and green peas curry simmered in a tomato-onion gravy.",
            "servings": 2,
            "prep_minutes": 10,
            "cook_minutes": 20,
            "difficulty": "easy",
            "cuisine": "Indian",
            "dietary_tags": ["vegetarian", "vegan"],
            "equipment_required": ["kadhai", "spatula", "lid"],
            "ingredients": [
                {
                    "name": "Potato",
                    "quantity": "200 g",
                    "is_optional": False,
                    "used_in_steps": [2, 3],
                },
                {
                    "name": "Green Peas",
                    "quantity": "100 g",
                    "is_optional": False,
                    "used_in_steps": [2, 3],
                },
                {"name": "Onion", "quantity": "80 g", "is_optional": False, "used_in_steps": [1]},
                {"name": "Tomato", "quantity": "100 g", "is_optional": False, "used_in_steps": [1]},
                {
                    "name": "Cooking Oil",
                    "quantity": "15 ml",
                    "is_optional": False,
                    "used_in_steps": [1],
                },
                {
                    "name": "Cumin Seeds",
                    "quantity": "3 g",
                    "is_optional": False,
                    "used_in_steps": [1],
                },
                {
                    "name": "Turmeric Powder",
                    "quantity": "2 g",
                    "is_optional": False,
                    "used_in_steps": [1],
                },
                {"name": "Salt", "quantity": "5 g", "is_optional": False, "used_in_steps": [2]},
                {"name": "Water", "quantity": "150 ml", "is_optional": False, "used_in_steps": [3]},
            ],
            "preparation_steps": [
                "Peel and cube 200 g potato into 2 cm pieces; finely chop 80 g onion and 100 g tomato.",
            ],
            "structured_steps": [
                {
                    "step_number": 1,
                    "title": "Saute cumin, onion, and tomato base",
                    "instruction": "Heat 15 ml cooking oil in a kadhai over medium heat, crackle 3 g cumin seeds, saute 80 g chopped onion for 4 minutes, then add 100 g tomato and 2 g turmeric powder and cook for 4 minutes until softened.",
                    "ingredients_used": [
                        "Cooking Oil",
                        "Cumin Seeds",
                        "Onion",
                        "Tomato",
                        "Turmeric Powder",
                    ],
                    "ingredient_quantities": {
                        "Cooking Oil": "15 ml",
                        "Cumin Seeds": "3 g",
                        "Onion": "80 g",
                        "Tomato": "100 g",
                        "Turmeric Powder": "2 g",
                    },
                    "duration_minutes": 8,
                    "heat_level": "medium",
                    "technique": "saute",
                    "equipment": ["kadhai", "spatula"],
                    "completion_cue": "Tomatoes break down into a thick glossy masala base.",
                    "caution": "Stir steadily so the onion-tomato base cooks evenly without sticking.",
                    "next_action": "Add cubed potatoes and green peas.",
                },
                {
                    "step_number": 2,
                    "title": "Coat potatoes and peas in masala",
                    "instruction": "Add 200 g cubed potato, 100 g green peas, and 5 g salt to the kadhai and saute over medium heat for 2 minutes until every piece is coated in the spiced base.",
                    "ingredients_used": ["Potato", "Green Peas", "Salt"],
                    "ingredient_quantities": {
                        "Potato": "200 g",
                        "Green Peas": "100 g",
                        "Salt": "5 g",
                    },
                    "duration_minutes": 2,
                    "heat_level": "medium",
                    "technique": "saute",
                    "equipment": ["kadhai", "spatula"],
                    "completion_cue": "Potatoes and peas are evenly coated with fragrant yellow masala.",
                    "caution": "Keep cubes uniform so all potatoes finish cooking together.",
                    "next_action": "Add water and cover to simmer.",
                },
                {
                    "step_number": 3,
                    "title": "Cover and simmer until tender",
                    "instruction": "Pour in 150 ml water, cover with a lid, and simmer on medium-low heat for 10 minutes until the 200 g potato cubes and 100 g green peas are fork-tender.",
                    "ingredients_used": ["Water", "Potato", "Green Peas"],
                    "ingredient_quantities": {
                        "Water": "150 ml",
                        "Potato": "200 g",
                        "Green Peas": "100 g",
                    },
                    "duration_minutes": 10,
                    "heat_level": "medium-low",
                    "technique": "simmer",
                    "equipment": ["kadhai", "lid"],
                    "completion_cue": "A knife slides smoothly through a potato cube and gravy clings lightly.",
                    "caution": "Check once halfway and add 30 ml water if the pan looks dry.",
                    "next_action": "Serve hot with warm chapati or steamed rice.",
                },
            ],
            "substitutions": [
                "Use frozen green peas thawed in warm water if fresh peas are out of season."
            ],
            "cooking_tips": [
                "Lightly mash 2 potato cubes at the end to naturally thicken the gravy."
            ],
            "troubleshooting_tips": [
                "Simmer uncovered for 2 minutes if the gravy is thinner than desired."
            ],
            "serving_result": "Tender spiced potatoes and sweet green peas in a rich home-style gravy.",
            "food_safety_notes": [],
        },
        # --- SNACKS (2) ---
        {
            "meal_category": "snacks",
            "position": 1,
            "title": "Cucumber Tomato Kachumber Salad",
            "description": "Crisp diced cucumber, tomato, and onion tossed with roasted cumin, salt, and fresh lemon juice.",
            "servings": 2,
            "prep_minutes": 10,
            "cook_minutes": 0,
            "difficulty": "easy",
            "cuisine": "Indian",
            "dietary_tags": ["vegetarian", "vegan", "no-cook"],
            "equipment_required": ["cutting board", "knife", "mixing bowl"],
            "ingredients": [
                {
                    "name": "Cucumber",
                    "quantity": "150 g",
                    "is_optional": False,
                    "used_in_steps": [1, 2],
                },
                {
                    "name": "Tomato",
                    "quantity": "100 g",
                    "is_optional": False,
                    "used_in_steps": [1, 2],
                },
                {
                    "name": "Onion",
                    "quantity": "50 g",
                    "is_optional": False,
                    "used_in_steps": [1, 2],
                },
                {"name": "Salt", "quantity": "3 g", "is_optional": False, "used_in_steps": [2]},
                {
                    "name": "Cumin Powder",
                    "quantity": "2 g",
                    "is_optional": False,
                    "used_in_steps": [2],
                },
                {
                    "name": "Lemon Juice",
                    "quantity": "10 ml",
                    "is_optional": False,
                    "used_in_steps": [2],
                },
            ],
            "preparation_steps": [
                "Wash 150 g cucumber and 100 g tomato thoroughly under running water.",
            ],
            "structured_steps": [
                {
                    "step_number": 1,
                    "title": "Dice fresh vegetables uniformly",
                    "instruction": "Finely dice 150 g cucumber, 100 g tomato, and 50 g onion into crisp 5 mm cubes on a clean cutting board.",
                    "ingredients_used": ["Cucumber", "Tomato", "Onion"],
                    "ingredient_quantities": {
                        "Cucumber": "150 g",
                        "Tomato": "100 g",
                        "Onion": "50 g",
                    },
                    "duration_minutes": 6,
                    "heat_level": None,
                    "technique": "chop",
                    "equipment": ["cutting board", "knife"],
                    "completion_cue": "All vegetables are evenly diced into bite-sized crisp cubes.",
                    "caution": "Remove watery tomato seeds if prepping more than 10 minutes ahead.",
                    "next_action": "Transfer vegetables to a mixing bowl for seasoning.",
                },
                {
                    "step_number": 2,
                    "title": "Toss with cumin, salt, and lemon",
                    "instruction": "Combine the diced 150 g cucumber, 100 g tomato, and 50 g onion in a bowl, sprinkle 3 g salt and 2 g cumin powder, drizzle 10 ml lemon juice, and toss gently for 1 minute.",
                    "ingredients_used": [
                        "Cucumber",
                        "Tomato",
                        "Onion",
                        "Salt",
                        "Cumin Powder",
                        "Lemon Juice",
                    ],
                    "ingredient_quantities": {
                        "Cucumber": "150 g",
                        "Tomato": "100 g",
                        "Onion": "50 g",
                        "Salt": "3 g",
                        "Cumin Powder": "2 g",
                        "Lemon Juice": "10 ml",
                    },
                    "duration_minutes": 2,
                    "heat_level": None,
                    "technique": "toss",
                    "equipment": ["mixing bowl"],
                    "completion_cue": "Vegetables glisten evenly with citrus dressing and toasted cumin.",
                    "caution": "Add salt right before serving so cucumbers stay crunchy.",
                    "next_action": "Plate in chilled bowls.",
                },
                {
                    "step_number": 3,
                    "title": "Rest briefly and serve chilled",
                    "instruction": "Let the tossed kachumber salad rest for 2 minutes so flavors meld, then spoon into two serving bowls and enjoy fresh.",
                    "ingredients_used": ["Cucumber", "Tomato", "Onion"],
                    "ingredient_quantities": {
                        "Cucumber": "150 g",
                        "Tomato": "100 g",
                        "Onion": "50 g",
                    },
                    "duration_minutes": 2,
                    "heat_level": None,
                    "technique": "serve",
                    "equipment": ["mixing bowl"],
                    "completion_cue": "Salad looks bright, crisp, and lightly dressed without pooled water.",
                    "caution": "Consume fresh within 30 minutes for best crunch.",
                    "next_action": "Serve as an afternoon snack or lunch accompaniment.",
                },
            ],
            "substitutions": ["Add 40 g diced carrot for extra sweetness and crunch."],
            "cooking_tips": ["Soak diced onion in cold water for 3 minutes for a milder bite."],
            "troubleshooting_tips": ["Drain excess liquid if salad sits too long after salting."],
            "serving_result": "Crisp, tangy, refreshing kachumber salad.",
            "food_safety_notes": [],
        },
        {
            "meal_category": "snacks",
            "position": 2,
            "title": "Banana Almond Milk Smoothie",
            "description": "Creamy chilled banana and milk smoothie blended with toasted almonds and a pinch of cardamom.",
            "servings": 2,
            "prep_minutes": 5,
            "cook_minutes": 0,
            "difficulty": "easy",
            "cuisine": "Indian",
            "dietary_tags": ["vegetarian", "no-cook"],
            "equipment_required": ["blender", "knife"],
            "ingredients": [
                {
                    "name": "Banana",
                    "quantity": "160 g",
                    "is_optional": False,
                    "used_in_steps": [1, 2],
                },
                {
                    "name": "Milk",
                    "quantity": "300 ml",
                    "is_optional": False,
                    "used_in_steps": [1, 2],
                },
                {
                    "name": "Almonds",
                    "quantity": "20 g",
                    "is_optional": False,
                    "used_in_steps": [1, 2],
                },
                {
                    "name": "Cardamom Powder",
                    "quantity": "1 g",
                    "is_optional": False,
                    "used_in_steps": [2],
                },
            ],
            "preparation_steps": [
                "Peel and slice 160 g ripe bananas into thick rounds.",
            ],
            "structured_steps": [
                {
                    "step_number": 1,
                    "title": "Combine banana, milk, and almonds",
                    "instruction": "Add 160 g sliced banana, 300 ml chilled milk, and 20 g almonds into a clean blender jar.",
                    "ingredients_used": ["Banana", "Milk", "Almonds"],
                    "ingredient_quantities": {
                        "Banana": "160 g",
                        "Milk": "300 ml",
                        "Almonds": "20 g",
                    },
                    "duration_minutes": 2,
                    "heat_level": None,
                    "technique": "combine",
                    "equipment": ["blender"],
                    "completion_cue": "All smoothie ingredients sit below the blender max-fill line.",
                    "caution": "Ensure blender lid is tightly sealed before starting.",
                    "next_action": "Blend until completely smooth.",
                },
                {
                    "step_number": 2,
                    "title": "Blend smooth with cardamom and pour",
                    "instruction": "Add 1 g cardamom powder and blend the 160 g banana, 300 ml milk, and 20 g almonds on high for 2 minutes until silky smooth, then pour into two chilled glasses and serve.",
                    "ingredients_used": ["Cardamom Powder", "Banana", "Milk", "Almonds"],
                    "ingredient_quantities": {
                        "Cardamom Powder": "1 g",
                        "Banana": "160 g",
                        "Milk": "300 ml",
                        "Almonds": "20 g",
                    },
                    "duration_minutes": 2,
                    "heat_level": None,
                    "technique": "blend",
                    "equipment": ["blender"],
                    "completion_cue": "Smoothie is frothy, velvety, and free of almond grit.",
                    "caution": "Serve immediately so banana does not oxidize.",
                    "next_action": "Enjoy chilled as an energizing snack.",
                },
            ],
            "substitutions": [
                "Use curd thinned with 50 ml cold water instead of milk for a tangy lassi style."
            ],
            "cooking_tips": [
                "Freeze sliced banana for 30 minutes for an extra-thick milkshake texture."
            ],
            "troubleshooting_tips": ["Blend an extra 30 seconds if almond pieces remain coarse."],
            "serving_result": "Frothy, naturally sweet banana almond smoothie with fragrant cardamom.",
            "food_safety_notes": [],
        },
        # --- DINNER (2) ---
        {
            "meal_category": "dinner",
            "position": 1,
            "title": "Paneer Bhurji",
            "description": "Quick crumbled paneer scrambled with onion, tomato, turmeric, and cumin.",
            "servings": 2,
            "prep_minutes": 10,
            "cook_minutes": 12,
            "difficulty": "easy",
            "cuisine": "Indian",
            "dietary_tags": ["vegetarian", "high-protein"],
            "equipment_required": ["pan", "spatula"],
            "ingredients": [
                {
                    "name": "Paneer",
                    "quantity": "200 g",
                    "is_optional": False,
                    "used_in_steps": [2, 3],
                },
                {"name": "Onion", "quantity": "80 g", "is_optional": False, "used_in_steps": [1]},
                {"name": "Tomato", "quantity": "100 g", "is_optional": False, "used_in_steps": [1]},
                {
                    "name": "Cooking Oil",
                    "quantity": "15 ml",
                    "is_optional": False,
                    "used_in_steps": [1],
                },
                {
                    "name": "Cumin Seeds",
                    "quantity": "3 g",
                    "is_optional": False,
                    "used_in_steps": [1],
                },
                {
                    "name": "Turmeric Powder",
                    "quantity": "2 g",
                    "is_optional": False,
                    "used_in_steps": [1],
                },
                {"name": "Salt", "quantity": "4 g", "is_optional": False, "used_in_steps": [2]},
            ],
            "preparation_steps": [
                "Crumble 200 g paneer evenly with clean fingers and finely chop 80 g onion and 100 g tomato.",
            ],
            "structured_steps": [
                {
                    "step_number": 1,
                    "title": "Saute cumin, onion, and tomato",
                    "instruction": "Heat 15 ml cooking oil in a pan over medium heat, crackle 3 g cumin seeds, saute 80 g onion for 4 minutes until translucent, then add 100 g tomato and 2 g turmeric powder and cook for 4 minutes.",
                    "ingredients_used": [
                        "Cooking Oil",
                        "Cumin Seeds",
                        "Onion",
                        "Tomato",
                        "Turmeric Powder",
                    ],
                    "ingredient_quantities": {
                        "Cooking Oil": "15 ml",
                        "Cumin Seeds": "3 g",
                        "Onion": "80 g",
                        "Tomato": "100 g",
                        "Turmeric Powder": "2 g",
                    },
                    "duration_minutes": 8,
                    "heat_level": "medium",
                    "technique": "saute",
                    "equipment": ["pan", "spatula"],
                    "completion_cue": "Tomatoes soften completely and oil glistens around the edges.",
                    "caution": "Keep heat at medium so onion browns gently without scorching.",
                    "next_action": "Fold in the crumbled paneer.",
                },
                {
                    "step_number": 2,
                    "title": "Fold crumbled paneer and salt",
                    "instruction": "Add 200 g crumbled paneer and 4 g salt to the pan, stirring gently over medium-low heat for 3 minutes until the paneer absorbs the spiced tomato base.",
                    "ingredients_used": ["Paneer", "Salt"],
                    "ingredient_quantities": {"Paneer": "200 g", "Salt": "4 g"},
                    "duration_minutes": 3,
                    "heat_level": "medium-low",
                    "technique": "fold",
                    "equipment": ["pan", "spatula"],
                    "completion_cue": "Paneer is evenly tinted golden and piping hot throughout.",
                    "caution": "Do not overcook paneer or it will lose moisture and turn chewy.",
                    "next_action": "Remove from heat and serve warm.",
                },
                {
                    "step_number": 3,
                    "title": "Finish and serve paneer bhurji",
                    "instruction": "Turn off heat, fluff the 200 g paneer bhurji lightly with a spatula for 1 minute, and transfer to a warm serving dish.",
                    "ingredients_used": ["Paneer"],
                    "ingredient_quantities": {"Paneer": "200 g"},
                    "duration_minutes": 1,
                    "heat_level": None,
                    "technique": "serve",
                    "equipment": ["pan", "spatula"],
                    "completion_cue": "Bhurji looks soft, moist, and aromatic with visible golden curds.",
                    "caution": "Serve immediately while paneer stays tender.",
                    "next_action": "Pair with warm chapati or toasted bread.",
                },
            ],
            "substitutions": ["Replace paneer with 200 g crumbled firm tofu for a vegan scramble."],
            "cooking_tips": [
                "Soak store-bought paneer in warm water for 5 minutes before crumbling for extra softness."
            ],
            "troubleshooting_tips": ["Splash 15 ml milk if the bhurji feels dry at the end."],
            "serving_result": "Soft, savory, spiced paneer scramble ready in 20 minutes.",
            "food_safety_notes": [],
        },
        {
            "meal_category": "dinner",
            "position": 2,
            "title": "Jeera Rice",
            "description": "Fragrant long-grain basmati rice tempered with whole cumin seeds in ghee and steamed fluffy.",
            "servings": 2,
            "prep_minutes": 15,
            "cook_minutes": 15,
            "difficulty": "easy",
            "cuisine": "Indian",
            "dietary_tags": ["vegetarian", "gluten-free"],
            "equipment_required": ["saucepan", "lid", "fork"],
            "ingredients": [
                {
                    "name": "Basmati Rice",
                    "quantity": "150 g",
                    "is_optional": False,
                    "used_in_steps": [1, 2, 3],
                },
                {"name": "Water", "quantity": "300 ml", "is_optional": False, "used_in_steps": [2]},
                {"name": "Ghee", "quantity": "10 ml", "is_optional": False, "used_in_steps": [2]},
                {
                    "name": "Cumin Seeds",
                    "quantity": "5 g",
                    "is_optional": False,
                    "used_in_steps": [2],
                },
                {"name": "Salt", "quantity": "4 g", "is_optional": False, "used_in_steps": [2]},
            ],
            "preparation_steps": [
                "Measure 150 g basmati rice and 300 ml water before heating the saucepan.",
            ],
            "structured_steps": [
                {
                    "step_number": 1,
                    "title": "Rinse and soak basmati rice",
                    "instruction": "Rinse 150 g basmati rice three times until water runs mostly clear, soak for 15 minutes, and drain well.",
                    "ingredients_used": ["Basmati Rice"],
                    "ingredient_quantities": {"Basmati Rice": "150 g"},
                    "duration_minutes": 15,
                    "heat_level": None,
                    "technique": "soak",
                    "equipment": ["saucepan"],
                    "completion_cue": "Rice grains appear opaque white and thoroughly drained.",
                    "caution": "Handle soaked rice gently so long grains do not break.",
                    "next_action": "Temper cumin seeds in ghee.",
                },
                {
                    "step_number": 2,
                    "title": "Temper cumin and simmer rice covered",
                    "instruction": "Heat 10 ml ghee in a saucepan over medium heat, crackle 5 g cumin seeds for 30 seconds, stir in drained 150 g basmati rice, 300 ml water, and 4 g salt, bring to a boil, then cover and simmer on low heat for 12 minutes.",
                    "ingredients_used": ["Ghee", "Cumin Seeds", "Basmati Rice", "Water", "Salt"],
                    "ingredient_quantities": {
                        "Ghee": "10 ml",
                        "Cumin Seeds": "5 g",
                        "Basmati Rice": "150 g",
                        "Water": "300 ml",
                        "Salt": "4 g",
                    },
                    "duration_minutes": 12,
                    "heat_level": "low",
                    "technique": "simmer",
                    "equipment": ["saucepan", "lid"],
                    "completion_cue": "Water is absorbed completely and tiny steam holes appear on the surface.",
                    "caution": "Keep the lid closed during simmering so steam does not escape.",
                    "next_action": "Rest covered off heat before fluffing.",
                },
                {
                    "step_number": 3,
                    "title": "Rest and fluff jeera rice",
                    "instruction": "Turn off heat, let the 150 g basmati rice rest covered for 5 minutes, then gently fluff the grains with a fork before serving.",
                    "ingredients_used": ["Basmati Rice"],
                    "ingredient_quantities": {"Basmati Rice": "150 g"},
                    "duration_minutes": 5,
                    "heat_level": None,
                    "technique": "rest",
                    "equipment": ["saucepan", "fork"],
                    "completion_cue": "Each rice grain is long, separate, and speckled with toasted cumin.",
                    "caution": "Use a fork rather than a heavy spoon so grains stay intact.",
                    "next_action": "Serve warm alongside dal or sabzi.",
                },
            ],
            "substitutions": ["Use cooking oil instead of ghee for a vegan jeera rice."],
            "cooking_tips": ["A 1:2 rice-to-water ratio yields fluffy separate grains."],
            "troubleshooting_tips": [
                "If grains feel slightly firm, cover for 3 more minutes off heat."
            ],
            "serving_result": "Fluffy, aromatic basmati jeera rice with toasted cumin seeds.",
            "food_safety_notes": [],
        },
    ]


def build_static_demo_daily_plan(
    *,
    demo_household_id: UUID,
    raw_inventory_payload: list[dict[str, Any]],
    plan_date: date | None = None,
    now_utc: datetime | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build and strictly validate the static 8-recipe ready daily meal plan for Demo Kitchen.

    Raises ValueError if any curated recipe fails ``validate_recipe_quality(..., strict_ai_generated=True)``.
    """
    ref_now = now_utc or datetime.now(UTC)
    target_date = plan_date or ref_now.date()
    plan_id = uuid5(
        DEMO_STATIC_PLAN_NAMESPACE,
        f"daily_meal_plan:{demo_household_id}:{target_date.isoformat()}",
    )

    curated = _curated_eight_recipes()
    counts_by_cat = {cat: 0 for cat in DAILY_CATEGORIES}
    recipe_rows: list[dict[str, Any]] = []

    for rec in curated:
        cat = rec["meal_category"]
        pos = rec["position"]
        counts_by_cat[cat] += 1

        rec_data = dict(rec)
        rec_data["steps"] = [
            str(st.get("instruction") or "")
            for st in rec_data.get("structured_steps") or []
            if isinstance(st, dict) and st.get("instruction")
        ]
        rec_data["slug"] = _slugify(rec_data["title"], cat, pos)

        # Compute live pantry availability deterministically against demo inventory
        avail = compute_recipe_availability(rec_data, raw_inventory_payload)

        # Validate using the real application culinary quality validator
        q_res = validate_recipe_quality(rec_data, strict_ai_generated=True)
        if not q_res.is_valid:
            raise ValueError(
                f"Static demo recipe '{rec_data['title']}' failed quality validation: {q_res.issues}"
            )

        slot_id = uuid5(
            DEMO_STATIC_PLAN_NAMESPACE,
            f"daily_meal_plan_recipe:{plan_id}:{cat}:{pos}",
        )
        recipe_rows.append(
            {
                "id": slot_id,
                "daily_meal_plan_id": plan_id,
                "meal_category": cat,
                "position": pos,
                "recipe_data": rec_data,
                "availability_data": avail,
                "quality_status": "valid",
                "quality_warnings": [],
                "created_at": ref_now,
            }
        )

    if any(counts_by_cat[cat] != 2 for cat in DAILY_CATEGORIES) or len(recipe_rows) != 8:
        raise ValueError(f"Expected 2 recipes per category (8 total), got {counts_by_cat}")

    plan_row: dict[str, Any] = {
        "id": plan_id,
        "household_id": demo_household_id,
        "local_date": target_date,
        "timezone": "Asia/Kolkata",
        "status": "ready",
        "auto_attempted": True,
        "manual_regeneration_count": 0,
        "context_snapshot": {
            "context_version": "demo_static_v1",
            "local_date": target_date.isoformat(),
            "timezone": "Asia/Kolkata",
            "inventory_count": len(raw_inventory_payload),
        },
        "weather_snapshot": None,
        "provider_metadata": {
            "provider": "demo_static",
            "curated": True,
            "valid_recipe_count": 8,
        },
        "generation_started_at": ref_now,
        "generation_completed_at": ref_now,
        "error_code": None,
        "error_message": None,
        "created_at": ref_now,
        "updated_at": ref_now,
    }
    return plan_row, recipe_rows
