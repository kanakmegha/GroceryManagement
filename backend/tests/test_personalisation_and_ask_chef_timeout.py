from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.db import Base
from app.infra.llm.hf_provider import HuggingFaceRecipeProvider
from app.main import create_app
from app.modules.auth.models import Household, HouseholdMember, User
from app.modules.cooking.models import CookingPlan, MealHistory
from app.modules.inventory.models import CanonicalItem, InventoryLot
from app.modules.recipes.models import RecipeFeedback
from app.modules.recipes.preferences import compute_household_preference_profile
from app.modules.recipes.schemas import RecipeChatRequest
from app.modules.recipes.service import chat_recipes


def _valid_qwen_chat_json() -> str:
    return json.dumps(
        {
            "reply": "Here is a detailed Paneer Bhurji recipe using your pantry.",
            "recipes": [
                {
                    "title": "Quick Paneer Bhurji",
                    "description": "Spiced scrambled paneer cooked with onions and tomatoes.",
                    "meal_type": "dinner",
                    "servings": 2,
                    "prep_minutes": 10,
                    "cook_minutes": 12,
                    "difficulty": "easy",
                    "cuisine": "North Indian",
                    "equipment_required": ["wide skillet", "spatula"],
                    "dietary_tags": ["vegetarian"],
                    "available_ingredients": [
                        {
                            "name": "Paneer",
                            "quantity": "200 g",
                            "prep_state": "crumbled",
                            "used_in_steps": [2],
                            "status": "available",
                            "needs_purchase": False,
                        },
                        {
                            "name": "Onion",
                            "quantity": "100 g",
                            "prep_state": "finely chopped",
                            "used_in_steps": [1],
                            "status": "available",
                            "needs_purchase": False,
                        },
                        {
                            "name": "Oil",
                            "quantity": "10 ml",
                            "prep_state": None,
                            "used_in_steps": [1],
                            "status": "available",
                            "needs_purchase": False,
                        },
                        {
                            "name": "Salt",
                            "quantity": "3 g",
                            "prep_state": None,
                            "used_in_steps": [2],
                            "status": "available",
                            "needs_purchase": False,
                        },
                    ],
                    "partial_ingredients": [],
                    "missing_ingredients": [],
                    "optional_ingredients": [],
                    "preparation_steps": [
                        "Crumble 200 g paneer and finely chop 100 g onion."
                    ],
                    "structured_steps": [
                        {
                            "step_number": 1,
                            "title": "Saute Onions",
                            "instruction": (
                                "Heat 10 ml oil in a wide skillet on medium heat and saute "
                                "100 g chopped onion for 4 minutes."
                            ),
                            "ingredients_used": ["Oil", "Onion"],
                            "ingredient_quantities": {"Oil": "10 ml", "Onion": "100 g"},
                            "heat_level": "medium",
                            "duration_minutes": 4,
                            "equipment": ["wide skillet"],
                            "completion_cue": "Onions turn soft and translucent.",
                            "caution": "Do not burn the onions.",
                            "next_action": "Add crumbled paneer.",
                        },
                        {
                            "step_number": 2,
                            "title": "Cook Paneer",
                            "instruction": (
                                "Add 200 g crumbled paneer and 3 g salt, tossing gently on "
                                "medium-low heat for 4 minutes."
                            ),
                            "ingredients_used": ["Paneer", "Salt"],
                            "ingredient_quantities": {"Paneer": "200 g", "Salt": "3 g"},
                            "heat_level": "medium-low",
                            "duration_minutes": 4,
                            "equipment": ["wide skillet", "spatula"],
                            "completion_cue": "Paneer is heated through and moist.",
                            "caution": "Avoid overcooking so paneer stays soft.",
                            "next_action": "Serve warm.",
                        },
                        {
                            "step_number": 3,
                            "title": "Rest & Serve",
                            "instruction": (
                                "Turn off heat, let the Quick Paneer Bhurji rest 1 minute, "
                                "and transfer to warm plates."
                            ),
                            "ingredients_used": ["Paneer"],
                            "ingredient_quantities": {"Paneer": "200 g"},
                            "heat_level": "low",
                            "duration_minutes": 1,
                            "equipment": ["spatula"],
                            "completion_cue": "Warm aromatic bhurji ready to serve.",
                            "caution": None,
                            "next_action": None,
                        },
                    ],
                    "substitutions": ["Replace paneer with firm tofu."],
                    "cooking_tips": ["Serve with warm rotis."],
                    "troubleshooting_tips": ["If dry, sprinkle 1 tbsp warm water."],
                    "serving_result": "Soft, spiced crumbled paneer.",
                    "food_safety_notes": [],
                    "uses_expiring_items": [],
                }
            ],
            "follow_up_questions": [],
        }
    )


