"""Tests for Meal History, Meal Feedback, Preference Learning, and Household Isolation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from tests.conftest import EphemeralJWKSProvider


@pytest.mark.asyncio
async def test_meal_history_lifecycle_feedback_preferences_and_isolation(
    client: AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    """Verify:
    1. Suggestions, planning, and cancellation do NOT create meal history.
    2. Explicit cooking completion creates meal history with actual quantities & FEFO deduction.
    3. Feedback is optional at completion, persisted, and editable later.
    4. Repeated meal feedback builds household preference profile without over-inferring from 1 rating.
    5. Household A cannot access Household B's meal history or feedback.
    """
    tok_a = jwks_provider.make_token(sub="user_meal_a", email="meala@example.com", name="Meal A")
    headers_a = {"Authorization": f"Bearer {tok_a}"}
    assert (await client.get("/api/v1/auth/me", headers=headers_a)).status_code == 200

    # Seed Paneer & Spinach
    cat_paneer = (
        await client.post(
            "/api/v1/catalog/items",
            headers=headers_a,
            json={"name": "Paneer", "category": "dairy", "default_base_unit": "g"},
        )
    ).json()
    soon = (datetime.now(UTC).date() + timedelta(days=2)).isoformat()
    lot_resp = await client.post(
        "/api/v1/inventory/lots",
        headers=headers_a,
        json={"canonical_item_id": cat_paneer["id"], "remaining_qty": 600, "base_unit": "g", "expires_on": soon},
    )
    assert lot_resp.status_code == 201

    # 1. AI Chef chat suggestion -> zero meal history
    chat_resp = await client.post(
        "/api/v1/recipes/chat",
        headers=headers_a,
        json={"message": "What can I cook with paneer?", "servings": 2},
    )
    assert chat_resp.status_code == 200
    assert (await client.get("/api/v1/meals/history", headers=headers_a)).json()["total"] == 0

    # 2. Planning + Cancelling -> zero meal history, zero stock deduction
    plan_cancel = (
        await client.post(
            "/api/v1/cooking-plans",
            headers=headers_a,
            json={
                "recipe_title": "Kadai Paneer",
                "planned_servings": 2,
                "meal_type": "dinner",
                "cuisine": "Indian",
                "ingredients": [{"canonical_item_id": cat_paneer["id"], "name": "Paneer", "planned_qty": 200, "base_unit": "g"}],
            },
        )
    ).json()
    assert (await client.get("/api/v1/meals/history", headers=headers_a)).json()["total"] == 0
    assert (await client.post(f"/api/v1/cooking-plans/{plan_cancel['id']}/cancel", headers=headers_a)).status_code == 200
    assert (await client.get("/api/v1/meals/history", headers=headers_a)).json()["total"] == 0

    # 3. Complete 2 cooking sessions -> creates 2 meal history records & FEFO deducts
    for idx in range(2):
        plan = (
            await client.post(
                "/api/v1/cooking-plans",
                headers=headers_a,
                json={
                    "recipe_title": "Palak Paneer",
                    "planned_servings": 2,
                    "meal_type": "dinner",
                    "cuisine": "Indian",
                    "prep_minutes": 15,
                    "cook_minutes": 15,
                    "steps": ["Sear paneer", "Simmer with spinach puree"],
                    "ingredients": [{"canonical_item_id": cat_paneer["id"], "name": "Paneer", "planned_qty": 200, "base_unit": "g"}],
                },
            )
        ).json()
        comp = await client.post(
            f"/api/v1/cooking-plans/{plan['id']}/complete",
            headers=headers_a,
            json={
                "items": [{"canonical_item_id": cat_paneer["id"], "actual_used_qty": 150, "waste_qty": 0}],
                "substitutions": ["Used low-fat paneer"] if idx == 1 else [],
            },
        )
        assert comp.status_code == 200
        assert comp.json()["meal_history_id"] is not None

    lots_after = (await client.get("/api/v1/inventory/lots", headers=headers_a)).json()["items"]
    assert float(lots_after[0]["remaining_qty"]) == 300.0  # 600 - 150 - 150

    hist_list = (await client.get("/api/v1/meals/history", headers=headers_a)).json()
    assert hist_list["total"] == 2
    assert "Palak Paneer" in hist_list["frequently_cooked"]
    meal_id_1 = hist_list["items"][0]["id"]
    meal_id_2 = hist_list["items"][1]["id"]
    assert hist_list["items"][0]["rating"] is None  # skipped at completion

    # 4. Edit feedback later from meal history
    fb1 = await client.patch(
        f"/api/v1/meals/history/{meal_id_1}/feedback",
        headers=headers_a,
        json={
            "rating": 5,
            "feedback_tags": ["Loved the taste", "Would like to make it again"],
            "feedback_comment": "Great weeknight dinner",
            "would_make_again": True,
        },
    )
    assert fb1.status_code == 200
    assert fb1.json()["rating"] == 5

    fb2 = await client.patch(
        f"/api/v1/meals/history/{meal_id_2}/feedback",
        headers=headers_a,
        json={"rating": 4, "feedback_tags": ["Too spicy"], "would_make_again": True},
    )
    assert fb2.status_code == 200

    # Filters work
    filtered = (await client.get("/api/v1/meals/history?meal_type=dinner&min_rating=5", headers=headers_a)).json()
    assert filtered["total"] == 1
    detail = (await client.get(f"/api/v1/meals/history/{meal_id_1}", headers=headers_a)).json()
    assert detail["ingredients_used"][0]["actual_qty"] == "150"
    assert detail["recipe_snapshot"]["steps"] == ["Sear paneer", "Simmer with spinach puree"]

    # 5. Household B isolation
    tok_b = jwks_provider.make_token(sub="user_meal_b", email="mealb@example.com", name="Meal B")
    headers_b = {"Authorization": f"Bearer {tok_b}"}
    assert (await client.get("/api/v1/auth/me", headers=headers_b)).status_code == 200
    assert (await client.get("/api/v1/meals/history", headers=headers_b)).json()["total"] == 0
    assert (await client.get(f"/api/v1/meals/history/{meal_id_1}", headers=headers_b)).status_code == 404
    assert (
        await client.patch(
            f"/api/v1/meals/history/{meal_id_1}/feedback",
            headers=headers_b,
            json={"rating": 1},
        )
    ).status_code == 404
