"""Phase 7.6 Recipe Instruction Quality, Structured Steps & Validator tests."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.modules.recipes.catalogue_data import EXPANDED_RECIPE_CATALOG
from app.modules.recipes.quality_validator import (
    audit_recipe_catalogue,
    validate_recipe_quality,
)


def test_phase7_6_all_64_catalogue_recipes_pass_quality_validation() -> None:
    total, failed_count, failed = audit_recipe_catalogue(EXPANDED_RECIPE_CATALOG)
    assert total == 64
    assert failed_count == 0, [(f.recipe_id, f.issues) for f in failed]

    roti_dal = next(r for r in EXPANDED_RECIPE_CATALOG if r["recipe_id"] == "cat-roti-urad-dal")
    assert len(roti_dal["structured_steps"]) == 6
    titles = [s["title"].lower() for s in roti_dal["structured_steps"]]
    assert any("dal" in t for t in titles)
    assert any("tadka" in t for t in titles)
    assert any("roti" in t for t in titles)


def test_phase7_6_quality_validator_catches_vague_invented_and_missing_ingredients() -> None:
    bad_recipe = {
        "recipe_id": "bad-1",
        "title": "Whole Wheat Roti & Dal Tadka",
        "dish_family": "roti_dal",
        "ingredients": [
            {"name": "Urad Dal", "qty": 150, "unit": "g", "is_optional": False},
            {"name": "Whole Wheat Flour (Atta)", "qty": 200, "unit": "g", "is_optional": False},
            {"name": "Paneer", "qty": 100, "unit": "g", "is_optional": False},
        ],
        "structured_steps": [
            {
                "step_number": 1,
                "title": "Cook dal",
                "instruction": "Prepare spiced tadka with turmeric and garlic and cook until done.",
                "ingredients_used": ["Urad Dal"],
                "completion_cue": "",
            }
        ],
    }
    res = validate_recipe_quality(bad_recipe)
    assert res.is_valid is False
    joined = " ".join(res.issues)
    assert "at least 4 structured steps" in joined
    assert "vague instruction phrase" in joined
    assert "turmeric" in joined
    assert "garlic" in joined
    assert "Paneer" in joined
    assert "completion cue" in joined


@pytest.mark.asyncio
async def test_phase7_6_recommendations_and_ai_recipe_validation_and_cooked_feedback(
    client: AsyncClient,
    jwks_provider,
    temp_db_url: str,
) -> None:
    from app.core.db import build_async_engine, build_session_maker
    from app.modules.catalog.ingredient_catalogue_sync import sync_catalogue_to_db

    engine = build_async_engine(temp_db_url)
    sm = build_session_maker(engine)
    async with sm() as db:
        await sync_catalogue_to_db(db)

    sub = f"p76-{uuid.uuid4().hex[:8]}"
    token = jwks_provider.make_token(sub=sub, email=f"{sub}@example.com", exp_offset=3600)
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    hh_id = me.json()["active_household"]["id"]
    hdr = {**headers, "X-Household-Id": hh_id}

    # Add pantry items so recommendations return non-empty items
    draft = await client.post(
        "/api/v1/purchases/drafts",
        headers=hdr,
        json={
            "merchant_name": "Test Store",
            "items": [
                {
                    "display_name": "Urad Dal",
                    "qty": 500,
                    "unit": "g",
                    "unit_price_paise": 8000,
                    "line_total_paise": 8000,
                    "user_approved_ingredient": True,
                },
                {
                    "display_name": "Whole Wheat Flour",
                    "qty": 1000,
                    "unit": "g",
                    "unit_price_paise": 6000,
                    "line_total_paise": 6000,
                    "user_approved_ingredient": True,
                },
                {
                    "display_name": "Oil",
                    "qty": 500,
                    "unit": "ml",
                    "unit_price_paise": 9000,
                    "line_total_paise": 9000,
                    "user_approved_ingredient": True,
                },
            ],
        },
    )
    assert draft.status_code == 201
    conf = await client.post(
        f"/api/v1/purchases/drafts/{draft.json()['id']}/confirm",
        headers={**hdr, "Idempotency-Key": str(uuid.uuid4())},
        json={},
    )
    assert conf.status_code == 200

    recs = await client.get("/api/v1/recipes/recommendations?limit=5", headers=hdr)
    assert recs.status_code == 200, recs.text
    items = recs.json()["items"]
    assert len(items) > 0
    for item in items:
        assert len(item["structured_steps"]) >= 3
        assert item["quality_warnings"] == []

    # AI-generated vague recipe is flagged with quality_warnings
    fake_ai_payload = (
        [
            {
                "recipe_id": "ai-vague-1",
                "title": "Quick Dal",
                "servings": 2,
                "ingredients": [
                    {"name": "Urad Dal", "qty": 150, "unit": "g", "is_optional": False}
                ],
                "steps": ["Prepare spiced tadka and cook until done."],
            }
        ],
        {"reply": "Here is an idea.", "provider_status": "live", "model": "test-model"},
    )

    async def _fake_invoke(*args, **kwargs):
        return fake_ai_payload

    with patch("app.modules.recipes.service._invoke_provider", side_effect=_fake_invoke):
        chat_resp = await client.post(
            "/api/v1/recipes/chat",
            headers=hdr,
            json={"message": "Make dal", "servings": 2},
        )
    assert chat_resp.status_code == 200, chat_resp.text
    ai_sug = chat_resp.json()["suggestions"][0]
    assert len(ai_sug["quality_warnings"]) > 0

    lots_before = await client.get("/api/v1/inventory/lots?status=active", headers=hdr)
    assert lots_before.status_code == 200
    before_total = sum(float(it["remaining_qty"]) for it in lots_before.json()["items"])
    assert before_total > 0

    # Cooked feedback succeeds without altering pantry lot remaining_qty
    fb = await client.post(
        "/api/v1/recipes/feedback",
        headers=hdr,
        json={
            "recipe_id": "cat-roti-urad-dal",
            "recipe_title": "Whole Wheat Roti & Urad Dal Tadka",
            "action": "cooked",
        },
    )
    assert fb.status_code == 201, fb.text

    lots = await client.get("/api/v1/inventory/lots?status=active", headers=hdr)
    assert lots.status_code == 200
    assert sum(float(it["remaining_qty"]) for it in lots.json()["items"]) == before_total
    await engine.dispose()


async def _async_return(val):
    return val
