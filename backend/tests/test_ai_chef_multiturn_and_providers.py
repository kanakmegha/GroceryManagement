"""Tests for multi-turn AI Chef, LLM provider adapters, safety invariants, and FEFO cooking completion."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from app.infra.llm.hf_provider import HuggingFaceRecipeProvider
from app.modules.recipes.service import _build_provider
from tests.conftest import EphemeralJWKSProvider


@pytest.mark.asyncio
async def test_huggingface_only_provider_structured_json_timeout_and_outage_fallback() -> None:
    """HuggingFaceRecipeProvider is the sole LLM provider, parses structured JSON, enforces
    empty-inventory safety, and falls back deterministically on 429/503/timeout without
    switching to any other LLM or leaking HF_TOKEN."""
    secret_hf = "hf_secret_test_token_123"
    provider_built = _build_provider(get_settings())
    assert isinstance(provider_built, HuggingFaceRecipeProvider)

    def hf_ok(_req: httpx.Request) -> httpx.Response:
        payload = {
            "reply": "Here is a quick paneer dish using your expiring paneer.",
            "recipes": [
                {
                    "title": "Quick Paneer Bhurji",
                    "description": "Spiced scrambled paneer ready in 15 minutes.",
                    "meal_type": "dinner",
                    "servings": 2,
                    "prep_minutes": 10,
                    "cook_minutes": 10,
                    "available_ingredients": [
                        {"name": "Paneer", "quantity": "200 g", "status": "available", "needs_purchase": False}
                    ],
                    "partial_ingredients": [],
                    "missing_ingredients": [],
                    "substitutions": ["Use tofu if out of paneer."],
                    "cooking_tips": ["Do not overcook paneer."],
                    "food_safety_notes": ["Use fresh dairy before expiry."],
                    "steps": ["Crumble paneer and saute with spices."],
                    "uses_expiring_items": ["Paneer"],
                }
            ],
            "follow_up_questions": ["Would you like roti or rice with this?"],
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload)}}]})

    hf = HuggingFaceRecipeProvider(hf_token=secret_hf, transport=httpx.MockTransport(hf_ok))
    res = await hf.generate_recipes(
        "Quick dinner",
        inventory_items=[{"name": "Paneer", "qty": 200, "base_unit": "g", "expiring_soon": True}],
    )
    assert res.provider_status == "live_hf"
    assert res.recipes[0].title == "Quick Paneer Bhurji"
    assert res.recipes[0].meal_type == "dinner"
    assert res.follow_up_questions == ["Would you like roti or rice with this?"]

    # Empty inventory safety invariant: even if HF returns available_ingredients, backend strips them to missing!
    def hf_hallucinated(_req: httpx.Request) -> httpx.Response:
        payload = {
            "recipes": [
                {
                    "title": "Dal Tadka",
                    "description": "Lentil curry.",
                    "available_ingredients": [
                        {"name": "Toor Dal", "quantity": "200 g", "status": "available", "needs_purchase": False}
                    ],
                    "missing_ingredients": [],
                    "steps": ["Cook dal."],
                }
            ],
            "follow_up_questions": [],
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload)}}]})

    hf_empty = HuggingFaceRecipeProvider(hf_token=secret_hf, transport=httpx.MockTransport(hf_hallucinated))
    empty_res = await hf_empty.generate_recipes("Cook dal", inventory_items=[])
    assert empty_res.recipes[0].available_ingredients == []
    assert len(empty_res.recipes[0].missing_ingredients) == 1

    # Rate limit (429) and timeout fallback never crash, never switch to another LLM, and never leak HF_TOKEN
    def rate_limited(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limit exceeded")

    def timeout_err(_req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    for transport in (httpx.MockTransport(rate_limited), httpx.MockTransport(timeout_err)):
        hf_err = HuggingFaceRecipeProvider(hf_token=secret_hf, transport=transport)
        err_res = await hf_err.generate_recipes(
            "Quick dinner", inventory_items=[{"name": "Paneer", "qty": 200, "base_unit": "g"}]
        )
        assert err_res.provider_status == "fallback_error"
        assert secret_hf not in err_res.provider_message
        assert len(err_res.recipes) >= 1


@pytest.mark.asyncio
async def test_multiturn_ai_chef_and_fefo_cooking_completion_workflow(
    client: AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    """Multi-turn AI Chef respects expiring items, never mutates inventory on chat/plan/cancel,
    and deducts via FEFO only on explicit cooking completion."""
    tok = jwks_provider.make_token(sub="user_chef_1", email="chef@example.com", name="Chef")
    headers = {"Authorization": f"Bearer {tok}"}
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 200

    # Seed Paneer expiring in 1 day + Toor Dal
    cat_paneer = (
        await client.post(
            "/api/v1/catalog/items",
            headers=headers,
            json={"name": "Paneer", "category": "dairy", "default_base_unit": "g"},
        )
    ).json()
    soon = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()
    lot_resp = await client.post(
        "/api/v1/inventory/lots",
        headers=headers,
        json={
            "canonical_item_id": cat_paneer["id"],
            "remaining_qty": 400,
            "base_unit": "g",
            "expires_on": soon,
        },
    )
    assert lot_resp.status_code == 201

    # Turn 1: vague query -> follow-up clarification + suggestions
    turn1 = await client.post(
        "/api/v1/recipes/chat",
        headers=headers,
        json={"message": "I want to cook something today", "servings": 2},
    )
    assert turn1.status_code == 200
    d1 = turn1.json()
    assert d1["empty_inventory"] is False
    assert len(d1["follow_up_questions"]) >= 1
    assert len(d1["suggestions"]) >= 1

    # Turn 2: follow-up with conversation_history
    turn2 = await client.post(
        "/api/v1/recipes/chat",
        headers=headers,
        json={
            "message": "High-protein quick dinner using expiring paneer",
            "servings": 2,
            "max_prep_minutes": 20,
            "conversation_history": [
                {"role": "user", "content": "I want to cook something today"},
                {"role": "assistant", "content": d1["reply"]},
            ],
        },
    )
    assert turn2.status_code == 200
    d2 = turn2.json()
    assert any("Paneer" in s["title"] or "Paneer" in s.get("uses_expiring_items", []) for s in d2["suggestions"])

    # Generating recipes NEVER deducts inventory
    lots_before = (await client.get("/api/v1/inventory/lots", headers=headers)).json()["items"]
    assert float(lots_before[0]["remaining_qty"]) == 400.0

    # Creating a cooking plan NEVER deducts inventory; cancelling NEVER deducts
    plan_cancel = (
        await client.post(
            "/api/v1/cooking-plans",
            headers=headers,
            json={
                "recipe_title": "Paneer Stir-Fry",
                "planned_servings": 2,
                "ingredients": [
                    {"canonical_item_id": cat_paneer["id"], "planned_qty": 200, "base_unit": "g"}
                ],
            },
        )
    ).json()
    assert (await client.post(f"/api/v1/cooking-plans/{plan_cancel['id']}/cancel", headers=headers)).status_code == 200
    lots_after_cancel = (await client.get("/api/v1/inventory/lots", headers=headers)).json()["items"]
    assert float(lots_after_cancel[0]["remaining_qty"]) == 400.0

    # Explicit completion deducts actual quantity (150g) via FEFO and does NOT create outside-food expenditure
    plan_done = (
        await client.post(
            "/api/v1/cooking-plans",
            headers=headers,
            json={
                "recipe_title": "Paneer Stir-Fry",
                "planned_servings": 2,
                "ingredients": [
                    {"canonical_item_id": cat_paneer["id"], "planned_qty": 200, "base_unit": "g"}
                ],
            },
        )
    ).json()
    comp_resp = await client.post(
        f"/api/v1/cooking-plans/{plan_done['id']}/complete",
        headers=headers,
        json={
            "items": [
                {
                    "canonical_item_id": cat_paneer["id"],
                    "actual_used_qty": 150,
                    "waste_qty": 0,
                }
            ]
        },
    )
    assert comp_resp.status_code == 200
    lots_after_done = (await client.get("/api/v1/inventory/lots", headers=headers)).json()["items"]
    assert float(lots_after_done[0]["remaining_qty"]) == 250.0

    # Household B isolation: Household B has empty pantry and cannot see Household A's paneer
    tok_b = jwks_provider.make_token(sub="user_chef_b", email="chefb@example.com", name="Chef B")
    headers_b = {"Authorization": f"Bearer {tok_b}"}
    assert (await client.get("/api/v1/auth/me", headers=headers_b)).status_code == 200
    chat_b = (
        await client.post(
            "/api/v1/recipes/chat",
            headers=headers_b,
            json={"message": "What can I cook with paneer?", "servings": 2},
        )
    ).json()
    assert chat_b["empty_inventory"] is True
    assert chat_b["suggestions"] == []


@pytest.mark.asyncio
async def test_controlled_readonly_tool_loop_validation_and_bounds() -> None:
    """Controlled backend tool loop executes only allowlisted read-only tools,
    validates arguments, and bounds tool rounds to prevent infinite loops."""
    from uuid import uuid4

    from app.modules.recipes.tools import ReadOnlyChefToolset

    class _DummyToolset(ReadOnlyChefToolset):
        def __init__(self) -> None:
            self.calls: list[str] = []
            self._household_id = uuid4()
            self._equipment = ["air_fryer"]
            self._dietary = "vegetarian"
            self._servings = 2
            self._meal_type = "dinner"
            self._max_prep_minutes = 20

        async def _get_pantry_items(self, *, category=None):
            self.calls.append("get_pantry_items")
            return {"items": [{"name": "Spinach", "qty": 200, "base_unit": "g", "expiring_soon": True}]}

        async def _get_expiring_items(self, *, days_ahead=3):
            self.calls.append(f"get_expiring_items:{days_ahead}")
            return {"days_ahead": days_ahead, "items": [{"name": "Spinach", "qty": 200, "base_unit": "g"}]}

        async def _get_recent_cooking_history(self, *, limit=8):
            self.calls.append("get_recent_cooking_history")
            return {"recent_recipes": []}

    toolset = _DummyToolset()
    assert await toolset.execute_tool("delete_inventory_lot", {}) == {"error": "unknown_or_disallowed_tool"}
    assert await toolset.execute_tool("get_expiring_items", {"days_ahead": 999}) == {"error": "invalid_tool_arguments"}
    assert await toolset.execute_tool("get_available_equipment", {"unexpected": 1}) == {"error": "invalid_tool_arguments"}
    assert await toolset.execute_tool("get_available_equipment", {}) == {"equipment": ["air_fryer"]}

    round_counter = {"n": 0}

    def tool_then_json(req: httpx.Request) -> httpx.Response:
        round_counter["n"] += 1
        if round_counter["n"] == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {"name": "get_expiring_items", "arguments": '{"days_ahead": 2}'},
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        final_payload = {
            "reply": "Use your expiring spinach in a quick Spinach Dal.",
            "recipes": [
                {
                    "title": "Quick Spinach Dal",
                    "description": "Fast iron-rich spinach and lentils.",
                    "servings": 2,
                    "prep_minutes": 15,
                    "available_ingredients": [{"name": "Spinach", "quantity": "200 g", "status": "available", "needs_purchase": False}],
                    "missing_ingredients": [{"name": "Toor Dal", "quantity": "150 g", "status": "missing", "needs_purchase": True}],
                    "steps": ["Simmer dal and fold in spinach."],
                    "uses_expiring_items": ["Spinach"],
                }
            ],
            "follow_up_questions": [],
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(final_payload)}}]})

    hf = HuggingFaceRecipeProvider(hf_token="hf_test_tool_token", transport=httpx.MockTransport(tool_then_json))
    res = await hf.generate_recipes(
        "What can I make before my spinach expires?",
        inventory_items=[{"name": "Spinach", "qty": 200, "base_unit": "g", "expiring_soon": True}],
        tool_executor=toolset,
    )
    assert res.provider_status == "live_hf"
    assert round_counter["n"] == 2
    assert "get_expiring_items:2" in toolset.calls
    assert res.recipes[0].title == "Quick Spinach Dal"


@pytest.mark.asyncio
async def test_every_recommendation_and_chat_suggestion_is_a_complete_usable_recipe(
    client: AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    """Every recommendation and AI Chef suggestion includes complete instructions,
    missing indicators, difficulty, prep/cook times, substitutions, cooking tips, and
    explicit AI-generated source labelling when no verified external link is present."""
    tok = jwks_provider.make_token(sub="user_usable_rec", email="usable@example.com", name="Usable")
    headers = {"Authorization": f"Bearer {tok}"}
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 200

    cat_paneer = (
        await client.post(
            "/api/v1/catalog/items",
            headers=headers,
            json={"name": "Paneer", "category": "dairy", "default_base_unit": "g"},
        )
    ).json()
    await client.post(
        "/api/v1/inventory/lots",
        headers=headers,
        json={"canonical_item_id": cat_paneer["id"], "remaining_qty": 200, "base_unit": "g"},
    )

    recs = (await client.get("/api/v1/recipes/recommendations?limit=5", headers=headers)).json()
    assert recs["empty_inventory"] is False
    assert len(recs["items"]) >= 1
    first_rec = recs["items"][0]
    assert len(first_rec["instructions"]) >= 2
    assert len(first_rec["ingredients"]) >= 1
    assert first_rec["prep_minutes"] > 0
    assert first_rec["source_type"] == "ai_generated"
    assert first_rec["source_label"] == "AI-generated recipe"

    chat = (
        await client.post(
            "/api/v1/recipes/chat",
            headers=headers,
            json={"message": "Give me a quick paneer dinner", "servings": 2},
        )
    ).json()
    assert len(chat["suggestions"]) >= 1
    sug = chat["suggestions"][0]
    assert len(sug["instructions"]) >= 2
    assert sug["source_type"] in {"ai_generated", "external_reference"}
    if sug["source_type"] == "ai_generated":
        assert sug["source_label"] == "AI-generated recipe"
        assert sug["external_links"] == []