@pytest.mark.asyncio
async def test_ten_distinct_completed_recipes_trigger_and_persistence(tmp_path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'pers.db'}"
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with session_maker() as db:
        user = User(clerk_user_id="clk_pers", display_name="Chef")
        db.add(user)
        await db.flush()
        hh = Household(name="Pers Home", created_by=user.id)
        db.add(hh)
        await db.flush()
        db.add(HouseholdMember(household_id=hh.id, user_id=user.id, role="owner"))

        # Add 9 distinct completed recipes + 2 repeats of recipe 1 + RecipeFeedback views
        for idx in range(9):
            cp = CookingPlan(
                household_id=hh.id,
                created_by=user.id,
                recipe_id=f"rec-{idx}",
                recipe_title=f"Dish {idx}",
                status="completed",
                planned_servings=2,
                ingredients=[],
            )
            db.add(cp)
            await db.flush()
            db.add(
                MealHistory(
                    household_id=hh.id,
                    cooking_plan_id=cp.id,
                    cooked_by=user.id,
                    recipe_id=f"rec-{idx}",
                    recipe_title=f"Dish {idx}",
                    cooked_at=datetime.now(UTC),
                )
            )
        # Repeat Dish 0 twice
        for _ in range(2):
            cp_rep = CookingPlan(
                household_id=hh.id,
                created_by=user.id,
                recipe_id="rec-0",
                recipe_title="Dish 0",
                status="completed",
                planned_servings=2,
                ingredients=[],
            )
            db.add(cp_rep)
            await db.flush()
            db.add(
                MealHistory(
                    household_id=hh.id,
                    cooking_plan_id=cp_rep.id,
                    cooked_by=user.id,
                    recipe_id="rec-0",
                    recipe_title="Dish 0",
                    cooked_at=datetime.now(UTC),
                )
            )
        # Generated/viewed/feedback-only recipes must NOT count
        db.add(
            RecipeFeedback(
                household_id=hh.id,
                recipe_id="rec-viewed-only",
                recipe_title="Viewed Only Dish",
                action="cooked",
            )
        )
        await db.commit()
        hid, uid, hh_obj, user_obj = hh.id, user.id, hh, user

    from app.core.tenancy import HouseholdContext, get_household_context

    app = create_app(Settings(DATABASE_URL=db_url, DAILY_MEAL_SCHEDULER_ENABLED=False))
    app.dependency_overrides[get_household_context] = lambda: HouseholdContext(
        user=user_obj,
        household=hh_obj,
        role="owner",
    )

    from app.core.db import get_db

    async def _override_db():
        async with session_maker() as s:
            yield s

    app.dependency_overrides[get_db] = _override_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        headers = {"X-Household-Id": str(hid)}
        r9 = (await client.get("/api/v1/recipes/preferences", headers=headers)).json()
        assert r9["distinct_completed_recipes"] == 9
        assert r9["should_show_personalisation_prompt"] is False

        # Complete 10th distinct recipe
        async with session_maker() as db:
            cp10 = CookingPlan(
                household_id=hid,
                created_by=uid,
                recipe_id="rec-9",
                recipe_title="Dish 9",
                status="completed",
                planned_servings=2,
                ingredients=[],
            )
            db.add(cp10)
            await db.flush()
            db.add(
                MealHistory(
                    household_id=hid,
                    cooking_plan_id=cp10.id,
                    cooked_by=uid,
                    recipe_id="rec-9",
                    recipe_title="Dish 9",
                    cooked_at=datetime.now(UTC),
                )
            )
            await db.commit()

        r10 = (await client.get("/api/v1/recipes/preferences", headers=headers)).json()
        assert r10["distinct_completed_recipes"] == 10
        assert r10["should_show_personalisation_prompt"] is True

        # Persist user selection ("new") -> prompt is dismissed and choice persists
        saved = (
            await client.patch(
                "/api/v1/recipes/preferences/recommendation-mode",
                headers=headers,
                json={"recommendation_mode": "new", "dismiss_personalisation_prompt": True},
            )
        ).json()
        assert saved["recommendation_mode"] == "new"
        assert saved["should_show_personalisation_prompt"] is False
        assert saved["personalisation_prompt_dismissed"] is True

        reloaded = (await client.get("/api/v1/recipes/preferences", headers=headers)).json()
        assert reloaded["recommendation_mode"] == "new"
        assert reloaded["should_show_personalisation_prompt"] is False

    await engine.dispose()


