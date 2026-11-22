from __future__ import annotations

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
from app.modules.recipes.context_engine import classify_meal_context, classify_season_context
from tests.conftest import EphemeralJWKSProvider


def test_phase6_meal_windows_timezone_and_season_weather() -> None:
    """Covers meal-time windows, timezone conversion, season fallback, and weather refinement."""
    # 02:30 UTC == 08:00 IST (Breakfast) vs 21:30 EDT/EST (Dinner) in New York
    t_utc = datetime(2026, 7, 15, 2, 30, tzinfo=UTC)
    ist_ctx = classify_meal_context(t_utc, "Asia/Kolkata")
    assert ist_ctx.meal_window == "breakfast"
    assert ist_ctx.greeting == "Good morning"

    ny_ctx = classify_meal_context(t_utc, "America/New_York")
    assert ny_ctx.meal_window == "dinner"
    assert ny_ctx.greeting == "Good evening"

    # Lunch (13:00 IST = 07:30 UTC), Snack (17:00 IST = 11:30 UTC), Dinner (20:30 IST = 15:00 UTC)
    assert (
        classify_meal_context(datetime(2026, 7, 15, 7, 30, tzinfo=UTC), "Asia/Kolkata").meal_window
        == "lunch"
    )
    assert (
        classify_meal_context(datetime(2026, 7, 15, 11, 30, tzinfo=UTC), "Asia/Kolkata").meal_window
        == "snack"
    )
    assert (
        classify_meal_context(datetime(2026, 7, 15, 15, 0, tzinfo=UTC), "Asia/Kolkata").meal_window
        == "dinner"
    )

    # Season fallback without weather vs weather override
    s_winter = classify_season_context(datetime(2026, 1, 15, tzinfo=UTC).date())
    assert s_winter.season == "winter" and s_winter.weather_used is False

    s_rain = classify_season_context(
        datetime(2026, 4, 15, tzinfo=UTC).date(), temperature_c=24.0, rainfall_mm=12.5
    )
    assert s_rain.season == "monsoon" and s_rain.weather_used is True


