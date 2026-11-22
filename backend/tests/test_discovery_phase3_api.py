from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select

from app.modules.discovery.models import Dish, DishFeedback, DishIngredient
from app.modules.inventory.models import (
    CanonicalIngredient,
    CanonicalItem,
    IngredientMapping,
    InventoryLot,
    StockLedger,
)
from tests.conftest import EphemeralJWKSProvider


async def _seed_dish_and_pantry(
    app: object,
    *,
    household_id: UUID,
) -> tuple[Dish, Dish, Dish]:
    """Seed 3 test dishes and pantry stock for `salt` only under `household_id`."""
    session_maker = app.state.session_maker  # type: ignore[attr-defined]
    async with session_maker() as db:
        salt = (
            await db.execute(
                select(CanonicalIngredient).where(
                    CanonicalIngredient.variant_id == "salt__unspecified"
                )
            )
        ).scalar_one_or_none()
        if salt is None:
            salt = CanonicalIngredient(
                name="Salt",
                normalized_name="salt (api test)",
                variant_id="salt__unspecified",
                family_id="salt",
                is_unspecified_variant=True,
                category="pantry",
                standard_unit="g",
            )
            db.add(salt)
            await db.flush()

        item = CanonicalItem(
            household_id=household_id,
            slug=f"table-salt-{household_id}",
            name="Table Salt",
            category="pantry",
            default_base_unit="g",
        )
        db.add(item)
        await db.flush()
        db.add(
            IngredientMapping(
                household_id=household_id,
                canonical_item_id=item.id,
                canonical_ingredient_id=salt.id,
                mapping_source="user_confirmed",
                matching_confidence=Decimal("1.000"),
                user_confirmed=True,
            )
        )
        db.add(
            InventoryLot(
                household_id=household_id,
                canonical_item_id=item.id,
                initial_qty=Decimal("500"),
                remaining_qty=Decimal("500"),
                base_unit="g",
                lot_status="active",
                purchased_at=datetime.now(UTC),
            )
        )

        d_ready = Dish(
            slug="api-ready-salt-dish",
            canonical_name="Ready Salt Dish",
            diet_tags=["vegetarian"],
            is_active=True,
        )
        d_almost = Dish(
            slug="api-almost-dish",
            canonical_name="Almost Dish",
            diet_tags=["vegetarian"],
            is_active=True,
        )
        d_explore = Dish(
            slug="api-explore-dish",
            canonical_name="Explore Dish",
            diet_tags=["vegetarian"],
            is_active=True,
        )
        d_hidden = Dish(
            slug="api-hidden-dish",
            canonical_name="Hidden Dish",
            diet_tags=["vegetarian"],
            is_active=True,
        )
        db.add_all([d_ready, d_almost, d_explore, d_hidden])
        await db.flush()

        db.add(
            DishIngredient(
                dish_id=d_ready.id,
                ingredient_name="Salt",
                requirement_mode="generic_ingredient",
                requirement_type="required",
                family_id="salt",
            )
        )
        db.add(
            DishIngredient(
                dish_id=d_almost.id,
                ingredient_name="Spinach",
                requirement_mode="generic_ingredient",
                requirement_type="required",
                family_id="spinach",
            )
        )
        for idx, fam in enumerate(("spinach", "garlic", "ginger", "paneer")):
            db.add(
                DishIngredient(
                    dish_id=d_explore.id,
                    ingredient_name=fam.title(),
                    requirement_mode="generic_ingredient",
                    requirement_type="required",
                    family_id=fam,
                    sort_order=idx,
                )
            )
        db.add(
            DishIngredient(
                dish_id=d_hidden.id,
                ingredient_name="Salt",
                requirement_mode="generic_ingredient",
                requirement_type="required",
                family_id="salt",
            )
        )
        db.add(DishFeedback(household_id=household_id, dish_id=d_hidden.id, action="hide"))
        await db.commit()
        return d_ready, d_almost, d_hidden