@pytest.mark.asyncio
async def test_recommendation_modes_respect_positive_ratings_and_unexplored_dishes(
    tmp_path,
) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'modes.db'}"
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with session_maker() as db:
        user = User(clerk_user_id="clk_modes", display_name="Chef")
        db.add(user)
        await db.flush()
        hh = Household(name="Modes Home", created_by=user.id)
        db.add(hh)
        await db.flush()
        db.add(HouseholdMember(household_id=hh.id, user_id=user.id, role="owner"))

        # Positively rated South Indian dish (rating=5) + unrated North Indian dish
        for idx, (title, cuisine, rating) in enumerate(
            [
                ("South Indian Dal Tadka & Rice Bowl", "South Indian", 5),
                ("Unrated Paneer Dish", "North Indian", None),
            ]
        ):
            cp = CookingPlan(
                household_id=hh.id,
                created_by=user.id,
                recipe_id=f"m-{idx}",
                recipe_title=title,
                status="completed",
                planned_servings=2,
                ingredients=[],
            )
            db.add(cp)
            await db.flush()
            db.add(
                MealHistory(
                    household_id=hh.id,
                    cooking_plan_id=cp.id,
                    cooked_by=user.id,
                    recipe_id=f"m-{idx}",
                    recipe_title=title,
                    cuisine=cuisine,
                    rating=rating,
                    cooked_at=datetime.now(UTC),
                )
            )
        await db.commit()

        profile = await compute_household_preference_profile(db, hh.id)
        fav_titles = {f["title"] for f in profile["favourite_dishes"]}
        assert "South Indian Dal Tadka & Rice Bowl" in fav_titles
        assert "Unrated Paneer Dish" not in fav_titles
        assert "South Indian" in profile["explored_cuisines"]

    # Verify deterministic fallback scoring honors "enjoyed" vs "new" without violating diet
    provider = HuggingFaceRecipeProvider(hf_token="", model_id="Qwen/Qwen2.5-72B-Instruct")
    inv = [
        {"name": "toor dal", "quantity": 200, "base_unit": "g"},
        {"name": "paneer", "quantity": 200, "base_unit": "g"},
        {"name": "poha", "quantity": 200, "base_unit": "g"},
    ]
    enjoyed_res = await provider.generate_recipes(
        message="Suggest dinner",
        inventory_items=inv,
        dietary="vegetarian",
        preference_profile={**profile, "recommendation_mode": "enjoyed"},
    )
    new_res = await provider.generate_recipes(
        message="Suggest dinner",
        inventory_items=inv,
        dietary="vegetarian",
        preference_profile={**profile, "recommendation_mode": "new"},
    )
    assert enjoyed_res.recipes[0].title == "South Indian Dal Tadka & Rice Bowl"
    assert new_res.recipes[0].title != "South Indian Dal Tadka & Rice Bowl"

    await engine.dispose()


