import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.errors import AppError
from app.modules.auth.models import Household, User
from app.modules.catalog.ingredient_mapping_service import (
    backfill_canonical_item_mappings,
    confirm_ingredient_mapping,
    resolve_or_suggest_mapping,
)
from app.modules.inventory.models import CanonicalIngredient, CanonicalItem
from tests.conftest import EphemeralJWKSProvider


async def test_brand_agnostic_mapping_and_phase5_identity_distinctions(
    temp_db_url: str,
) -> None:
    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        owner = User(clerk_user_id="clerk_p2_1", email="p2@example.com", display_name="Phase2 User")
        db.add(owner)
        await db.flush()
        hh = Household(name="Phase2 HH", created_by=owner.id)
        db.add(hh)
        await db.flush()

        # Brands + pack sizes -> same Milk ingredient, distinct CanonicalItems & variants
        aavin_fc = CanonicalItem(
            household_id=hh.id,
            slug="aavin-full-cream-milk-500ml",
            name="Aavin Full Cream Milk 500ml",
            category="dairy",
            default_base_unit="ml",
        )
        amul_gold = CanonicalItem(
            household_id=hh.id,
            slug="amul-gold-fc-milk-1l",
            name="Amul Gold Full Cream Milk 1L",
            category="dairy",
            default_base_unit="ml",
        )
        amul_toned = CanonicalItem(
            household_id=hh.id,
            slug="amul-toned-milk-1l",
            name="Amul Toned Milk 1L",
            category="dairy",
            default_base_unit="ml",
        )
        basmati = CanonicalItem(
            household_id=hh.id,
            slug="india-gate-basmati-rice-5kg",
            name="India Gate Basmati Rice 5kg",
            category="grain",
            default_base_unit="g",
        )
        idli_rice = CanonicalItem(
            household_id=hh.id,
            slug="idli-rice-5kg",
            name="Idli Rice 5kg",
            category="grain",
            default_base_unit="g",
        )
        sunflower = CanonicalItem(
            household_id=hh.id,
            slug="fortune-sunflower-oil-1l",
            name="Fortune Sunlite Sunflower Oil 1L",
            category="oil",
            default_base_unit="ml",
        )
        mustard = CanonicalItem(
            household_id=hh.id,
            slug="dhara-mustard-oil-1l",
            name="Dhara Kachi Ghani Mustard Oil 1L",
            category="oil",
            default_base_unit="ml",
        )
        rice_flour = CanonicalItem(
            household_id=hh.id,
            slug="rice-flour-500g",
            name="Rice Flour 500g",
            category="grain",
            default_base_unit="g",
        )
        coconut_milk = CanonicalItem(
            household_id=hh.id,
            slug="coconut-milk-400ml",
            name="Coconut Milk 400ml",
            category="dairy",
            default_base_unit="ml",
        )
        unknown_item = CanonicalItem(
            household_id=hh.id,
            slug="mystery-artisan-mix",
            name="Mystery Artisan Mix 250g",
            category="general",
            default_base_unit="g",
        )
        db.add_all(
            [
                aavin_fc,
                amul_gold,
                amul_toned,
                basmati,
                idli_rice,
                sunflower,
                mustard,
                rice_flour,
                coconut_milk,
                unknown_item,
            ]
        )
        await db.flush()

        res_aavin = await resolve_or_suggest_mapping(
            db, household_id=hh.id, canonical_item=aavin_fc
        )
        res_amul_gold = await resolve_or_suggest_mapping(
            db, household_id=hh.id, canonical_item=amul_gold
        )
        res_toned = await resolve_or_suggest_mapping(
            db, household_id=hh.id, canonical_item=amul_toned
        )
        assert res_aavin.suggested_ingredient is not None
        assert res_amul_gold.suggested_ingredient is not None
        assert res_aavin.suggested_ingredient.normalized_name == "full_cream_milk"
        assert res_amul_gold.suggested_ingredient.normalized_name == "full_cream_milk"
        assert res_aavin.suggested_ingredient.id == res_amul_gold.suggested_ingredient.id
        assert res_toned.suggested_ingredient is not None
        assert res_toned.suggested_ingredient.normalized_name == "toned_milk"
        assert res_aavin.suggested_ingredient.id != res_toned.suggested_ingredient.id

        res_basmati = await resolve_or_suggest_mapping(
            db, household_id=hh.id, canonical_item=basmati
        )
        res_idli = await resolve_or_suggest_mapping(
            db, household_id=hh.id, canonical_item=idli_rice
        )
        assert res_basmati.suggested_ingredient is not None
        assert res_idli.suggested_ingredient is not None
        assert res_basmati.suggested_ingredient.normalized_name == "basmati_rice"
        assert res_idli.suggested_ingredient.normalized_name == "idli_rice"
        assert res_basmati.suggested_ingredient.id != res_idli.suggested_ingredient.id

        res_sun = await resolve_or_suggest_mapping(db, household_id=hh.id, canonical_item=sunflower)
        res_mus = await resolve_or_suggest_mapping(db, household_id=hh.id, canonical_item=mustard)
        assert res_sun.suggested_ingredient.normalized_name == "sunflower_oil"
        assert res_mus.suggested_ingredient.normalized_name == "mustard_oil"

        # Rice Flour maps to Rice Flour (never Basmati Rice); Coconut Milk maps to Coconut Milk
        # (never dairy Milk)
        res_rf = await resolve_or_suggest_mapping(db, household_id=hh.id, canonical_item=rice_flour)
        res_cm = await resolve_or_suggest_mapping(
            db, household_id=hh.id, canonical_item=coconut_milk
        )
        res_unk = await resolve_or_suggest_mapping(
            db, household_id=hh.id, canonical_item=unknown_item
        )
        assert res_rf.suggested_ingredient is not None
        assert res_rf.suggested_ingredient.normalized_name == "rice_flour"
        assert res_rf.suggested_ingredient.normalized_name != "basmati_rice"
        assert res_rf.suggested_ingredient.id != res_basmati.suggested_ingredient.id

        assert res_cm.suggested_ingredient is not None
        assert res_cm.suggested_ingredient.normalized_name == "coconut_milk"
        assert res_cm.suggested_ingredient.normalized_name != "milk"
        assert res_cm.suggested_ingredient.id != res_aavin.suggested_ingredient.id

        assert res_unk.status == "unmapped"

    await engine.dispose()