@pytest.mark.asyncio
async def test_discover_dishes_unauthenticated_rejected(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/discover/dishes")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_discover_dishes_invalid_and_unauthorized_household_context(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    token_a = jwks_provider.make_token(sub="user_disc_a", email="da@example.com", name="A")
    token_b = jwks_provider.make_token(sub="user_disc_b", email="db@example.com", name="B")

    me_a = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token_a}"}
    )
    me_b = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token_b}"}
    )
    assert me_a.status_code == 200 and me_b.status_code == 200
    hh_a = me_a.json()["active_household"]["id"]
    hh_b = me_b.json()["active_household"]["id"]

    # Invalid X-Household-Id UUID -> 400
    bad_hdr = await client.get(
        "/api/v1/discover/dishes",
        headers={"Authorization": f"Bearer {token_a}", "X-Household-Id": "not-a-uuid"},
    )
    assert bad_hdr.status_code == 400

    # Household A user requesting Household B's X-Household-Id -> 403
    cross_hh = await client.get(
        "/api/v1/discover/dishes",
        headers={"Authorization": f"Bearer {token_a}", "X-Household-Id": hh_b},
    )
    assert cross_hh.status_code == 403

    # Unknown household UUID -> 403
    unknown_hh = await client.get(
        "/api/v1/discover/dishes",
        headers={"Authorization": f"Bearer {token_a}", "X-Household-Id": str(uuid4())},
    )
    assert unknown_hh.status_code == 403

    # Passing an arbitrary household_id query param never overrides authenticated context
    own = await client.get(
        f"/api/v1/discover/dishes?household_id={hh_b}",
        headers={"Authorization": f"Bearer {token_a}", "X-Household-Id": hh_a},
    )
    assert own.status_code == 200


@pytest.mark.asyncio
async def test_discover_dishes_buckets_hidden_isolation_and_zero_mutations(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    token_a = jwks_provider.make_token(sub="user_api_a", email="aa@example.com", name="AA")
    token_b = jwks_provider.make_token(sub="user_api_b", email="bb@example.com", name="BB")
    hdr_a = {"Authorization": f"Bearer {token_a}"}
    hdr_b = {"Authorization": f"Bearer {token_b}"}

    hh_a = (await client.get("/api/v1/auth/me", headers=hdr_a)).json()["active_household"]["id"]
    hh_b = (await client.get("/api/v1/auth/me", headers=hdr_b)).json()["active_household"]["id"]
    hdr_a["X-Household-Id"] = hh_a
    hdr_b["X-Household-Id"] = hh_b

    app = client._transport.app  # type: ignore[attr-defined]
    d_ready, d_almost, d_hidden = await _seed_dish_and_pantry(app, household_id=UUID(hh_a))

    session_maker = app.state.session_maker
    async with session_maker() as db:
        lots_before = (
            await db.execute(select(func.count()).select_from(InventoryLot))
        ).scalar_one()
        ledger_before = (
            await db.execute(select(func.count()).select_from(StockLedger))
        ).scalar_one()

    # Household A: default (include_hidden=False)
    res_a = await client.get("/api/v1/discover/dishes", headers=hdr_a)
    assert res_a.status_code == 200
    body_a = res_a.json()
    assert set(body_a.keys()) == {"can_make_now", "almost_there", "explore_more", "counts"}
    assert [d["slug"] for d in body_a["can_make_now"]] == [d_ready.slug]
    assert [d["slug"] for d in body_a["almost_there"]] == [d_almost.slug]
    assert [d["slug"] for d in body_a["explore_more"]] == ["api-explore-dish"]
    assert body_a["counts"] == {"can_make_now": 1, "almost_there": 1, "explore_more": 1}

    # Household A: include_hidden=true includes d_hidden in can_make_now
    res_a_hidden = await client.get(
        "/api/v1/discover/dishes?include_hidden=true", headers=hdr_a
    )
    assert res_a_hidden.status_code == 200
    can_slugs_hidden = {d["slug"] for d in res_a_hidden.json()["can_make_now"]}
    assert can_slugs_hidden == {d_ready.slug, d_hidden.slug}

    # Household B: empty pantry -> no can_make_now, and sees d_hidden (hide was A-only)
    res_b = await client.get("/api/v1/discover/dishes", headers=hdr_b)
    assert res_b.status_code == 200
    body_b = res_b.json()
    assert body_b["can_make_now"] == []
    assert body_b["counts"]["can_make_now"] == 0
    assert {d["slug"] for d in body_b["almost_there"]} == {
        d_ready.slug,
        d_almost.slug,
        d_hidden.slug,
    }

    # Verify zero inventory or stock ledger mutations
    async with session_maker() as db:
        lots_after = (
            await db.execute(select(func.count()).select_from(InventoryLot))
        ).scalar_one()
        ledger_after = (
            await db.execute(select(func.count()).select_from(StockLedger))
        ).scalar_one()
        assert lots_after == lots_before
        assert ledger_after == ledger_before
