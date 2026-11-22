import asyncio
import json
import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.modules.auth.models import Household, User
from app.modules.catalog.ingredient_seed import seed_canonical_ingredients
from app.modules.inventory.models import (
    CanonicalIngredient,
    CanonicalItem,
    IngredientMapping,
    InventoryLot,
    ItemAlias,
    StockLedger,
)
from app.modules.purchases.models import Purchase, PurchaseItem
from tests.conftest import EphemeralJWKSProvider

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _make_sqlite_engine(db_url: str):
    engine = create_async_engine(db_url, connect_args={"check_same_thread": False})

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fk(dbapi_conn, _conn_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


async def test_create_ingredient_and_constraints(temp_db_url: str) -> None:
    engine = _make_sqlite_engine(temp_db_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as session:
        ing = CanonicalIngredient(
            name="Extra Virgin Olive Oil",
            normalized_name="extra_virgin_olive_oil",
            category="oil",
            subcategory="cooking_oil",
            standard_unit="ml",
            cooking_variants=["cold_pressed", "first_press"],
            supported_units=["ml", "l"],
        )
        session.add(ing)
        await session.commit()
        await session.refresh(ing)

        assert ing.id is not None
        assert ing.created_at is not None
        assert ing.updated_at is not None
        assert ing.cooking_variants == ["cold_pressed", "first_press"]
        assert ing.supported_units == ["ml", "l"]

        # Duplicate normalized_name is rejected
        dup = CanonicalIngredient(
            name="EVOO",
            normalized_name="extra_virgin_olive_oil",
            category="oil",
            subcategory="cooking_oil",
            standard_unit="ml",
            cooking_variants=[],
            supported_units=["ml"],
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        # Invalid standard_unit is rejected
        bad_unit = CanonicalIngredient(
            name="Bad Unit Spice",
            normalized_name="bad_unit_spice",
            category="spice",
            subcategory="ground_spice",
            standard_unit="bushel",
            cooking_variants=[],
            supported_units=["bushel"],
        )
        session.add(bad_unit)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    await engine.dispose()


async def test_mapping_duplicate_prevention_household_isolation_and_fk_integrity(
    temp_db_url: str,
) -> None:
    engine = _make_sqlite_engine(temp_db_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as session:
        owner = User(
            clerk_user_id="clerk_owner_1",
            email="owner@example.com",
            display_name="Owner One",
        )
        session.add(owner)
        await session.flush()
        hh1 = Household(name="Household One", created_by=owner.id)
        hh2 = Household(name="Household Two", created_by=owner.id)
        session.add_all([hh1, hh2])

        milk = (
            await session.execute(
                select(CanonicalIngredient).where(CanonicalIngredient.normalized_name == "milk")
            )
        ).scalar_one()
        curd = (
            await session.execute(
                select(CanonicalIngredient).where(CanonicalIngredient.normalized_name == "curd")
            )
        ).scalar_one()

        amul_milk = CanonicalItem(
            household_id=None,
            slug="amul-gold-milk-1l",
            name="Amul Gold Milk 1L",
            category="dairy",
            default_base_unit="ml",
        )
        aavin_milk = CanonicalItem(
            household_id=hh1.id,
            slug="aavin-full-cream-500ml",
            name="Aavin Full Cream Milk 500ml",
            category="dairy",
            default_base_unit="ml",
        )
        session.add_all([amul_milk, aavin_milk])
        await session.commit()

        hh1_id = hh1.id
        hh2_id = hh2.id
        amul_id = amul_milk.id
        aavin_id = aavin_milk.id
        milk_id = milk.id
        curd_id = curd.id

        # 1. Global mapping succeeds
        global_map = IngredientMapping(
            household_id=None,
            canonical_item_id=amul_id,
            canonical_ingredient_id=milk_id,
            matching_confidence=Decimal("0.980"),
            mapping_source="catalogue",
            user_confirmed=True,
        )
        session.add(global_map)
        await session.commit()

        # Duplicate global mapping for same canonical_item_id is prevented
        dup_global = IngredientMapping(
            household_id=None,
            canonical_item_id=amul_id,
            canonical_ingredient_id=curd_id,
            matching_confidence=Decimal("0.800"),
            mapping_source="classifier_v2",
            user_confirmed=False,
        )
        session.add(dup_global)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        # 2. Household-scoped mappings: hh1 and hh2 can each have their own mapping
        hh1_map = IngredientMapping(
            household_id=hh1_id,
            canonical_item_id=amul_id,
            canonical_ingredient_id=milk_id,
            matching_confidence=Decimal("1.000"),
            mapping_source="user_confirmed",
            user_confirmed=True,
        )
        hh2_map = IngredientMapping(
            household_id=hh2_id,
            canonical_item_id=amul_id,
            canonical_ingredient_id=curd_id,
            matching_confidence=Decimal("0.950"),
            mapping_source="user_correction",
            user_confirmed=True,
        )
        session.add_all([hh1_map, hh2_map])
        await session.commit()

        # Duplicate household mapping for hh1 + amul_milk is prevented
        dup_hh1 = IngredientMapping(
            household_id=hh1_id,
            canonical_item_id=amul_id,
            canonical_ingredient_id=curd_id,
            matching_confidence=Decimal("0.500"),
            mapping_source="classifier_v2",
            user_confirmed=False,
        )
        session.add(dup_hh1)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        # Household isolation query
        hh1_rows = (
            (
                await session.execute(
                    select(IngredientMapping).where(IngredientMapping.household_id == hh1_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(hh1_rows) == 1
        assert hh1_rows[0].canonical_ingredient_id == milk_id

        # 3. FK integrity: nonexistent canonical_item_id fails
        bad_fk = IngredientMapping(
            household_id=hh1_id,
            canonical_item_id=uuid4(),
            canonical_ingredient_id=milk_id,
            matching_confidence=Decimal("1.000"),
            mapping_source="manual",
            user_confirmed=True,
        )
        session.add(bad_fk)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        # FK RESTRICT: deleting a CanonicalIngredient referenced by IngredientMapping is blocked
        milk_ref = await session.get(CanonicalIngredient, milk_id)
        assert milk_ref is not None
        await session.delete(milk_ref)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        # FK CASCADE: deleting a CanonicalItem cascades to its IngredientMapping
        session.add(
            IngredientMapping(
                household_id=hh1_id,
                canonical_item_id=aavin_id,
                canonical_ingredient_id=milk_id,
                matching_confidence=Decimal("0.950"),
                mapping_source="user_confirmed",
                user_confirmed=True,
            )
        )
        await session.commit()
        aavin_ref = await session.get(CanonicalItem, aavin_id)
        assert aavin_ref is not None
        await session.delete(aavin_ref)
        await session.commit()
        remaining_aavin = (
            (
                await session.execute(
                    select(IngredientMapping).where(IngredientMapping.canonical_item_id == aavin_id)
                )
            )
            .scalars()
            .all()
        )
        assert remaining_aavin == []

    await engine.dispose()


async def test_migration_preserves_existing_data_and_seeds_taxonomy(tmp_path: Path) -> None:
    db_file = tmp_path / "upgrade_0011_to_0012.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    alembic_cfg = Config(str(_ALEMBIC_INI))
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)

    # 1. Upgrade to pre-ingredient revision 0011
    await asyncio.to_thread(command.upgrade, alembic_cfg, "0011_classification_v2_fields")

    engine = _make_sqlite_engine(db_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    user_id = uuid4()
    hh_id = uuid4()
    item_id = uuid4()
    alias_id = uuid4()
    purchase_id = uuid4()
    purchase_item_id = uuid4()
    lot_id = uuid4()
    ledger_id = uuid4()

    async with Session() as session:
        session.add(
            User(
                id=user_id,
                clerk_user_id="clerk_mig_1",
                email="mig@example.com",
                display_name="Migration User",
            )
        )
        await session.flush()
        session.add(Household(id=hh_id, name="Migration Household", created_by=user_id))
        await session.flush()
        session.add(
            CanonicalItem(
                id=item_id,
                household_id=hh_id,
                slug="aavin-milk-500ml",
                name="Aavin Milk 500ml",
                category="dairy",
                default_base_unit="ml",
            )
        )
        session.add(
            ItemAlias(
                id=alias_id,
                household_id=hh_id,
                canonical_item_id=item_id,
                normalized_alias="aavin milk 500ml",
                source="user_confirmed",
            )
        )
        session.add(
            Purchase(
                id=purchase_id,
                household_id=hh_id,
                created_by=user_id,
                status="confirmed",
                source_type="manual",
                purchased_on=date(2026, 11, 20),
                total_amount_paise=3000,
            )
        )
        await session.flush()
        await session.execute(
            text(
                "INSERT INTO purchase_items "
                "(id, purchase_id, household_id, canonical_item_id, display_name, "
                "normalized_qty, base_unit, unit_price_paise, line_total_paise, created_at) "
                "VALUES (:id, :pid, :hh, :ci, :name, :qty, :unit, :up, :lt, :ts)"
            ),
            {
                "id": purchase_item_id.hex,
                "pid": purchase_id.hex,
                "hh": hh_id.hex,
                "ci": item_id.hex,
                "name": "Aavin Milk 500ml",
                "qty": 500.0,
                "unit": "ml",
                "up": 3000,
                "lt": 3000,
                "ts": datetime.now(UTC).isoformat(),
            },
        )
        session.add(
            InventoryLot(
                id=lot_id,
                household_id=hh_id,
                canonical_item_id=item_id,
                purchase_item_id=purchase_item_id,
                initial_qty=Decimal("500.000"),
                remaining_qty=Decimal("500.000"),
                base_unit="ml",
                unit_cost_paise=3000,
                purchased_at=datetime.now(UTC),
            )
        )
        await session.flush()
        session.add(
            StockLedger(
                id=ledger_id,
                household_id=hh_id,
                lot_id=lot_id,
                canonical_item_id=item_id,
                event_type="PURCHASE_IN",
                quantity_delta=Decimal("500.000"),
                base_unit="ml",
                balance_after_lot=Decimal("500.000"),
                unit_cost_paise=3000,
                actor_user_id=user_id,
            )
        )
        await session.commit()
    await engine.dispose()

    # 2. Upgrade to head (0012_ingredient_first_foundation)
    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")

    engine2 = _make_sqlite_engine(db_url)
    Session2 = async_sessionmaker(engine2, expire_on_commit=False)

    async with Session2() as session:
        # Existing records preserved with exact IDs
        ci = await session.get(CanonicalItem, item_id)
        assert ci is not None and ci.name == "Aavin Milk 500ml" and ci.household_id == hh_id
        al = await session.get(ItemAlias, alias_id)
        assert al is not None and al.canonical_item_id == item_id
        pi = await session.get(PurchaseItem, purchase_item_id)
        assert pi is not None and pi.canonical_item_id == item_id and pi.unit_price_paise == 3000
        lot = await session.get(InventoryLot, lot_id)
        assert lot is not None and lot.remaining_qty == Decimal("500.000")
        led = await session.get(StockLedger, ledger_id)
        assert led is not None and led.quantity_delta == Decimal("500.000")

        # Existing CanonicalItems are NOT automatically mapped
        auto_maps = (await session.execute(select(IngredientMapping))).scalars().all()
        assert auto_maps == []

        # Initial seed covers all 8 required categories
        seeded = (await session.execute(select(CanonicalIngredient))).scalars().all()
        seeded_categories = {row.category for row in seeded}
        assert {
            "dairy",
            "grain",
            "pulse",
            "vegetable",
            "fruit",
            "oil",
            "spice",
            "nut",
        } <= seeded_categories

        # Idempotent JSON seed supports future additions without code changes
        custom_seed = tmp_path / "custom_seed.json"
        custom_seed.write_text(
            json.dumps(
                [
                    {
                        "name": "Milk",
                        "normalized_name": "milk",
                        "category": "dairy",
                        "subcategory": "milk_and_dairy",
                        "standard_unit": "ml",
                        "cooking_variants": ["whole"],
                        "supported_units": ["ml", "l"],
                    },
                    {
                        "name": "Black Pepper",
                        "normalized_name": "black_pepper",
                        "category": "spice",
                        "subcategory": "whole_spice",
                        "standard_unit": "g",
                        "cooking_variants": ["whole", "cracked"],
                        "supported_units": ["g", "kg"],
                    },
                ]
            ),
            encoding="utf-8",
        )
        added = await seed_canonical_ingredients(session, seed_path=custom_seed)
        await session.commit()
        assert added == 1  # Only Black Pepper added; Milk not duplicated

    await engine2.dispose()

    # 3. Verify downgrade & re-upgrade cleanly
    await asyncio.to_thread(command.downgrade, alembic_cfg, "0011_classification_v2_fields")
    with sqlite3.connect(db_file) as conn:
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "canonical_ingredients" not in tables
        assert "ingredient_mappings" not in tables
    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")


async def test_existing_purchase_inventory_and_price_history_after_migration(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    token = jwks_provider.make_token(sub="user_p1_reg", email="p1@example.com", name="P1")
    headers = {"Authorization": f"Bearer {token}"}

    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200

    # Create and confirm a manual purchase
    create_resp = await client.post(
        "/api/v1/purchases",
        headers=headers,
        json={
            "merchant_name": "Local Kirana",
            "purchased_on": "2026-11-20",
            "items": [
                {
                    "display_name": "Amul Gold Milk 1L",
                    "qty": 1,
                    "unit": "l",
                    "unit_price_paise": 6800,
                    "line_total_paise": 6800,
                    "category": "dairy",
                }
            ],
        },
    )
    assert create_resp.status_code == 201
    purchase = create_resp.json()
    purchase_id = purchase["id"]

    confirm_resp = await client.post(
        f"/api/v1/purchases/{purchase_id}/confirm",
        headers=headers,
        json={},
    )
    assert confirm_resp.status_code == 200

    # Verify inventory summary works unchanged
    inv_resp = await client.get("/api/v1/inventory/summary", headers=headers)
    assert inv_resp.status_code == 200
    items = inv_resp.json()["items"]
    assert len(items) == 1
    assert Decimal(items[0]["total_remaining_qty"]) == Decimal("1000.000")
    assert items[0]["canonical_item_id"]

    # Verify price history & insights summary work unchanged
    insights_resp = await client.get("/api/v1/insights/summary", headers=headers)
    assert insights_resp.status_code == 200
    assert insights_resp.json()["total_spend_paise"] == 6800