async def test_conflict_rejection_household_isolation_and_idempotent_backfill(
    temp_db_url: str,
) -> None:
    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        u1 = User(clerk_user_id="clerk_u1", email="u1@example.com", display_name="U1")
        u2 = User(clerk_user_id="clerk_u2", email="u2@example.com", display_name="U2")
        db.add_all([u1, u2])
        await db.flush()
        hh1 = Household(name="HH1", created_by=u1.id)
        hh2 = Household(name="HH2", created_by=u2.id)
        db.add_all([hh1, hh2])
        await db.flush()

        milk = (
            await db.execute(
                select(CanonicalIngredient).where(CanonicalIngredient.normalized_name == "milk")
            )
        ).scalar_one()
        curd = (
            await db.execute(
                select(CanonicalIngredient).where(CanonicalIngredient.normalized_name == "curd")
            )
        ).scalar_one()

        item_hh1 = CanonicalItem(
            household_id=hh1.id,
            slug="local-dairy-pack",
            name="Local Dairy Special 500ml",
            category="dairy",
            default_base_unit="ml",
        )
        item_sun = CanonicalItem(
            household_id=hh1.id,
            slug="sri-lakshmi-sunflower-oil-1l",
            name="Sri Lakshmi Sunflower Oil 1L",
            category="oil",
            default_base_unit="ml",
        )
        db.add_all([item_hh1, item_sun])
        await db.commit()

        # User confirms item_hh1 -> milk
        m1 = await confirm_ingredient_mapping(
            db,
            household_id=hh1.id,
            canonical_item_id=item_hh1.id,
            canonical_ingredient_id=milk.id,
        )
        assert m1.user_confirmed is True
        await db.commit()

        # Conflict rejected without allow_override=True
        with pytest.raises(AppError) as exc_info:
            await confirm_ingredient_mapping(
                db,
                household_id=hh1.id,
                canonical_item_id=item_hh1.id,
                canonical_ingredient_id=curd.id,
            )
        assert exc_info.value.code == "MAPPING_CONFLICT"

        # Household isolation: hh2 cannot confirm hh1's private item
        with pytest.raises(AppError) as exc_forbidden:
            await confirm_ingredient_mapping(
                db,
                household_id=hh2.id,
                canonical_item_id=item_hh1.id,
                canonical_ingredient_id=milk.id,
            )
        assert exc_forbidden.value.code == "FORBIDDEN"

        # Backfill dry_run=True writes nothing; dry_run=False is idempotent
        dry = await backfill_canonical_item_mappings(db, dry_run=True)
        assert dry.reused_count == 1
        assert dry.auto_mapped_count == 1  # item_sun

        r1 = await backfill_canonical_item_mappings(db, dry_run=False)
        await db.commit()
        assert r1.auto_mapped_count == 1

        r2 = await backfill_canonical_item_mappings(db, dry_run=False)
        await db.commit()
        assert r2.auto_mapped_count == 0
        assert r2.reused_count == 2

    await engine.dispose()