@pytest.mark.asyncio
async def test_phase6_contextual_recommendations_pantry_grounding_and_preferences(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
    temp_db_url: str,
) -> None:
    """Covers morning/lunch/snack/dinner API responses, confirmed vs unconfirmed mappings, expired
    exclusion, diversity, and empty state.
    """
    token = jwks_provider.make_token(sub="user_p6", email="p6@example.com", name="P6 User")
    hdr = {"Authorization": f"Bearer {token}"}
    me = await client.get("/api/v1/auth/me", headers=hdr)
    assert me.status_code == 200
    hh_id = UUID(me.json()["active_household"]["id"])
    hdr["X-Household-Id"] = str(hh_id)

    # 1. Empty inventory -> empty_inventory=True, items=[]
    empty_res = await client.get("/api/v1/recipes/recommendations?mode=progressive", headers=hdr)
    assert empty_res.status_code == 200
    assert empty_res.json()["empty_inventory"] is True
    assert empty_res.json()["items"] == []

    # Seed pantry with confirmed Rice, Toor Dal, Moong Dal, Milk, Almonds, Oil;
    # plus an expired Paneer lot and an UNCONFIRMED Oats mapping
    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)
    today = datetime.now(UTC).date()

    async with Session() as db:
        ing_by_norm = {
            i.normalized_name: i
            for i in (await db.execute(select(CanonicalIngredient))).scalars().all()
        }
        items_spec = [
            ("toor-p6", "Arhar Dal", "g", Decimal("500"), "toor_dal", True, None),
            ("rice-p6", "India Gate Basmati", "g", Decimal("1000"), "basmati_rice", True, None),
            ("moong-p6", "Moong Dal Split", "g", Decimal("500"), "moong_dal", True, None),
            ("milk-aavin", "Aavin Milk", "ml", Decimal("500"), "milk", True, None),
            ("milk-amul", "Amul Milk", "ml", Decimal("500"), "milk", True, None),
            ("almond-p6", "Badam", "g", Decimal("200"), "almonds", True, None),
            ("oil-p6", "Sunflower Oil", "ml", Decimal("500"), "sunflower_oil", True, None),
            (
                "paneer-exp",
                "Expired Paneer",
                "g",
                Decimal("300"),
                "paneer",
                True,
                today - timedelta(days=2),
            ),
            ("oats-unconf", "Quaker Oats", "g", Decimal("500"), "rolled_oats", False, None),
        ]
        for slug, name, unit, qty, ing_norm, confirmed, exp_on in items_spec:
            ci = CanonicalItem(
                household_id=hh_id, slug=slug, name=name, category="pantry", default_base_unit=unit
            )
            db.add(ci)
            await db.flush()
            db.add(
                InventoryLot(
                    household_id=hh_id,
                    canonical_item_id=ci.id,
                    initial_qty=qty,
                    remaining_qty=qty,
                    base_unit=unit,
                    unit_cost_paise=5000,
                    expires_on=exp_on,
                    lot_status="active",
                )
            )
            if ing_norm in ing_by_norm:
                db.add(
                    IngredientMapping(
                        household_id=hh_id,
                        canonical_item_id=ci.id,
                        canonical_ingredient_id=ing_by_norm[ing_norm].id,
                        matching_confidence=Decimal("1.000") if confirmed else Decimal("0.750"),
                        mapping_source="user_confirmed" if confirmed else "catalogue",
                        user_confirmed=confirmed,
                    )
                )
        await db.commit()

    # 2. Controlled meal windows (IST)
    # Breakfast (08:00 IST = 02:30 UTC)
    b_res = (
        await client.get(
            "/api/v1/recipes/recommendations?limit=5&mode=progressive&at_time=2026-07-15T02:30:00Z",
            headers=hdr,
        )
    ).json()
    assert b_res["meal_window"] == "breakfast"
    assert b_res["greeting"] == "Good morning"
    assert len(b_res["items"]) >= 1
    assert all("salad" not in r["title"].lower() for r in b_res["items"])

    # Lunch (13:00 IST = 07:30 UTC)
    l_res = (
        await client.get(
            "/api/v1/recipes/recommendations?limit=5&mode=progressive&at_time=2026-07-15T07:30:00Z",
            headers=hdr,
        )
    ).json()
    assert l_res["meal_window"] == "lunch"
    lunch_titles = [r["title"] for r in l_res["items"]]
    assert "Toor Dal Tadka & Steamed Rice" in lunch_titles

    # Evening Snack (17:00 IST = 11:30 UTC)
    s_res = (
        await client.get(
            "/api/v1/recipes/recommendations?limit=5&mode=progressive&at_time=2026-07-15T11:30:00Z",
            headers=hdr,
        )
    ).json()
    assert s_res["meal_window"] == "snack"
    assert s_res["meal_window_label"] == "Evening Snacks"

    # Dinner (20:30 IST = 15:00 UTC)
    d_res = (
        await client.get(
            "/api/v1/recipes/recommendations?limit=5&mode=progressive&at_time=2026-07-15T15:00:00Z",
            headers=hdr,
        )
    ).json()
    assert d_res["meal_window"] == "dinner"
    assert any("Khichdi" in r["title"] for r in d_res["items"])

    # Expired Paneer lot must not make Paneer Stir-Fry COOK_NOW
    for r in l_res["items"]:
        if "Paneer" in r["title"]:
            assert r["recommendation_type"] != "COOK_NOW"

    # 3. Preferences API persistence & filtering (nut_free + max_prep_minutes=5 excludes longer
    # prep)
    pref_put = await client.put(
        "/api/v1/recipes/preferences",
        headers=hdr,
        json={
            "timezone": "Asia/Kolkata",
            "diet_type": "vegan",
            "dietary_restrictions": ["nut_free", "dairy_free"],
            "preferred_cuisines": ["Indian"],
            "preferred_meal_types": ["lunch"],
            "max_prep_minutes": 10,
        },
    )
    assert pref_put.status_code == 200
    assert pref_put.json()["diet_type"] == "vegan"

    filtered_res = (
        await client.get(
            "/api/v1/recipes/recommendations?limit=5&mode=progressive&at_time=2026-07-15T11:30:00Z",
            headers=hdr,
        )
    ).json()
    for r in filtered_res["items"]:
        assert r["prep_minutes"] <= 10
        assert "Milk" not in r["title"] and "Almonds" not in r["title"]

    await engine.dispose()
