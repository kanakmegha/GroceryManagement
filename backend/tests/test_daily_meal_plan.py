"""Tests for Daily AI Meal Planner (scheduler, one-call generator, validation, and API)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.db import Base
from app.modules.auth.models import Household, HouseholdMember, User
from app.modules.discovery.daily_generator import DailyMealPlanGenerator
from app.modules.discovery.daily_models import DailyMealPlan
from app.modules.discovery.daily_scheduler import (
    claim_daily_plan_slot,
    is_household_due_for_daily_plan,
    run_daily_plan_for_household,
    run_scheduler_tick,
)
from app.modules.discovery.daily_service import get_daily_plan_for_household
from app.modules.inventory.models import CanonicalItem, InventoryLot
from app.modules.recipes.models import HouseholdRecipePreference


def _make_valid_recipe(title: str, meal_category: str) -> dict:
    return {
        "title": title,
        "description": f"Beginner-friendly {title} prepared with pantry staples.",
        "meal_category": meal_category,
        "servings": 2,
        "prep_minutes": 10,
        "cook_minutes": 12,
        "difficulty": "easy",
        "cuisine": "Indian",
        "equipment_required": ["skillet", "spatula"],
        "available_ingredients": [
            {"name": "Oil", "quantity": "10 ml", "status": "available"},
            {"name": "Salt", "quantity": "3 g", "status": "available"},
            {"name": "Cumin seeds", "quantity": "2 g", "status": "available"},
            {"name": "Onion", "quantity": "80 g", "status": "available"},
        ],
        "preparation_steps": ["Finely chop 80 g onion and measure 2 g cumin seeds and 3 g salt."],
        "structured_steps": [
            {
                "step_number": 1,
                "title": "Warm Oil & Crackle Cumin",
                "instruction": (
                    "Heat 10 ml oil in a skillet over medium heat for 30 seconds and "
                    "crackle 2 g cumin seeds."
                ),
                "ingredients_used": ["Oil", "Cumin seeds"],
                "ingredient_quantities": {"Oil": "10 ml", "Cumin seeds": "2 g"},
                "heat_level": "medium",
                "duration_minutes": 1,
                "equipment": ["skillet"],
                "completion_cue": "Cumin seeds sizzle and turn aromatic.",
            },
            {
                "step_number": 2,
                "title": "Sauté Onion with Salt",
                "instruction": (
                    "Add 80 g chopped onion and 3 g salt, stirring occasionally and cooking "
                    "over medium heat for 5 minutes until tender and golden."
                ),
                "ingredients_used": ["Onion", "Salt"],
                "ingredient_quantities": {"Onion": "80 g", "Salt": "3 g"},
                "heat_level": "medium",
                "duration_minutes": 5,
                "equipment": ["skillet", "spatula"],
                "completion_cue": "Onions are soft and light golden.",
            },
            {
                "step_number": 3,
                "title": "Rest & Serve Warm",
                "instruction": (
                    "Transfer to warm serving bowls and serve immediately with accompaniments."
                ),
                "ingredients_used": [],
                "ingredient_quantities": {},
                "heat_level": None,
                "duration_minutes": 1,
                "equipment": ["serving bowl"],
                "completion_cue": "Dish is hot and aromatic.",
            },
        ],
        "steps": ["Warm oil and crackle cumin.", "Sauté onion with salt.", "Serve warm."],
    }


def _eight_valid_recipes_json() -> str:
    recipes = [
        _make_valid_recipe("Savory Onion Skillet Breakfast", "breakfast"),
        _make_valid_recipe("Cumin Spiced Morning Scramble", "breakfast"),
        _make_valid_recipe("Golden Onion Lunch Bowl", "lunch"),
        _make_valid_recipe("Quick Tempered Skillet Lunch", "lunch"),
        _make_valid_recipe("Crispy Evening Skillet Bites", "snacks"),
        _make_valid_recipe("Warm Cumin Tea-Time Snack", "snacks"),
        _make_valid_recipe("Homestyle Onion Dinner Bowl", "dinner"),
        _make_valid_recipe("Light Spiced Evening Dinner", "dinner"),
    ]
    return json.dumps({"recipes": recipes})


def test_timezone_and_dst_due_checks() -> None:
    # 02:59 IST = 21:29 UTC previous day -> NOT due
    assert not is_household_due_for_daily_plan(
        "Asia/Kolkata", datetime(2026, 11, 22, 21, 29, tzinfo=UTC)
    )
    # 03:00 IST = 21:30 UTC previous day -> DUE
    assert is_household_due_for_daily_plan(
        "Asia/Kolkata", datetime(2026, 11, 22, 21, 30, tzinfo=UTC)
    )
    # America/New_York EDT (summer UTC-4): 03:00 EDT = 07:00 UTC
    assert not is_household_due_for_daily_plan(
        "America/New_York", datetime(2026, 7, 15, 6, 59, tzinfo=UTC)
    )
    assert is_household_due_for_daily_plan(
        "America/New_York", datetime(2026, 7, 15, 7, 0, tzinfo=UTC)
    )
    # America/New_York EST (winter UTC-5): 03:00 EST = 08:00 UTC
    assert not is_household_due_for_daily_plan(
        "America/New_York", datetime(2026, 1, 15, 7, 59, tzinfo=UTC)
    )
    assert is_household_due_for_daily_plan(
        "America/New_York", datetime(2026, 1, 15, 8, 0, tzinfo=UTC)
    )


@pytest.mark.asyncio
async def test_scheduler_idempotency_concurrency_and_no_auto_retry_on_failure(tmp_path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'sched.db'}"
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with session_maker() as db:
        u = User(clerk_user_id="clk_sched", display_name="Chef")
        db.add(u)
        await db.flush()
        h = Household(name="IST Home", created_by=u.id)
        db.add(h)
        await db.flush()
        db.add(HouseholdMember(household_id=h.id, user_id=u.id, role="owner"))
        db.add(HouseholdRecipePreference(household_id=h.id, timezone="Asia/Kolkata"))
        await db.commit()
        hid = h.id

    call_count = 0

    def _ok_handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": _eight_valid_recipes_json()}}
                ],
                "usage": {"prompt_tokens": 200, "completion_tokens": 1200, "total_tokens": 1400},
            },
        )

    gen = DailyMealPlanGenerator(
        settings=Settings(HF_TOKEN="test-token"),
        transport=httpx.MockTransport(_ok_handler),
    )
    due_utc = datetime(2026, 11, 22, 22, 0, tzinfo=UTC)  # 03:30 IST on 2026-11-23

    # Tick 1 generates exactly once
    assert await run_scheduler_tick(session_maker, utc_now=due_utc, generator=gen) == 1
    assert call_count == 1

    # Tick 2 (duplicate execution / restart simulation) does NOT call LLM again
    assert await run_scheduler_tick(session_maker, utc_now=due_utc, generator=gen) == 0
    assert call_count == 1

    # Concurrent worker claim on same (household_id, local_date) returns None
    async with session_maker() as db:
        second_claim = await claim_daily_plan_slot(
            db,
            household_id=hid,
            local_date=date(2026, 11, 23),
            timezone_str="Asia/Kolkata",
        )
        assert second_claim is None

    # Failed job on next day is NOT auto-retried repeatedly
    fail_calls = 0

    def _fail_handler(req: httpx.Request) -> httpx.Response:
        nonlocal fail_calls
        fail_calls += 1
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "length", "message": {"content": "{bad"}}]},
        )

    fail_gen = DailyMealPlanGenerator(
        settings=Settings(HF_TOKEN="test-token"),
        transport=httpx.MockTransport(_fail_handler),
    )
    next_day_utc = datetime(2026, 11, 23, 22, 0, tzinfo=UTC)  # 03:30 IST on 2026-11-24
    assert await run_scheduler_tick(session_maker, utc_now=next_day_utc, generator=fail_gen) == 1
    assert fail_calls == 1
    assert await run_scheduler_tick(session_maker, utc_now=next_day_utc, generator=fail_gen) == 0
    assert fail_calls == 1

    async with session_maker() as db:
        failed_plan = (
            await db.execute(
                select(DailyMealPlan).where(
                    DailyMealPlan.household_id == hid,
                    DailyMealPlan.local_date == date(2026, 11, 24),
                )
            )
        ).scalar_one()
        assert failed_plan.status == "failed"
        assert failed_plan.error_code == "TRUNCATED_OUTPUT"

    await engine.dispose()


@pytest.mark.asyncio
async def test_generator_rejects_missing_category_duplicates_and_malformed_json() -> None:
    # Missing category (7 recipes)
    seven_recipes = json.loads(_eight_valid_recipes_json())["recipes"][:7]
    gen_7 = DailyMealPlanGenerator(
        settings=Settings(HF_TOKEN="test-token"),
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": json.dumps({"recipes": seven_recipes})},
                        }
                    ]
                },
            )
        ),
    )
    out_7 = await gen_7.generate({"local_date": "2026-11-23"})
    assert not out_7.success
    assert out_7.error_code == "CATEGORY_OR_DUPLICATE_ERROR"

    # Malformed JSON
    gen_bad = DailyMealPlanGenerator(
        settings=Settings(HF_TOKEN="test-token"),
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json={"choices": [{"finish_reason": "stop", "message": {"content": "{not json"}}]},
            )
        ),
    )
    out_bad = await gen_bad.generate({"local_date": "2026-11-23"})
    assert not out_bad.success
    assert out_bad.error_code == "MALFORMED_JSON"


@pytest.mark.asyncio
async def test_daily_plan_api_endpoints_and_pantry_integrity(client: AsyncClient) -> None:
    # Unauthenticated request rejected
    unauth = await client.get("/api/v1/discover/daily-plan")
    assert unauth.status_code == 401

    app = client._transport.app  # type: ignore[attr-defined]
    async with app.state.session_maker() as db:
        u = User(clerk_user_id="clk_api_daily", display_name="API User")
        db.add(u)
        await db.flush()
        h = Household(name="API Home", created_by=u.id)
        db.add(h)
        await db.flush()
        db.add(HouseholdMember(household_id=h.id, user_id=u.id, role="owner"))
        onion = CanonicalItem(
            household_id=h.id,
            slug="onion",
            name="Onion",
            category="produce",
            default_base_unit="g",
        )
        db.add(onion)
        await db.flush()
        db.add(
            InventoryLot(
                household_id=h.id,
                canonical_item_id=onion.id,
                initial_qty=Decimal("500.000"),
                remaining_qty=Decimal("500.000"),
                base_unit="g",
            )
        )
        await db.commit()

        gen = DailyMealPlanGenerator(
            settings=Settings(HF_TOKEN="test-token"),
            transport=httpx.MockTransport(
                lambda req: httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {"content": _eight_valid_recipes_json()},
                            }
                        ]
                    },
                )
            ),
        )
        plan = await run_daily_plan_for_household(
            db,
            h.id,
            utc_now=datetime(2026, 11, 23, 12, 0, tzinfo=UTC),
            generator=gen,
            is_manual_regeneration=True,
        )
        assert plan is not None
        assert plan.status == "ready", f"{plan.error_code}: {plan.error_message}"

        res = await get_daily_plan_for_household(
            db,
            h.id,
            utc_now=datetime(2026, 11, 23, 12, 0, tzinfo=UTC),
        )
        assert res.status == "ready"
        assert len(res.recipes) == 8
        assert len(res.categories["breakfast"]) == 2
        assert len(res.categories["lunch"]) == 2
        assert len(res.categories["snacks"]) == 2
        assert len(res.categories["dinner"]) == 2
        # Verify InventoryLot was never mutated
        lot_after = (
            await db.execute(select(InventoryLot).where(InventoryLot.household_id == h.id))
        ).scalar_one()
        assert lot_after.remaining_qty == Decimal("500.000")


@pytest.mark.asyncio
async def test_generator_normalizes_missing_ingredient_status_and_repairs_malformed_json() -> None:
    # 1. Valid 8 recipes where ingredient items omit `status` (matching prompt buckets)
    payload = json.loads(_eight_valid_recipes_json())
    for rec in payload["recipes"]:
        for ing in rec["available_ingredients"]:
            ing.pop("status", None)

    gen_norm = DailyMealPlanGenerator(
        settings=Settings(HF_TOKEN="test-token"),
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": json.dumps(payload)}}
                    ]
                },
            )
        ),
    )
    out_norm = await gen_norm.generate({"local_date": "2026-11-23"})
    assert out_norm.success, f"{out_norm.error_code}: {out_norm.error_message}"
    assert len(out_norm.slots) == 8
    assert out_norm.provider_metadata["repair_attempted"] is False

    # 2. Malformed JSON repaired on single targeted repair attempt
    calls = 0

    def _repair_handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": '{"recipes": [broken}'}}
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": _eight_valid_recipes_json()},
                    }
                ]
            },
        )

    gen_repair = DailyMealPlanGenerator(
        settings=Settings(HF_TOKEN="test-token"),
        transport=httpx.MockTransport(_repair_handler),
    )
    out_repair = await gen_repair.generate({"local_date": "2026-11-23"})
    assert calls == 2
    assert out_repair.success
    assert out_repair.provider_metadata["repair_attempted"] is True

    # 3. Missing required field (e.g. title) reports ValidationError detail
    bad_field_payload = json.loads(_eight_valid_recipes_json())
    del bad_field_payload["recipes"][0]["title"]
    gen_missing_field = DailyMealPlanGenerator(
        settings=Settings(HF_TOKEN="test-token"),
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": json.dumps(bad_field_payload)},
                        }
                    ]
                },
            )
        ),
    )
    out_missing_field = await gen_missing_field.generate({"local_date": "2026-11-23"})
    assert not out_missing_field.success
    assert out_missing_field.error_code == "MALFORMED_JSON"
    assert "validation_error_detail" in out_missing_field.provider_metadata

    # 4. Quality validation failure (missing quantities / vague steps) is NOT silently accepted
    bad_quality_payload = json.loads(_eight_valid_recipes_json())
    bad_quality_payload["recipes"][0]["available_ingredients"] = [
        {"name": "Salt", "quantity": "", "status": "available"}
    ]
    bad_quality_payload["recipes"][0]["structured_steps"] = []
    gen_bad_quality = DailyMealPlanGenerator(
        settings=Settings(HF_TOKEN="test-token"),
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": json.dumps(bad_quality_payload)},
                        }
                    ]
                },
            )
        ),
    )
    out_bad_quality = await gen_bad_quality.generate({"local_date": "2026-11-23"})
    assert not out_bad_quality.success
    assert out_bad_quality.error_code == "VALIDATION_FAILED"

    # 5. Incorrect category counts (3 breakfast, 1 lunch) rejected
    bad_cat_payload = json.loads(_eight_valid_recipes_json())
    bad_cat_payload["recipes"][2]["meal_category"] = "breakfast"
    gen_bad_cat = DailyMealPlanGenerator(
        settings=Settings(HF_TOKEN="test-token"),
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": json.dumps(bad_cat_payload)},
                        }
                    ]
                },
            )
        ),
    )
    out_bad_cat = await gen_bad_cat.generate({"local_date": "2026-11-23"})
    assert not out_bad_cat.success
    assert out_bad_cat.error_code == "CATEGORY_OR_DUPLICATE_ERROR"


@pytest.mark.asyncio
async def test_generator_incomplete_one_recipe_duplicate_titles_and_finish_reasons() -> None:
    # 1. One recipe only with finish_reason=stop must fail, never silently accept partial plan
    one_recipe = json.loads(_eight_valid_recipes_json())["recipes"][:1]
    gen_one = DailyMealPlanGenerator(
        settings=Settings(HF_TOKEN="test-token"),
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": json.dumps({"recipes": one_recipe})},
                        }
                    ]
                },
            )
        ),
    )
    out_one = await gen_one.generate({"local_date": "2026-11-23"})
    assert not out_one.success
    assert out_one.error_code == "CATEGORY_OR_DUPLICATE_ERROR"
    assert not out_one.slots

    # 2. Duplicate recipe titles rejected
    dup_payload = json.loads(_eight_valid_recipes_json())
    dup_payload["recipes"][1]["title"] = dup_payload["recipes"][0]["title"]
    gen_dup = DailyMealPlanGenerator(
        settings=Settings(HF_TOKEN="test-token"),
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": json.dumps(dup_payload)},
                        }
                    ]
                },
            )
        ),
    )
    out_dup = await gen_dup.generate({"local_date": "2026-11-23"})
    assert not out_dup.success
    assert out_dup.error_code == "CATEGORY_OR_DUPLICATE_ERROR"

    # 3. Truncated provider response with finish_reason=length -> TRUNCATED_OUTPUT
    gen_len = DailyMealPlanGenerator(
        settings=Settings(HF_TOKEN="test-token"),
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {"content": '{"recipes": [{"title": "Cut'},
                        }
                    ]
                },
            )
        ),
    )
    out_len = await gen_len.generate({"local_date": "2026-11-23"})
    assert not out_len.success
    assert out_len.error_code == "TRUNCATED_OUTPUT"

    # 4. finish_reason=stop with incomplete/truncated JSON -> MALFORMED_JSON
    gen_stop_incomplete = DailyMealPlanGenerator(
        settings=Settings(HF_TOKEN="test-token"),
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": '{"recipes": [{"title": "Cut'},
                        }
                    ]
                },
            )
        ),
    )
    out_stop_incomplete = await gen_stop_incomplete.generate({"local_date": "2026-11-23"})
    assert not out_stop_incomplete.success
    assert out_stop_incomplete.error_code == "MALFORMED_JSON"

    # 5. String preparation_steps coerced to list[str] for valid 8-recipe response
    str_prep_payload = json.loads(_eight_valid_recipes_json())
    str_prep_payload["recipes"][0]["preparation_steps"] = (
        "Finely chop 80 g onion and measure 2 g cumin seeds and 3 g salt."
    )
    gen_str_prep = DailyMealPlanGenerator(
        settings=Settings(HF_TOKEN="test-token"),
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": json.dumps(str_prep_payload)},
                        }
                    ]
                },
            )
        ),
    )
    out_str_prep = await gen_str_prep.generate({"local_date": "2026-11-23"})
    assert out_str_prep.success, f"{out_str_prep.error_code}: {out_str_prep.error_message}"
    assert len(out_str_prep.slots) == 8


def test_dish_classification_and_strict_quality_rules() -> None:
    from app.modules.recipes.quality_validator import validate_recipe_quality

    # 1. Masala Moong Dal Chilla must NOT inherit dal-soup tempering/pressure-cook requirements
    chilla = {
        "title": "Masala Moong Dal Chilla",
        "servings": 2,
        "prep_minutes": 15,
        "cook_minutes": 12,
        "difficulty": "easy",
        "ingredients": [
            {"name": "Moong dal", "quantity": "150 g"},
            {"name": "Water", "quantity": "120 ml"},
            {"name": "Salt", "quantity": "3 g"},
            {"name": "Oil", "quantity": "10 ml"},
            {"name": "Onion", "quantity": "40 g"},
        ],
        "preparation_steps": ["Soak 150 g moong dal and chop 40 g onion finely."],
        "structured_steps": [
            {
                "step_number": 1,
                "title": "Grind Smooth Batter",
                "instruction": (
                    "Blend soaked 150 g moong dal with 120 ml water, 3 g salt, and "
                    "40 g onion into a smooth pourable batter."
                ),
                "ingredients_used": ["Moong dal", "Water", "Salt", "Onion"],
                "ingredient_quantities": {
                    "Moong dal": "150 g",
                    "Water": "120 ml",
                    "Salt": "3 g",
                    "Onion": "40 g",
                },
                "heat_level": None,
                "duration_minutes": 3,
                "completion_cue": "Batter coats the back of a spoon smoothly.",
            },
            {
                "step_number": 2,
                "title": "Spread Batter on Tawa",
                "instruction": (
                    "Heat a non-stick tawa on medium heat, brush 5 ml oil, pour a ladle "
                    "of batter and spread into a thin 18 cm circle."
                ),
                "ingredients_used": ["Oil"],
                "ingredient_quantities": {"Oil": "5 ml"},
                "heat_level": "medium",
                "duration_minutes": 2,
                "completion_cue": "Edges turn crisp and lift easily from the tawa.",
            },
            {
                "step_number": 3,
                "title": "Flip & Crisp Chilla",
                "instruction": (
                    "Drizzle remaining 5 ml oil, flip carefully, and cook on medium heat "
                    "for 2 minutes until golden brown on both sides."
                ),
                "ingredients_used": ["Oil"],
                "ingredient_quantities": {"Oil": "5 ml"},
                "heat_level": "medium",
                "duration_minutes": 2,
                "completion_cue": "Both sides have golden crisp spots.",
            },
        ],
    }
    res_chilla = validate_recipe_quality(chilla, strict_ai_generated=True)
    assert res_chilla.is_valid, res_chilla.issues

    # 2. Dal soup (Moong Dal Tadka) STILL requires wash/soak, cook lentils, tempering
    bad_dal = {
        **chilla,
        "title": "Moong Dal Tadka",
        "structured_steps": [
            {
                "step_number": 1,
                "title": "Mix Ingredients",
                "instruction": "Combine 150 g moong dal with 120 ml water and 3 g salt in a bowl.",
                "ingredients_used": ["Moong dal", "Water", "Salt"],
                "heat_level": None,
                "duration_minutes": 2,
                "completion_cue": "Ingredients are combined in the bowl.",
            },
            {
                "step_number": 2,
                "title": "Warm Gently",
                "instruction": (
                    "Warm the mixture in a pan with 10 ml oil for 3 minutes on low heat."
                ),
                "ingredients_used": ["Oil"],
                "heat_level": "low",
                "duration_minutes": 3,
                "completion_cue": "Mixture is warm throughout.",
            },
            {
                "step_number": 3,
                "title": "Garnish with Onion",
                "instruction": "Top with 40 g chopped onion and serve immediately in bowls.",
                "ingredients_used": ["Onion"],
                "heat_level": None,
                "duration_minutes": 1,
                "completion_cue": "Onion garnish sits evenly on top.",
            },
        ],
    }
    res_dal = validate_recipe_quality(bad_dal, strict_ai_generated=True)
    assert not res_dal.is_valid
    assert any("dal recipe is missing" in i for i in res_dal.issues)

    # 3. Smoothie (2 steps, no stovetop heat) passes without artificial heat steps
    smoothie = {
        "title": "Banana Almond Smoothie",
        "servings": 2,
        "prep_minutes": 5,
        "cook_minutes": 0,
        "difficulty": "easy",
        "ingredients": [
            {"name": "Banana", "quantity": "200 g"},
            {"name": "Milk", "quantity": "300 ml"},
            {"name": "Almonds", "quantity": "20 g"},
        ],
        "preparation_steps": ["Peel and slice 200 g banana; roughly chop 20 g almonds."],
        "structured_steps": [
            {
                "step_number": 1,
                "title": "Blend Banana, Milk & Almonds",
                "instruction": (
                    "Combine 200 g sliced banana, 300 ml chilled milk, and 20 g almonds "
                    "in a blender and blend until velvety smooth."
                ),
                "ingredients_used": ["Banana", "Milk", "Almonds"],
                "ingredient_quantities": {"Banana": "200 g", "Milk": "300 ml", "Almonds": "20 g"},
                "heat_level": None,
                "duration_minutes": 2,
                "completion_cue": "Smoothie is frothy with no almond chunks.",
            },
            {
                "step_number": 2,
                "title": "Pour & Serve Chilled",
                "instruction": (
                    "Pour the blended smoothie into two chilled glasses and serve "
                    "immediately while cold and frothy."
                ),
                "ingredients_used": [],
                "heat_level": None,
                "duration_minutes": 1,
                "completion_cue": "Glasses are filled evenly and frothy.",
            },
        ],
    }
    res_smoothie = validate_recipe_quality(smoothie, strict_ai_generated=True)
    assert res_smoothie.is_valid, res_smoothie.issues

    # 4. Missing salt in savoury dish, unlisted ingredient, missing heat_level, missing essential
    no_salt = {
        **chilla,
        "title": "Masala Oats Porridge",
        "ingredients": [i for i in chilla["ingredients"] if i["name"] != "Salt"],
    }
    res_no_salt = validate_recipe_quality(no_salt, strict_ai_generated=True)
    assert not res_no_salt.is_valid
    assert any("missing salt" in i.lower() for i in res_no_salt.issues)

    unlisted_sugar = {
        **smoothie,
        "structured_steps": [
            {
                **smoothie["structured_steps"][0],
                "instruction": (
                    "Blend 200 g banana, 300 ml milk, 20 g almonds, and 1 tsp sugar until smooth."
                ),
            },
            smoothie["structured_steps"][1],
        ],
    }
    res_unlisted = validate_recipe_quality(unlisted_sugar, strict_ai_generated=True)
    assert not res_unlisted.is_valid
    assert any("sugar" in i.lower() for i in res_unlisted.issues)

    no_heat = {
        **chilla,
        "structured_steps": [
            chilla["structured_steps"][0],
            {**chilla["structured_steps"][1], "heat_level": None},
            chilla["structured_steps"][2],
        ],
    }
    res_no_heat = validate_recipe_quality(no_heat, strict_ai_generated=True)
    assert not res_no_heat.is_valid
    assert any("heat_level" in i for i in res_no_heat.issues)

    missing_essential = {
        **chilla,
        "title": "Aloo Matar Sabzi",
        "ingredients": [{"name": "Potato", "quantity": "200 g"}],
    }
    res_missing_ess = validate_recipe_quality(missing_essential, strict_ai_generated=True)
    assert not res_missing_ess.is_valid
    assert any(
        "Missing essential ingredient for Aloo Matar Sabzi" in i for i in res_missing_ess.issues
    )


@pytest.mark.asyncio
async def test_targeted_quality_repair_preserves_valid_and_rejects_on_failure() -> None:
    payload = json.loads(_eight_valid_recipes_json())
    # Break recipe 0 (remove heat_level on step 1) while leaving recipes 1..7 valid
    broken_title = payload["recipes"][0]["title"]
    valid_title_1 = payload["recipes"][1]["title"]
    payload["recipes"][0]["structured_steps"][0]["heat_level"] = None

    repair_calls: list[dict] = []

    def _success_repair_handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        if len(repair_calls) == 0:
            repair_calls.append(body)
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": json.dumps(payload)}}
                    ]
                },
            )
        repair_calls.append(body)
        # Verify ONLY the 1 failing recipe was sent to repair
        failing = body["messages"][1]["content"]
        parsed_user = json.loads(failing)
        assert len(parsed_user["failing_recipes"]) == 1
        assert parsed_user["failing_recipes"][0]["recipe"]["title"] == broken_title
        repaired_rec = _make_valid_recipe(broken_title, "breakfast")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps({"recipes": [repaired_rec]})},
                    }
                ]
            },
        )

    gen = DailyMealPlanGenerator(
        settings=Settings(HF_TOKEN="test-token"),
        transport=httpx.MockTransport(_success_repair_handler),
    )
    out = await gen.generate({"local_date": "2026-11-23"})
    assert out.success, f"{out.error_code}: {out.error_message}"
    assert out.provider_metadata["quality_repair_attempted"] is True
    assert out.provider_metadata["quality_repair_succeeded"] is True
    assert len(out.slots) == 8
    assert out.slots[1].recipe_data["title"] == valid_title_1

    # Failed repair (e.g. swaps title or still invalid) must reject the entire plan
    def _bad_repair_handler(req: httpx.Request) -> httpx.Response:
        if "failing_recipes" in req.content.decode("utf-8"):
            swapped = _make_valid_recipe("Unrelated Pizza", "breakfast")
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": json.dumps({"recipes": [swapped]})},
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload)}}]
            },
        )

    gen_fail = DailyMealPlanGenerator(
        settings=Settings(HF_TOKEN="test-token"),
        transport=httpx.MockTransport(_bad_repair_handler),
    )
    out_fail = await gen_fail.generate({"local_date": "2026-11-23"})
    assert not out_fail.success
    assert out_fail.error_code == "VALIDATION_FAILED"
    assert out_fail.provider_metadata["quality_repair_succeeded"] is False
    assert out_fail.provider_metadata["quality_repair_identity_ok"] is False
    assert (
        out_fail.provider_metadata["quality_repair_rejection_reason"] == "repair_identity_mismatch"
    )
    assert out_fail.provider_metadata["initial_valid_recipe_count"] == 7


def test_plural_chilla_canonical_aliases_and_concise_instructions() -> None:
    from app.modules.recipes.flatbread_and_workflows import detect_dish_workflow
    from app.modules.recipes.quality_validator import validate_recipe_quality

    # 1. Plural pancake/chilla precedence vs dal
    wf_pancakes = detect_dish_workflow({"title": "Masala Chana Dal Pancakes"})
    wf_chillas = detect_dish_workflow({"title": "Moong Dal Chillas"})
    wf_dal = detect_dish_workflow({"title": "Moong Dal Tadka"})
    assert wf_pancakes is not None and wf_pancakes.category == "chilla_pancake"
    assert wf_chillas is not None and wf_chillas.category == "chilla_pancake"
    assert wf_dal is not None and wf_dal.category == "dal"

    # 2. Confirmed alias (Chini DF -> sugar) vs genuine mismatch (Jaggery -> sugar) & unconfirmed
    base_smoothie = {
        "title": "Banana Almond Smoothie",
        "servings": 2,
        "prep_minutes": 5,
        "cook_minutes": 0,
        "difficulty": "easy",
        "ingredients": [
            {"name": "Banana", "quantity": "200 g"},
            {"name": "Milk", "quantity": "300 ml"},
            {
                "name": "Chini DF",
                "quantity": "2 tsp",
                "canonical_name": "sugar",
                "confirmed_aliases": ["chini", "sugar"],
            },
        ],
        "preparation_steps": ["Peel and slice 200 g banana."],
        "structured_steps": [
            {
                "step_number": 1,
                "title": "Blend Banana, Milk & Sugar",
                "instruction": (
                    "Blend 200 g banana, 300 ml chilled milk, and 2 tsp sugar until smooth."
                ),
                "ingredients_used": ["Banana", "Milk", "Chini DF"],
                "ingredient_quantities": {"Banana": "200 g", "Milk": "300 ml", "Chini DF": "2 tsp"},
                "heat_level": None,
                "duration_minutes": 2,
                "completion_cue": "Smoothie is frothy and evenly blended.",
            },
            {
                "step_number": 2,
                "title": "Pour & Serve Chilled",
                "instruction": "Serve chilled in glasses.",  # 25 chars or concise 20+ char check
                "ingredients_used": [],
                "heat_level": None,
                "duration_minutes": 1,
                "completion_cue": "Glasses are filled evenly.",
            },
        ],
    }
    res_alias_ok = validate_recipe_quality(base_smoothie, strict_ai_generated=True)
    assert res_alias_ok.is_valid, res_alias_ok.issues

    # Without confirmed alias, Chini DF must NOT be treated as sugar
    unconfirmed = {
        **base_smoothie,
        "ingredients": [
            {"name": "Banana", "quantity": "200 g"},
            {"name": "Milk", "quantity": "300 ml"},
            {"name": "Chini DF", "quantity": "10 g"},
        ],
    }
    res_unconfirmed = validate_recipe_quality(unconfirmed, strict_ai_generated=True)
    assert not res_unconfirmed.is_valid
    assert any("sugar" in i.lower() for i in res_unconfirmed.issues)

    # Genuine mismatch: Jaggery listed, instruction mentions sugar -> must fail
    jaggery_mismatch = {
        **base_smoothie,
        "ingredients": [
            {"name": "Banana", "quantity": "200 g"},
            {"name": "Milk", "quantity": "300 ml"},
            {"name": "Jaggery", "quantity": "10 g", "canonical_name": "jaggery"},
        ],
    }
    res_jaggery = validate_recipe_quality(jaggery_mismatch, strict_ai_generated=True)
    assert not res_jaggery.is_valid
    assert any("sugar" in i.lower() for i in res_jaggery.issues)

    # 3. Concise meaningful instruction (19 chars) passes, vague short instruction fails
    concise_ok = {
        **base_smoothie,
        "structured_steps": [
            base_smoothie["structured_steps"][0],
            {**base_smoothie["structured_steps"][1], "instruction": "Serve cold in cups."},
        ],
    }
    assert validate_recipe_quality(concise_ok, strict_ai_generated=True).is_valid

    vague_short = {
        **base_smoothie,
        "structured_steps": [
            base_smoothie["structured_steps"][0],
            {**base_smoothie["structured_steps"][1], "instruction": "Cook until done."},
        ],
    }
    res_vague = validate_recipe_quality(vague_short, strict_ai_generated=True)
    assert not res_vague.is_valid


def test_salad_classification_positive_and_negative() -> None:
    from app.modules.recipes.flatbread_and_workflows import detect_dish_workflow
    from app.modules.recipes.quality_validator import validate_recipe_quality

    wf_salad = detect_dish_workflow({"title": "Chana Dal Salad with Pomegranate"})
    wf_dal = detect_dish_workflow({"title": "Chana Dal Tadka"})
    assert wf_salad is not None and wf_salad.category == "salad"
    assert wf_dal is not None and wf_dal.category == "dal"

    chana_dal_salad = {
        "title": "Chana Dal Salad with Pomegranate",
        "servings": 2,
        "prep_minutes": 15,
        "cook_minutes": 10,
        "difficulty": "easy",
        "ingredients": [
            {"name": "Chana dal", "quantity": "100 g"},
            {"name": "Water", "quantity": "300 ml"},
            {"name": "Onion", "quantity": "50 g"},
            {"name": "Salt", "quantity": "3 g"},
            {"name": "Lemon juice", "quantity": "1 tbsp"},
        ],
        "preparation_steps": ["Rinse 100 g chana dal and finely chop 50 g onion."],
        "structured_steps": [
            {
                "step_number": 1,
                "title": "Boil & Drain Chana Dal",
                "instruction": (
                    "Boil 100 g chana dal in 300 ml water on medium heat for 10 minutes "
                    "until tender yet firm, then drain and cool."
                ),
                "ingredients_used": ["Chana dal", "Water"],
                "heat_level": "medium",
                "duration_minutes": 10,
                "completion_cue": "Dal is tender without turning mushy.",
            },
            {
                "step_number": 2,
                "title": "Chop & Combine Base",
                "instruction": (
                    "Combine cooled chana dal with 50 g chopped onion in a mixing bowl."
                ),
                "ingredients_used": ["Onion"],
                "heat_level": None,
                "duration_minutes": 2,
                "completion_cue": "Ingredients are evenly distributed.",
            },
            {
                "step_number": 3,
                "title": "Toss & Season Salad",
                "instruction": ("Add 3 g salt and 1 tbsp lemon juice, then toss gently to coat."),
                "ingredients_used": ["Salt", "Lemon juice"],
                "heat_level": None,
                "duration_minutes": 1,
                "completion_cue": "Salad is evenly seasoned and bright.",
            },
        ],
    }
    res_salad = validate_recipe_quality(chana_dal_salad, strict_ai_generated=True)
    assert res_salad.is_valid, res_salad.issues


def test_pantry_reconciliation_separation_buckets_and_variants() -> None:
    from app.modules.discovery.daily_generator import compute_recipe_availability
    from app.modules.recipes.quality_validator import validate_recipe_quality

    # 1. Chole with Jeera Rice includes rice even when pantry has NO rice
    jeera_rice_rec = {
        "title": "Chole with Jeera Rice",
        "servings": 2,
        "prep_minutes": 15,
        "cook_minutes": 25,
        "difficulty": "medium",
        "ingredients": [
            {"name": "Basmati rice", "quantity": "200 g", "is_optional": False},
            {"name": "Chickpeas", "quantity": "200 g", "is_optional": False},
            {"name": "Water", "quantity": "450 ml", "is_optional": False},
            {"name": "Cumin seeds", "quantity": "4 g", "is_optional": False},
            {"name": "Oil", "quantity": "15 ml", "is_optional": False},
            {"name": "Salt", "quantity": "5 g", "is_optional": False},
            {"name": "Coriander leaves", "quantity": "10 g", "is_optional": True},
        ],
        "preparation_steps": [
            "Rinse and soak 200 g basmati rice for 15 minutes; drain 200 g chickpeas."
        ],
        "structured_steps": [
            {
                "step_number": 1,
                "title": "Temper Cumin & Cook Rice",
                "instruction": (
                    "Heat 15 ml oil in a pot on medium heat, crackle 4 g cumin seeds, "
                    "add 200 g soaked basmati rice, 450 ml water, and 3 g salt."
                ),
                "ingredients_used": ["Oil", "Cumin seeds", "Basmati rice", "Water", "Salt"],
                "heat_level": "medium",
                "duration_minutes": 12,
                "completion_cue": "Rice grains are tender and fluffy after steaming.",
            },
            {
                "step_number": 2,
                "title": "Simmer Chickpeas",
                "instruction": (
                    "Simmer 200 g chickpeas with remaining 2 g salt on medium-low heat "
                    "for 10 minutes until tender."
                ),
                "ingredients_used": ["Chickpeas", "Salt"],
                "heat_level": "medium-low",
                "duration_minutes": 10,
                "completion_cue": "Chickpeas mash easily between fingers.",
            },
            {
                "step_number": 3,
                "title": "Fluff Rice & Serve",
                "instruction": (
                    "Rest rice for 3 minutes, fluff gently with a fork, garnish with "
                    "10 g coriander leaves, and serve with chole."
                ),
                "ingredients_used": ["Coriander leaves"],
                "heat_level": None,
                "duration_minutes": 3,
                "completion_cue": "Rice is fluffy and garnished evenly.",
            },
        ],
    }

    # Pantry has chickpeas, cumin, oil, salt, water, and partial coriander, but NO rice
    pantry_no_rice = [
        {
            "canonical_item_id": "11111111-1111-1111-1111-111111111111",
            "name": "Chickpeas",
            "quantity": 500,
            "base_unit": "g",
        },
        {
            "canonical_item_id": "22222222-2222-2222-2222-222222222222",
            "name": "Water",
            "quantity": 2000,
            "base_unit": "ml",
        },
        {
            "canonical_item_id": "33333333-3333-3333-3333-333333333333",
            "name": "Cumin seeds",
            "quantity": 50,
            "base_unit": "g",
        },
        {
            "canonical_item_id": "44444444-4444-4444-4444-444444444444",
            "name": "Oil",
            "quantity": 500,
            "base_unit": "ml",
        },
        {
            "canonical_item_id": "55555555-5555-5555-5555-555555555555",
            "name": "Salt",
            "quantity": 500,
            "base_unit": "g",
        },
        {
            "canonical_item_id": "66666666-6666-6666-6666-666666666666",
            "name": "Coriander leaves",
            "quantity": 5,
            "base_unit": "g",
        },
    ]
    avail_1 = compute_recipe_availability(jeera_rice_rec, pantry_no_rice)
    assert [i["name"] for i in avail_1["missing_ingredients"]] == ["Basmati rice"]
    # Optional coriander leaves (even partial) must NOT count as missing/partial essential
    assert len(avail_1["partial_ingredients"]) == 0
    assert len(avail_1["optional_ingredients"]) == 1
    # Missing 1-3 essential ingredients -> almost_there
    assert avail_1["availability_bucket"] == "almost_there"

    # 2. All essential ingredients in stock (even with optional missing) -> can_make_now
    pantry_all = pantry_no_rice + [
        {
            "canonical_item_id": "77777777-7777-7777-7777-777777777777",
            "name": "Basmati rice",
            "quantity": 1000,
            "base_unit": "g",
        },
    ]
    avail_all = compute_recipe_availability(jeera_rice_rec, pantry_all)
    assert avail_all["availability_bucket"] == "can_make_now"

    # 3. Missing > 3 essential ingredients -> explore_more
    avail_empty = compute_recipe_availability(jeera_rice_rec, [])
    assert len(avail_empty["missing_ingredients"]) == 6
    assert avail_empty["availability_bucket"] == "explore_more"

    # 4. Vegetable Poha with empty pantry still passes strict recipe quality when poha is listed
    poha_rec = {
        "title": "Vegetable Poha",
        "servings": 2,
        "prep_minutes": 10,
        "cook_minutes": 10,
        "difficulty": "easy",
        "ingredients": [
            {"name": "Poha", "quantity": "150 g", "is_optional": False},
            {"name": "Oil", "quantity": "10 ml", "is_optional": False},
            {"name": "Mustard seeds", "quantity": "2 g", "is_optional": False},
            {"name": "Turmeric", "quantity": "2 g", "is_optional": False},
            {"name": "Salt", "quantity": "3 g", "is_optional": False},
            {"name": "Onion", "quantity": "60 g", "is_optional": False},
        ],
        "preparation_steps": ["Rinse 150 g poha in a colander and drain; chop 60 g onion."],
        "structured_steps": [
            {
                "step_number": 1,
                "title": "Rinse & Drain Poha",
                "instruction": (
                    "Rinse 150 g poha gently under water and drain in a colander to soften."
                ),
                "ingredients_used": ["Poha"],
                "heat_level": None,
                "duration_minutes": 2,
                "completion_cue": "Poha grains are soft and separate.",
            },
            {
                "step_number": 2,
                "title": "Temper Mustard & Onion",
                "instruction": (
                    "Heat 10 ml oil on medium heat, crackle 2 g mustard seeds, and saute "
                    "60 g onion with 2 g turmeric and 3 g salt for 4 minutes."
                ),
                "ingredients_used": ["Oil", "Mustard seeds", "Onion", "Turmeric", "Salt"],
                "heat_level": "medium",
                "duration_minutes": 4,
                "completion_cue": "Onions turn translucent and aromatic.",
            },
            {
                "step_number": 3,
                "title": "Fold & Steam Poha",
                "instruction": (
                    "Fold in softened 150 g poha gently and steam covered on low heat "
                    "for 2 minutes."
                ),
                "ingredients_used": ["Poha"],
                "heat_level": "low",
                "duration_minutes": 2,
                "completion_cue": "Poha is warmed through and fluffy.",
            },
        ],
    }
    avail_poha = compute_recipe_availability(poha_rec, [])
    assert any(i["name"] == "Poha" for i in avail_poha["missing_ingredients"])
    assert validate_recipe_quality(poha_rec, strict_ai_generated=True).is_valid


@pytest.mark.asyncio
async def test_generator_connect_error_records_exception_chain_and_scheduler_retries_once(
    tmp_path,
) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'connect_err.db'}"
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with session_maker() as db:
        u = User(clerk_user_id="clk_conn_err", display_name="Chef")
        db.add(u)
        await db.flush()
        h = Household(name="Retry Home", created_by=u.id)
        db.add(h)
        await db.flush()
        db.add(HouseholdMember(household_id=h.id, user_id=u.id, role="owner"))
        db.add(HouseholdRecipePreference(household_id=h.id, timezone="Asia/Kolkata"))
        await db.commit()
        hid = h.id

    attempts = 0

    def _connect_then_ok_handler(req: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("DNS temporary failure", request=req)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": _eight_valid_recipes_json()}}
                ],
                "usage": {"prompt_tokens": 200, "completion_tokens": 1200, "total_tokens": 1400},
            },
        )

    gen = DailyMealPlanGenerator(
        settings=Settings(HF_TOKEN="test-token"),
        transport=httpx.MockTransport(_connect_then_ok_handler),
    )
    due_utc = datetime(2026, 11, 23, 22, 0, tzinfo=UTC)  # 03:30 IST on 2026-11-24

    # First tick records CONNECTERROR with exception chain in provider_metadata
    assert await run_scheduler_tick(session_maker, utc_now=due_utc, generator=gen) == 1
    assert attempts == 1

    async with session_maker() as db:
        plan = (
            await db.execute(
                select(DailyMealPlan).where(
                    DailyMealPlan.household_id == hid,
                    DailyMealPlan.local_date == date(2026, 11, 24),
                )
            )
        ).scalar_one()
        assert plan.status == "failed"
        assert plan.error_code == "CONNECTERROR"
        assert plan.error_message == "Provider request failed: ConnectError."
        assert "ConnectError: DNS temporary failure" in (
            (plan.provider_metadata or {}).get("error_chain") or ""
        )

    # Immediate tick within cooldown does NOT retry
    assert await run_scheduler_tick(session_maker, utc_now=due_utc, generator=gen) == 0
    assert attempts == 1

    # Tick after 120s cooldown retries once and recovers
    from datetime import timedelta

    async with session_maker() as db:
        stale_plan = (
            await db.execute(
                select(DailyMealPlan).where(
                    DailyMealPlan.household_id == hid,
                    DailyMealPlan.local_date == date(2026, 11, 24),
                )
            )
        ).scalar_one()
        stale_plan.generation_completed_at = datetime.now(UTC) - timedelta(seconds=180)
        await db.commit()

    assert await run_scheduler_tick(session_maker, utc_now=due_utc, generator=gen) == 1
    assert attempts == 2

    async with session_maker() as db:
        recovered = (
            await db.execute(
                select(DailyMealPlan).where(
                    DailyMealPlan.household_id == hid,
                    DailyMealPlan.local_date == date(2026, 11, 24),
                )
            )
        ).scalar_one()
        assert recovered.status == "ready"
        assert (recovered.provider_metadata or {}).get("transient_retry_count") == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_stale_generating_recovery_and_non_stale_untouched(tmp_path) -> None:
    """Covers stale recovery, non-stale protection, same plan ID, no duplicates, and metadata."""
    import asyncio
    from datetime import timedelta

    from app.modules.discovery.daily_models import DailyMealPlanRecipe

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'stale_recovery.db'}"
    engine = create_async_engine(db_url, connect_args={"timeout": 30})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with session_maker() as db:
        u = User(clerk_user_id="clk_stale_1", display_name="Chef")
        db.add(u)
        await db.flush()
        h = Household(name="Stale Kitchen", created_by=u.id)
        db.add(h)
        await db.flush()
        db.add(HouseholdMember(household_id=h.id, user_id=u.id, role="owner"))
        db.add(HouseholdRecipePreference(household_id=h.id, timezone="Asia/Kolkata"))
        await db.flush()

        now_utc = datetime.now(UTC)
        due_utc = datetime(2026, 11, 24, 7, 0, tzinfo=UTC)  # 12:30 IST
        plan = DailyMealPlan(
            household_id=h.id,
            local_date=date(2026, 11, 24),
            timezone="Asia/Kolkata",
            status="generating",
            auto_attempted=True,
            manual_regeneration_count=0,
            error_code="CONNECTERROR",
            error_message="Provider request failed: ConnectError.",
            generation_started_at=now_utc - timedelta(minutes=2),  # non-stale (< 10 min)
            generation_completed_at=now_utc - timedelta(hours=7),
            provider_metadata={
                "provider": "huggingface",
                "model": "Qwen/Qwen2.5-72B-Instruct",
                "transient_retry_count": 1,
            },
        )
        db.add(plan)
        await db.commit()
        hid = h.id
        original_plan_id = plan.id

    attempts = 0

    def _ok_handler(req: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": _eight_valid_recipes_json()}}
                ],
                "usage": {"prompt_tokens": 200, "completion_tokens": 1200, "total_tokens": 1400},
            },
        )

    gen = DailyMealPlanGenerator(
        settings=Settings(HF_TOKEN="test-token"),
        transport=httpx.MockTransport(_ok_handler),
    )

    # 1. Non-stale generating record (< 600s) remains untouched
    assert (
        await run_scheduler_tick(
            session_maker, utc_now=due_utc, generator=gen, stale_threshold_seconds=600
        )
        == 0
    )
    assert attempts == 0

    # 2. Make it stale (> 600s) and test concurrent workers claiming the same stale plan
    async with session_maker() as db:
        row = (
            await db.execute(select(DailyMealPlan).where(DailyMealPlan.id == original_plan_id))
        ).scalar_one()
        row.generation_started_at = datetime.now(UTC) - timedelta(minutes=20)
        await db.commit()

    async def _worker_claim() -> DailyMealPlan | None:
        async with session_maker() as db:
            res = await claim_daily_plan_slot(
                db,
                household_id=hid,
                local_date=date(2026, 11, 24),
                timezone_str="Asia/Kolkata",
                stale_threshold_seconds=600,
            )
            await db.commit()
            return res

    c1, c2 = await asyncio.gather(_worker_claim(), _worker_claim())
    claimed_list = [c for c in (c1, c2) if c is not None]
    assert len(claimed_list) == 1
    assert claimed_list[0].id == original_plan_id

    # Reset generation_started_at back to stale so run_scheduler_tick can run end-to-end
    async with session_maker() as db:
        row = (
            await db.execute(select(DailyMealPlan).where(DailyMealPlan.id == original_plan_id))
        ).scalar_one()
        row.generation_started_at = datetime.now(UTC) - timedelta(minutes=20)
        row.error_code = "CONNECTERROR"
        row.provider_metadata = {
            "provider": "huggingface",
            "model": "Qwen/Qwen2.5-72B-Instruct",
            "transient_retry_count": 1,
        }
        await db.commit()

    # 3. Successful recovery and generation
    assert (
        await run_scheduler_tick(
            session_maker, utc_now=due_utc, generator=gen, stale_threshold_seconds=600
        )
        == 1
    )
    assert attempts == 1

    async with session_maker() as db:
        plans = (
            await db.execute(
                select(DailyMealPlan).where(
                    DailyMealPlan.household_id == hid,
                    DailyMealPlan.local_date == date(2026, 11, 24),
                )
            )
        ).scalars().all()
        assert len(plans) == 1
        recovered = plans[0]
        assert recovered.id == original_plan_id
        assert recovered.status == "ready"
        assert recovered.error_code is None
        meta = recovered.provider_metadata or {}
        assert meta.get("transient_retry_count") == 1
        assert meta.get("stale_recovery_count") == 1
        assert meta.get("interrupted_generation_count") == 1
        assert meta.get("previous_error_code") == "CONNECTERROR"

        recipes = (
            await db.execute(
                select(DailyMealPlanRecipe).where(
                    DailyMealPlanRecipe.daily_meal_plan_id == original_plan_id
                )
            )
        ).scalars().all()
        assert len(recipes) == 8

    await engine.dispose()


@pytest.mark.asyncio
async def test_stale_recovery_retry_limit_and_crash_during_recovery(tmp_path) -> None:
    """Covers retry limit enforcement and process interruption during recovery."""
    from datetime import timedelta

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'stale_limit.db'}"
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    now_utc = datetime.now(UTC)
    due_utc = datetime(2026, 11, 24, 7, 0, tzinfo=UTC)
    async with session_maker() as db:
        u = User(clerk_user_id="clk_stale_2", display_name="Chef")
        db.add(u)
        await db.flush()
        h = Household(name="Crash Kitchen", created_by=u.id)
        db.add(h)
        await db.flush()
        db.add(HouseholdMember(household_id=h.id, user_id=u.id, role="owner"))
        db.add(HouseholdRecipePreference(household_id=h.id, timezone="Asia/Kolkata"))
        await db.flush()
        plan = DailyMealPlan(
            household_id=h.id,
            local_date=date(2026, 11, 24),
            timezone="Asia/Kolkata",
            status="generating",
            auto_attempted=True,
            generation_started_at=now_utc - timedelta(minutes=25),
            provider_metadata={"transient_retry_count": 1},
        )
        db.add(plan)
        await db.commit()
        hid = h.id
        plan_id = plan.id

    # Simulate process interruption *during* recovery:
    # 1) Claim stale slot (stale_recovery_count becomes 1) and commit, but never finish
    async with session_maker() as db:
        claimed = await claim_daily_plan_slot(
            db,
            household_id=hid,
            local_date=date(2026, 11, 24),
            timezone_str="Asia/Kolkata",
            stale_threshold_seconds=600,
        )
        assert claimed is not None
        assert (claimed.provider_metadata or {}).get("stale_recovery_count") == 1
        # Backdate generation_started_at to simulate a second stale crash
        claimed.generation_started_at = datetime.now(UTC) - timedelta(minutes=25)
        await db.commit()

    # 2) Next scheduler tick sees stale_recovery_count == 1 -> must NOT loop; transitions to failed
    attempts = 0

    def _never_called(req: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json={})

    gen = DailyMealPlanGenerator(
        settings=Settings(HF_TOKEN="test-token"),
        transport=httpx.MockTransport(_never_called),
    )
    assert (
        await run_scheduler_tick(
            session_maker, utc_now=due_utc, generator=gen, stale_threshold_seconds=600
        )
        == 0
    )
    assert attempts == 0

    async with session_maker() as db:
        final_plan = (
            await db.execute(select(DailyMealPlan).where(DailyMealPlan.id == plan_id))
        ).scalar_one()
        assert final_plan.status == "failed"
        assert final_plan.error_code == "STALE_GENERATION_INTERRUPTED"
        assert (final_plan.provider_metadata or {}).get("stale_recovery_count") == 1
        assert (final_plan.provider_metadata or {}).get("interrupted_generation_count") == 2

        # Failed record still surfaces meaningful error via daily_service
        resp = await get_daily_plan_for_household(db, hid, utc_now=due_utc)
        assert resp.status == "failed"
        assert resp.error_code == "STALE_GENERATION_INTERRUPTED"
        assert resp.error_message is not None and "interrupted" in resp.error_message.lower()

    # Subsequent tick must not retry or loop
    assert (
        await run_scheduler_tick(
            session_maker,
            utc_now=due_utc + timedelta(minutes=15),
            generator=gen,
            stale_threshold_seconds=600,
        )
        == 0
    )
    assert attempts == 0

    await engine.dispose()


def test_dish_classification_stuffed_paratha_with_dal_chilla_and_dal_tadka() -> None:
    """Stuffed Paratha with Moong Dal must not trigger Dal Tadka workflow or spices."""
    from app.modules.recipes.flatbread_and_workflows import (
        detect_dish_workflow,
        detect_flatbread_spec,
    )
    from app.modules.recipes.ingredient_knowledge import detect_dish_ingredient_profile
    from app.modules.recipes.quality_validator import validate_recipe_quality

    paratha = {
        "title": "Stuffed Paratha with Moong Dal",
        "servings": 2,
        "prep_minutes": 20,
        "cook_minutes": 15,
        "difficulty": "medium",
        "ingredients": [
            {"name": "Whole wheat flour (atta)", "quantity": "180 g", "is_optional": False},
            {"name": "Water", "quantity": "100 ml", "is_optional": False},
            {"name": "Moong dal", "quantity": "80 g", "is_optional": False},
            {"name": "Salt", "quantity": "3 g", "is_optional": False},
            {"name": "Ghee", "quantity": "15 ml", "is_optional": False},
        ],
        "preparation_steps": [
            "Knead 180 g whole wheat flour with 100 ml water and 2 g salt into a soft dough; "
            "cook and mash 80 g moong dal with 1 g salt for stuffing."
        ],
        "structured_steps": [
            {
                "step_number": 1,
                "title": "Rest Dough & Prepare Stuffing",
                "instruction": (
                    "Rest the kneaded dough for 10 minutes while seasoning the cooked 80 g "
                    "moong dal stuffing."
                ),
                "ingredients_used": ["Whole wheat flour (atta)", "Water", "Moong dal", "Salt"],
                "heat_level": None,
                "duration_minutes": 10,
                "completion_cue": "Dough feels pliable and stuffing is dry and crumbly.",
            },
            {
                "step_number": 2,
                "title": "Stuff & Roll Paratha",
                "instruction": (
                    "Divide dough into balls, fill with moong dal stuffing, seal edges, and "
                    "roll gently into even discs."
                ),
                "ingredients_used": ["Whole wheat flour (atta)", "Moong dal"],
                "heat_level": None,
                "duration_minutes": 5,
                "completion_cue": "Stuffed discs are rolled evenly without tearing.",
            },
            {
                "step_number": 3,
                "title": "Roast on Tawa with Ghee",
                "instruction": (
                    "Cook each paratha on a hot tawa over medium heat for 3 minutes per side, "
                    "brushing 15 ml ghee until golden spots appear."
                ),
                "ingredients_used": ["Ghee"],
                "heat_level": "medium",
                "duration_minutes": 6,
                "completion_cue": "Both sides are golden brown with crisp edges.",
            },
        ],
    }

    assert detect_flatbread_spec(paratha) is not None
    assert detect_flatbread_spec(paratha).key == "stuffed_paratha"
    assert detect_dish_workflow(paratha) is None
    assert detect_dish_ingredient_profile(paratha).key == "stuffed_paratha"
    assert validate_recipe_quality(paratha, strict_ai_generated=True).is_valid

    chilla = {"title": "Moong Dal Chilla"}
    assert detect_dish_workflow(chilla).category == "chilla_pancake"
    assert detect_dish_ingredient_profile(chilla).key == "chilla_pancake"

    dal_tadka = {
        "title": "Chana Dal Tadka",
        "servings": 2,
        "prep_minutes": 10,
        "cook_minutes": 20,
        "difficulty": "easy",
        "ingredients": [
            {"name": "Chana dal", "quantity": "150 g", "is_optional": False},
            {"name": "Water", "quantity": "450 ml", "is_optional": False},
            {"name": "Salt", "quantity": "4 g", "is_optional": False},
            {"name": "Turmeric", "quantity": "2 g", "is_optional": False},
            {"name": "Oil", "quantity": "10 ml", "is_optional": False},
            {"name": "Cumin seeds", "quantity": "3 g", "is_optional": False},
        ],
        "preparation_steps": ["Rinse and soak 150 g chana dal in water for 15 minutes."],
        "structured_steps": [
            {
                "step_number": 1,
                "title": "Boil Chana Dal",
                "instruction": (
                    "Pressure cook 150 g chana dal with 450 ml water, 2 g turmeric, and "
                    "4 g salt on medium heat for 12 minutes until soft."
                ),
                "ingredients_used": ["Chana dal", "Water", "Turmeric", "Salt"],
                "heat_level": "medium",
                "duration_minutes": 12,
                "completion_cue": "Dal is tender and mashes easily.",
            },
            {
                "step_number": 2,
                "title": "Prepare Tadka Tempering",
                "instruction": (
                    "Heat 10 ml oil in a small pan and crackle 3 g cumin seeds for 1 minute."
                ),
                "ingredients_used": ["Oil", "Cumin seeds"],
                "heat_level": None,  # Missing heat_level on active cooking step!
                "duration_minutes": 1,
                "completion_cue": "Cumin seeds crackle and turn fragrant.",
            },
            {
                "step_number": 3,
                "title": "Combine & Simmer Dal",
                "instruction": (
                    "Pour the hot cumin tempering over the cooked chana dal and simmer "
                    "on low heat for 3 minutes before serving."
                ),
                "ingredients_used": ["Chana dal", "Cumin seeds"],
                "heat_level": "low",
                "duration_minutes": 3,
                "completion_cue": "Dal thickens slightly and absorbs the tempering aroma.",
            },
        ],
    }
    assert detect_dish_workflow(dal_tadka).category == "dal"
    assert detect_dish_ingredient_profile(dal_tadka).key == "dal_tadka"
    res_missing_heat = validate_recipe_quality(dal_tadka, strict_ai_generated=True)
    assert not res_missing_heat.is_valid
    assert any("Cooking step 2 is missing heat_level." in i for i in res_missing_heat.issues)

    dal_tadka["structured_steps"][1]["heat_level"] = "medium"
    assert validate_recipe_quality(dal_tadka, strict_ai_generated=True).is_valid


def test_substitutions_and_troubleshooting_tips_normalization() -> None:
    """Normalizer handles strings, dicts, list of dicts, and None without losing content."""
    from app.modules.discovery.daily_generator import (
        _DailyPlanLLMEnvelope,
        _normalize_daily_envelope_payload,
    )

    raw_payload = {
        "recipes": [
            {
                **_make_valid_recipe("Cumin Dal", "lunch"),
                "substitutions": {"Ghee": "Use 10 ml neutral oil instead"},
                "troubleshooting_tips": [
                    {"issue": "Too thick", "fix": "Stir in 30 ml warm water"},
                ],
                "cooking_tips": None,
                "food_safety_notes": "Serve hot within 2 hours.",
            }
        ]
    }
    normalized = _normalize_daily_envelope_payload(raw_payload)
    env = _DailyPlanLLMEnvelope.model_validate(normalized)
    rec = env.recipes[0]
    assert rec.substitutions == ["Ghee: Use 10 ml neutral oil instead"]
    assert rec.troubleshooting_tips == ["Too thick: Stir in 30 ml warm water"]
    assert rec.cooking_tips == []
    assert rec.food_safety_notes == ["Serve hot within 2 hours."]


@pytest.mark.asyncio
async def test_quality_repair_preserves_identity_rejects_failed_repair_and_requires_all_eight() -> (
    None
):
    """Covers identity preservation, failed repair remaining invalid, and 8/8 gate."""
    recipes_data = json.loads(_eight_valid_recipes_json())
    # Break heat_level on 1 recipe (snacks-1) so repair is invoked
    recipes_data["recipes"][4]["structured_steps"][0]["heat_level"] = None
    bad_title = recipes_data["recipes"][4]["title"]
    bad_cat = recipes_data["recipes"][4]["meal_category"]

    # Case 1: Repair replaces dish identity -> rejected, 0 recipes persisted
    call_idx = 0

    def _identity_mismatch_handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_idx
        call_idx += 1
        if call_idx == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": json.dumps(recipes_data)}}
                    ]
                },
            )
        wrong_repair = {
            "recipes": [
                {**recipes_data["recipes"][4], "title": "Completely Unrelated Dish"},
            ]
        }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": json.dumps(wrong_repair)}}
                ]
            },
        )

    gen_mismatch = DailyMealPlanGenerator(
        settings=Settings(HF_TOKEN="test-token"),
        transport=httpx.MockTransport(_identity_mismatch_handler),
    )
    out_mismatch = await gen_mismatch.generate({"raw_inventory_payload": []})
    assert not out_mismatch.success
    assert out_mismatch.error_code == "VALIDATION_FAILED"
    assert out_mismatch.provider_metadata.get("quality_repair_identity_ok") is False

    # Case 2: Repair preserves identity but leaves issue unfixed -> rejected
    call_idx = 0

    def _still_invalid_handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_idx
        call_idx += 1
        if call_idx == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": json.dumps(recipes_data)}}
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps({"recipes": [recipes_data["recipes"][4]]})
                        },
                    }
                ]
            },
        )

    gen_still_invalid = DailyMealPlanGenerator(
        settings=Settings(HF_TOKEN="test-token"),
        transport=httpx.MockTransport(_still_invalid_handler),
    )
    out_still_invalid = await gen_still_invalid.generate({"raw_inventory_payload": []})
    assert not out_still_invalid.success
    assert out_still_invalid.error_code == "VALIDATION_FAILED"
    assert out_still_invalid.provider_metadata.get("valid_recipe_count") == 7
    assert out_still_invalid.provider_metadata.get("quality_repair_succeeded") is False

    # Case 3: Repair fixes heat_level while preserving title & category -> all 8 accepted
    call_idx = 0

    def _fixed_handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_idx
        call_idx += 1
        if call_idx == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": json.dumps(recipes_data)}}
                    ]
                },
            )
        fixed_rec = json.loads(json.dumps(recipes_data["recipes"][4]))
        fixed_rec["structured_steps"][0]["heat_level"] = "medium"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps({"recipes": [fixed_rec]})},
                    }
                ]
            },
        )

    gen_fixed = DailyMealPlanGenerator(
        settings=Settings(HF_TOKEN="test-token"),
        transport=httpx.MockTransport(_fixed_handler),
    )
    out_fixed = await gen_fixed.generate({"raw_inventory_payload": []})
    assert out_fixed.success
    assert len(out_fixed.slots) == 8
    assert out_fixed.provider_metadata.get("valid_recipe_count") == 8
    assert out_fixed.provider_metadata.get("quality_repair_succeeded") is True
    assert any(
        s.meal_category == bad_cat and s.recipe_data.get("title") == bad_title
        for s in out_fixed.slots
    )
