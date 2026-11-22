"""Regression tests for backend-controlled inventory grounding, unit validation,
recommendation eligibility, and real-pantry data verification."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from httpx import AsyncClient

from app.infra.llm.hf_provider import HuggingFaceRecipeProvider
from app.modules.recipes.grounding import (
    classify_recommendation_type,
    is_blocked_ingredient_name,
    parse_quantity_and_unit,
    reconcile_ingredient,
)
from tests.conftest import EphemeralJWKSProvider


def test_unit_validation_and_context_leak_blocking() -> None:
    """Rejects context-leak strings, preserves explicit units, and repairs invalid units."""
    assert is_blocked_ingredient_name("expiring") is True
    assert is_blocked_ingredient_name("available household inventory") is True
    assert is_blocked_ingredient_name("Spinach") is False

    # Preserves '100 g' instead of turning it into '100 pcs'
    q1, u1 = parse_quantity_and_unit("100 g", None, ingredient_name="Spinach")
    assert q1 == Decimal("100")
    assert u1 == "g"

    # Repairs impossible '100 pcs' on spinach -> '100 g'
    q2, u2 = parse_quantity_and_unit("100", "pcs", ingredient_name="Spinach")
    assert q2 == Decimal("100")
    assert u2 == "g"

    # Repairs fractional pcs on count-based whole items (1.5 pcs cucumber -> 2 pcs)
    q3, u3 = parse_quantity_and_unit("1.5", "pcs", ingredient_name="Cucumber")
    assert q3 == Decimal("2")
    assert u3 == "pcs"


def test_reconcile_partial_missing_substitutions_and_eligibility() -> None:
    """Verifies COOK_NOW, partial quantities, COOK_WITH_SUBSTITUTIONS, and REQUIRES_SHOPPING."""
    inv = [
        {
            "canonical_item_id": "11111111-1111-1111-1111-111111111111",
            "name": "Green Cucumber (Sowthekaayi)",
            "normalized_name": "green cucumber",
            "qty": 1.0,
            "base_unit": "pcs",
            "expiring_soon": False,
        },
        {
            "canonical_item_id": "22222222-2222-2222-2222-222222222222",
            "name": "Fresh Paneer",
            "normalized_name": "fresh paneer",
            "qty": 100.0,
            "base_unit": "g",
            "expiring_soon": True,
        },
    ]

    # All available -> COOK_NOW
    ing_cuc = reconcile_ingredient({"name": "cucumber", "quantity": "1 pcs"}, inv)
    assert ing_cuc is not None and ing_cuc["status"] == "available"
    assert classify_recommendation_type([ing_cuc], [], inv) == "COOK_NOW"

    # Partial quantity (need 200 g paneer, have 100 g) -> partial + missing_qty='100'
    ing_pan = reconcile_ingredient({"name": "paneer", "quantity": "200 g"}, inv)
    assert ing_pan is not None
    assert ing_pan["status"] == "partial"
    assert ing_pan["missing_qty"] == "100"

    # Missing core ingredient with pantry substitution -> COOK_WITH_SUBSTITUTIONS
    ing_miss = reconcile_ingredient({"name": "tofu", "quantity": "150 g"}, inv)
    assert ing_miss is not None and ing_miss["status"] == "missing"
    assert (
        classify_recommendation_type(
            [ing_cuc, ing_miss],
            ["Replace tofu with fresh paneer"],
            inv,
        )
        == "COOK_WITH_SUBSTITUTIONS"
    )

    # Missing core ingredient without substitution -> REQUIRES_SHOPPING
    assert classify_recommendation_type([ing_miss], [], inv) == "REQUIRES_SHOPPING"


@pytest.mark.asyncio
async def test_api_strips_hallucinated_availability_context_leaks_and_false_expiry(
    client: AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even if an LLM returns 'expiring', '100 pcs' spinach, or claims missing items are available,
    the backend strips context leaks, repairs units, verifies expiry claims,
    and excludes non-food."""
    tok = jwks_provider.make_token(sub="user_ground_1", email="ground@example.com", name="Ground")
    headers = {"Authorization": f"Bearer {tok}"}
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 200

    # Seed 1 edible item (Cucumber) and 1 non-food item (Matchbox)
    cat_cuc = (
        await client.post(
            "/api/v1/catalog/items",
            headers=headers,
            json={
                "name": "Green Cucumber (Sowthekaayi)",
                "category": "vegetable",
                "default_base_unit": "pcs",
            },
        )
    ).json()
    cat_match = (
        await client.post(
            "/api/v1/catalog/items",
            headers=headers,
            json={
                "name": "Happy Mail Matchbox",
                "category": "non_food",
                "default_base_unit": "pcs",
            },
        )
    ).json()
    soon = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()
    await client.post(
        "/api/v1/inventory/lots",
        headers=headers,
        json={
            "canonical_item_id": cat_cuc["id"],
            "remaining_qty": 2,
            "base_unit": "pcs",
            "expires_on": soon,
        },
    )
    await client.post(
        "/api/v1/inventory/lots",
        headers=headers,
        json={"canonical_item_id": cat_match["id"], "remaining_qty": 1, "base_unit": "pcs"},
    )

    # Mock HF Router returning malformed ingredients & false availability/expiry claims
    def bad_hf_response(_req: httpx.Request) -> httpx.Response:
        payload = {
            "reply": "Here are two ideas.",
            "recipes": [
                {
                    "title": "Roasted Pantry Vegetable & Chickpea Skillet",
                    "description": "Zero-waste skillet.",
                    "servings": 2,
                    "prep_minutes": 25,
                    "available_ingredients": [
                        {
                            "name": "Spinach",
                            "quantity": "100",
                            "base_unit": "pcs",
                            "status": "available",
                        },
                        {"name": "expiring", "quantity": "1 pcs", "status": "available"},
                        {
                            "name": "available household inventory",
                            "quantity": "1 pcs",
                            "status": "available",
                        },
                    ],
                    "missing_ingredients": [],
                    "steps": ["Cook spinach."],
                    "uses_expiring_items": ["Spinach"],
                },
                {
                    "title": "Crisp Cucumber Salad",
                    "description": "Fresh cucumber salad.",
                    "servings": 2,
                    "prep_minutes": 10,
                    "available_ingredients": [
                        {"name": "Cucumber", "quantity": "1 pcs", "status": "available"},
                    ],
                    "missing_ingredients": [],
                    "steps": ["Slice cucumber and serve."],
                    "uses_expiring_items": ["Cucumber"],
                },
            ],
            "follow_up_questions": [],
        }
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(payload)}}]}
        )

    monkeypatch.setattr(
        "app.modules.recipes.service._build_provider",
        lambda _s: HuggingFaceRecipeProvider(
            hf_token="hf_test", transport=httpx.MockTransport(bad_hf_response)
        ),
    )

    chat = (
        await client.post(
            "/api/v1/recipes/chat",
            headers=headers,
            json={"message": "What can I cook?", "servings": 2},
        )
    ).json()

    # COOK_NOW (Crisp Cucumber Salad) must be ranked first!
    assert chat["suggestions"][0]["title"] == "Crisp Cucumber Salad"
    assert chat["suggestions"][0]["recommendation_type"] == "COOK_NOW"
    assert chat["suggestions"][0]["uses_expiring_items"] == ["Cucumber"]

    # Chickpea Skillet: context leaks stripped, Spinach moved to missing with '100 g', false
    # expiry stripped!
    skillet = chat["suggestions"][1]
    assert skillet["recommendation_type"] == "REQUIRES_SHOPPING"
    assert skillet["available_ingredients"] == []
    assert skillet["uses_expiring_items"] == []
    missing_names = [m["name"].lower() for m in skillet["missing_ingredients"]]
    assert "expiring" not in missing_names
    assert "available household inventory" not in missing_names
    spinach = next(m for m in skillet["missing_ingredients"] if m["name"].lower() == "spinach")
    assert spinach["base_unit"] == "g"
    assert spinach["quantity"] == "100"