async def test_receipt_confirmation_integration_and_price_history_separation(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    """Demonstrates Aavin Full Cream Milk & Amul Gold Milk mapping to Milk
    while keeping separate CanonicalItems, inventory lots, and price histories.
    """
    token = jwks_provider.make_token(sub="user_p2_api", email="p2api@example.com", name="P2API")
    headers = {"Authorization": f"Bearer {token}"}

    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200

    # Purchase 1: Aavin Full Cream Milk 500ml at Rs 32.00
    p1 = await client.post(
        "/api/v1/purchases",
        headers=headers,
        json={
            "merchant_name": "Aavin Booth",
            "purchased_on": "2026-11-19",
            "items": [
                {
                    "display_name": "Aavin Full Cream Milk 500ml",
                    "qty": 500,
                    "unit": "ml",
                    "unit_price_paise": 3200,
                    "line_total_paise": 3200,
                    "category": "dairy",
                }
            ],
        },
    )
    assert p1.status_code == 201
    c1 = await client.post(f"/api/v1/purchases/{p1.json()['id']}/confirm", headers=headers, json={})
    assert c1.status_code == 200
    c1_body = c1.json()
    assert len(c1_body["created_lots"]) == 1
    assert len(c1_body["ingredient_mappings"]) == 1
    assert c1_body["ingredient_mappings"][0]["canonical_ingredient_name"] == "Full Cream Milk"
    assert c1_body["ingredient_mappings"][0]["status"] == "auto_mapped"
    aavin_item_id = c1_body["created_lots"][0]["canonical_item_id"]
    milk_ing_id = c1_body["ingredient_mappings"][0]["canonical_ingredient_id"]

    # Purchase 2: Amul Gold Milk 1L at Rs 68.00 (user confirms mapping explicitly)
    p2 = await client.post(
        "/api/v1/purchases",
        headers=headers,
        json={
            "merchant_name": "Supermarket",
            "purchased_on": "2026-11-20",
            "items": [
                {
                    "display_name": "Amul Gold Full Cream Milk 1L",
                    "qty": 1,
                    "unit": "l",
                    "unit_price_paise": 6800,
                    "line_total_paise": 6800,
                    "category": "dairy",
                }
            ],
        },
    )
    assert p2.status_code == 201
    c2 = await client.post(f"/api/v1/purchases/{p2.json()['id']}/confirm", headers=headers, json={})
    assert c2.status_code == 200
    c2_body = c2.json()
    assert len(c2_body["created_lots"]) == 1
    amul_item_id = c2_body["created_lots"][0]["canonical_item_id"]
    assert amul_item_id != aavin_item_id
    assert c2_body["ingredient_mappings"][0]["canonical_ingredient_id"] == milk_ing_id

    # Purchase 3: Repeat Aavin Full Cream Milk -> existing mapping reused
    p3 = await client.post(
        "/api/v1/purchases",
        headers=headers,
        json={
            "merchant_name": "Aavin Booth",
            "purchased_on": "2026-11-20",
            "items": [
                {
                    "display_name": "Aavin Full Cream Milk 500ml",
                    "qty": 500,
                    "unit": "ml",
                    "unit_price_paise": 3400,
                    "line_total_paise": 3400,
                    "category": "dairy",
                }
            ],
        },
    )
    assert p3.status_code == 201
    c3 = await client.post(
        f"/api/v1/purchases/{p3.json()['id']}/confirm",
        headers=headers,
        json={
            "ingredient_mappings": [
                {
                    "canonical_item_id": aavin_item_id,
                    "canonical_ingredient_id": milk_ing_id,
                }
            ]
        },
    )
    assert c3.status_code == 200
    assert c3.json()["ingredient_mappings"][0]["user_confirmed"] is True

    # Existing product-level inventory and price history remain separate and intact
    inv = await client.get("/api/v1/inventory/summary", headers=headers)
    assert inv.status_code == 200
    assert len(inv.json()["items"]) == 2