@pytest.mark.asyncio
async def test_ask_chef_single_shot_no_unnecessary_repair_and_timeout_fallback(
    tmp_path,
) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'chef.db'}"
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with session_maker() as db:
        user = User(clerk_user_id="clk_chef", display_name="Chef")
        db.add(user)
        await db.flush()
        hh = Household(name="Chef Home", created_by=user.id)
        db.add(hh)
        await db.flush()
        db.add(HouseholdMember(household_id=hh.id, user_id=user.id, role="owner"))

        for name, unit, qty in [
            ("Paneer", "g", "200.000"),
            ("Onion", "g", "200.000"),
            ("Oil", "ml", "100.000"),
            ("Salt", "g", "100.000"),
        ]:
            ci = CanonicalItem(
                slug=name.lower(),
                name=name,
                category="pantry",
                default_base_unit=unit,
            )
            db.add(ci)
            await db.flush()
            db.add(
                InventoryLot(
                    household_id=hh.id,
                    canonical_item_id=ci.id,
                    remaining_qty=Decimal(qty),
                    initial_qty=Decimal(qty),
                    base_unit=unit,
                    lot_status="active",
                )
            )
        await db.commit()
        hid = hh.id

    call_count = 0

    def _single_shot_handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        body = json.loads(req.content)
        # Single-shot Ask Chef should not send tool definitions
        assert "tools" not in body
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": _valid_qwen_chat_json()}}
                ],
                "model": "Qwen/Qwen2.5-72B-Instruct",
            },
        )

    from app.modules.recipes import service as recipes_service

    orig_build = recipes_service._build_provider
    recipes_service._build_provider = lambda settings: HuggingFaceRecipeProvider(
        hf_token="test-token",
        model_id="Qwen/Qwen2.5-72B-Instruct",
        transport=httpx.MockTransport(_single_shot_handler),
    )
    try:
        async with session_maker() as db:
            res = await chat_recipes(
                db,
                household_id=hid,
                settings=Settings(HF_TOKEN="test-token"),
                payload=RecipeChatRequest(message="Quick paneer dinner", servings=2),
            )
        # No unnecessary repair call -> exactly 1 LLM HTTP call
        assert call_count == 1
        assert res.provider_status == "live_hf"
        assert len(res.suggestions) == 1
        sug = res.suggestions[0]
        assert sug.title == "Quick Paneer Bhurji"
        assert len(sug.structured_steps) == 3
        assert len(sug.instructions) == 3
        assert sug.equipment_required == ["wide skillet", "spatula"]
        assert sug.troubleshooting_tips == ["If dry, sprinkle 1 tbsp warm water."]
        assert sug.serving_result == "Soft, spiced crumbled paneer."

        # Timeout handling falls back cleanly
        def _timeout_handler(req: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=req)

        recipes_service._build_provider = lambda settings: HuggingFaceRecipeProvider(
            hf_token="test-token",
            model_id="Qwen/Qwen2.5-72B-Instruct",
            transport=httpx.MockTransport(_timeout_handler),
        )
        async with session_maker() as db:
            timeout_res = await chat_recipes(
                db,
                household_id=hid,
                settings=Settings(HF_TOKEN="test-token"),
                payload=RecipeChatRequest(message="Quick paneer dinner", servings=2),
            )
        assert timeout_res.provider_status == "fallback_error"
        assert len(timeout_res.suggestions) >= 1
    finally:
        recipes_service._build_provider = orig_build

    await engine.dispose()
