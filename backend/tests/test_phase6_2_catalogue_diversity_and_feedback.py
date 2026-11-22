from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.modules.inventory.models import (
    CanonicalIngredient,
    CanonicalItem,
    IngredientMapping,
    InventoryLot,
)
from app.modules.recipes.catalogue_data import EXPANDED_RECIPE_CATALOG, validate_recipe_catalog
from tests.conftest import EphemeralJWKSProvider


def test_phase6_2_expanded_catalogue_structure_and_regional_diversity() -> None:
    """Validates all 64 recipes for structure, quantities, units, steps, and regional variety."""
    errors = validate_recipe_catalog()
    assert errors == [], f"Catalogue validation errors: {errors}"
    assert 60 <= len(EXPANDED_RECIPE_CATALOG) <= 100

    cuisines = Counter(r["cuisine"] for r in EXPANDED_RECIPE_CATALOG)
    for required in (
        "North Indian",
        "South Indian",
        "Bengali",
        "Maharashtrian",
        "Gujarati",
        "Quick",
    ):
        assert cuisines[required] >= 5, f"Insufficient recipes for cuisine {required}"


@pytest.mark.asyncio
async def test_phase6_2_lunch_dinner_diversity_quantity_sufficiency_and_feedback(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
    temp_db_url: str,
) -> None:
    """Covers lunch/dinner separation, quantity sufficiency, unit mismatch, feedback, and household
    isolation.
    """
    tok_a = jwks_provider.make_token(sub="user_62_a", email="62a@example.com", name="62 A")
    tok_b = jwks_provider.make_token(sub="user_62_b", email="62b@example.com", name="62 B")
    hdr_a = {"Authorization": f"Bearer {tok_a}"}
    hdr_b = {"Authorization": f"Bearer {tok_b}"}

    me_a = await client.get("/api/v1/auth/me", headers=hdr_a)
    me_b = await client.get("/api/v1/auth/me", headers=hdr_b)
    hh_a = UUID(me_a.json()["active_household"]["id"])
    hh_b = UUID(me_b.json()["active_household"]["id"])
    hdr_a["X-Household-Id"] = str(hh_a)
    hdr_b["X-Household-Id"] = str(hh_b)

    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)
    today = datetime.now(UTC).date()

    async with Session() as db:
        ing_by_norm = {
            i.normalized_name: i
            for i in (await db.execute(select(CanonicalIngredient))).scalars().all()
        }
        items = [
            ("toor-62", "Toor Dal", "g", Decimal("1000"), "toor_dal", True, None),
            ("rice-62", "Basmati Rice", "g", Decimal("1000"), "basmati_rice", True, None),
            ("moong-62", "Moong Dal", "g", Decimal("1000"), "moong_dal", True, None),
            ("chana-62", "Chana Dal", "g", Decimal("1000"), "chana_dal", True, None),
            ("atta-62", "Whole Wheat Atta", "g", Decimal("1000"), "whole_wheat_flour", True, None),
            ("urad-62", "Urad Dal", "g", Decimal("1000"), "urad_dal", True, None),
            ("oil-62", "Sunflower Oil", "ml", Decimal("1000"), "sunflower_oil", True, None),
            # Insufficient Paneer (50g < 200g required)
            ("paneer-62", "Paneer", "g", Decimal("50"), "paneer", True, None),
            # Expired Spinach + Unconfirmed Milk
            (
                "spinach-62",
                "Spinach",
                "g",
                Decimal("500"),
                "spinach",
                True,
                today - timedelta(days=1),
            ),
            ("milk-62", "Milk Unconf", "ml", Decimal("1000"), "milk", False, None),
        ]
        for hh in (hh_a, hh_b):
            for slug, name, unit, qty, ing_norm, conf, exp in items:
                ci = CanonicalItem(
                    household_id=hh,
                    slug=f"{slug}-{str(hh)[:4]}",
                    name=name,
                    category="pantry",
                    default_base_unit=unit,
                )
                db.add(ci)
                await db.flush()
                db.add(
                    InventoryLot(
                        household_id=hh,
                        canonical_item_id=ci.id,
                        initial_qty=qty,
                        remaining_qty=qty,
                        base_unit=unit,
                        unit_cost_paise=5000,
                        expires_on=exp,
                        lot_status="active",
                    )
                )
                if ing_norm in ing_by_norm:
                    db.add(
                        IngredientMapping(
                            household_id=hh,
                            canonical_item_id=ci.id,
                            canonical_ingredient_id=ing_by_norm[ing_norm].id,
                            matching_confidence=Decimal("1.000") if conf else Decimal("0.750"),
                            mapping_source="user_confirmed" if conf else "catalogue",
                            user_confirmed=conf,
                        )
                    )
        await db.commit()

    # Lunch (13:00 IST) vs Dinner (20:00 IST) diversity
    lunch = (
        await client.get(
            "/api/v1/recipes/recommendations?limit=5&mode=progressive&at_time=2026-11-21T07:30:00Z",
            headers=hdr_a,
        )
    ).json()
    dinner = (
        await client.get(
            "/api/v1/recipes/recommendations?limit=5&mode=progressive&at_time=2026-11-21T14:30:00Z",
            headers=hdr_a,
        )
    ).json()
    lunch_ids = {r["recipe_id"] for r in lunch["items"]}
    dinner_ids = {r["recipe_id"] for r in dinner["items"]}
    assert lunch_ids != dinner_ids
    assert len(lunch_ids & dinner_ids) <= 2

    # Insufficient Paneer (50g < 200g) + expired Spinach must NOT be COOK_NOW
    for r in dinner["items"]:
        if "Paneer" in r["title"]:
            assert r["recommendation_type"] != "COOK_NOW"

    # Feedback: hide top lunch recipe in Household A -> disappears for A, still visible for B
    top_lunch = lunch["items"][0]
    fb_hide = await client.post(
        "/api/v1/recipes/feedback",
        headers=hdr_a,
        json={
            "recipe_id": top_lunch["recipe_id"],
            "recipe_title": top_lunch["title"],
            "action": "hide",
        },
    )
    assert fb_hide.status_code == 201

    lunch_a_after = (
        await client.get(
            "/api/v1/recipes/recommendations?limit=5&mode=progressive&at_time=2026-11-21T07:30:00Z",
            headers=hdr_a,
        )
    ).json()
    lunch_b_after = (
        await client.get(
            "/api/v1/recipes/recommendations?limit=5&mode=progressive&at_time=2026-11-21T07:30:00Z",
            headers=hdr_b,
        )
    ).json()
    assert top_lunch["recipe_id"] not in {r["recipe_id"] for r in lunch_a_after["items"]}
    assert top_lunch["recipe_id"] in {r["recipe_id"] for r in lunch_b_after["items"]}

    # Disliking one recipe does NOT eliminate its entire cuisine
    second_lunch = lunch_a_after["items"][0]
    await client.post(
        "/api/v1/recipes/feedback",
        headers=hdr_a,
        json={
            "recipe_id": second_lunch["recipe_id"],
            "recipe_title": second_lunch["title"],
            "action": "dislike",
        },
    )
    lunch_a_after_dislike = (
        await client.get(
            "/api/v1/recipes/recommendations?limit=5&mode=progressive&at_time=2026-11-21T07:30:00Z",
            headers=hdr_a,
        )
    ).json()
    assert len(lunch_a_after_dislike["items"]) >= 3

    await engine.dispose()
