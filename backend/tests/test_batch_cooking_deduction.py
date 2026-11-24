"""Regression tests for batched FEFO cooking deduction and atomic rollback."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.db import Base
from app.core.errors import AppError
from app.modules.auth.models import Household, HouseholdMember, User
from app.modules.cooking.models import CookingPlan
from app.modules.cooking.schemas import CompleteCookingPlanRequest, CompleteIngredientInput
from app.modules.cooking.service import complete_cooking_plan
from app.modules.inventory.models import CanonicalItem, InventoryLot, StockLedger
from app.modules.inventory.service import BatchFEFORequest, batch_allocate_fefo


@pytest.mark.asyncio
async def test_complete_cooking_plan_batches_fefo_queries_and_preserves_order(tmp_path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'batch_fefo.db'}"
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sm = async_sessionmaker(engine, expire_on_commit=False)

    async with sm() as db:
        user = User(clerk_user_id="clk_batch_1", display_name="Batch Tester")
        db.add(user)
        await db.flush()
        household = Household(name="Batch Home", created_by=user.id)
        db.add(household)
        await db.flush()
        db.add(HouseholdMember(household_id=household.id, user_id=user.id, role="owner"))

        items: list[CanonicalItem] = []
        early_lots: list[InventoryLot] = []
        late_lots: list[InventoryLot] = []
        plan_ingredients = []

        for idx in range(5):
            item = CanonicalItem(
                household_id=household.id,
                slug=f"batch-item-{idx}",
                name=f"Batch Item {idx}",
                category="produce",
                default_base_unit="g",
            )
            db.add(item)
            await db.flush()
            items.append(item)

            early = InventoryLot(
                household_id=household.id,
                canonical_item_id=item.id,
                initial_qty=Decimal("80.000"),
                remaining_qty=Decimal("80.000"),
                base_unit="g",
                expires_on=date.today() + timedelta(days=2),
            )
            late = InventoryLot(
                household_id=household.id,
                canonical_item_id=item.id,
                initial_qty=Decimal("200.000"),
                remaining_qty=Decimal("200.000"),
                base_unit="g",
                expires_on=date.today() + timedelta(days=10),
            )
            db.add_all([early, late])
            await db.flush()
            early_lots.append(early)
            late_lots.append(late)

            plan_ingredients.append(
                {
                    "line_id": f"line-{idx}",
                    "canonical_item_id": str(item.id),
                    "name": item.name,
                    "planned_qty": "100.000",
                    "base_unit": "g",
                }
            )

        plan = CookingPlan(
            household_id=household.id,
            created_by=user.id,
            recipe_title="Batch Test Meal",
            planned_servings=2,
            status="planned",
            ingredients=plan_ingredients,
        )
        db.add(plan)
        await db.commit()

        lot_selects: list[str] = []

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def _track_sql(conn, cursor, statement, parameters, context, executemany):
            normalized = " ".join(statement.strip().split()).upper()
            if normalized.startswith("SELECT") and "FROM INVENTORY_LOTS" in normalized:
                lot_selects.append(statement)

        req = CompleteCookingPlanRequest(
            items=[
                CompleteIngredientInput(
                    line_id=f"line-{idx}",
                    canonical_item_id=items[idx].id,
                    actual_used_qty=Decimal("100.000"),
                )
                for idx in range(5)
            ]
        )

        result = await complete_cooking_plan(
            db,
            household_id=household.id,
            user_id=user.id,
            plan_id=plan.id,
            payload=req,
        )
        event.remove(engine.sync_engine, "before_cursor_execute", _track_sql)

        assert result.status == "completed"
        assert len(lot_selects) == 1

        for early, late in zip(early_lots, late_lots, strict=True):
            await db.refresh(early)
            await db.refresh(late)
            assert early.remaining_qty == Decimal("0.000")
            assert early.lot_status == "depleted"
            assert late.remaining_qty == Decimal("180.000")
            assert late.lot_status == "active"

        ledger_rows = (
            await db.execute(
                select(StockLedger).where(
                    StockLedger.household_id == household.id,
                    StockLedger.reference_id == plan.id,
                )
            )
        ).scalars().all()
        assert len(ledger_rows) == 10
        assert sum(row.quantity_delta for row in ledger_rows) == Decimal("-500.000")

    await engine.dispose()


@pytest.mark.asyncio
async def test_batch_allocate_fefo_same_item_twice_and_atomic_insufficient_stock(tmp_path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'batch_edge.db'}"
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sm = async_sessionmaker(engine, expire_on_commit=False)

    async with sm() as db:
        user = User(clerk_user_id="clk_batch_2", display_name="Batch Tester 2")
        db.add(user)
        await db.flush()
        household = Household(name="Batch Home 2", created_by=user.id)
        db.add(household)
        await db.flush()

        item_a = CanonicalItem(
            household_id=household.id,
            slug="tomato-batch",
            name="Tomato",
            category="produce",
            default_base_unit="g",
        )
        item_b = CanonicalItem(
            household_id=household.id,
            slug="onion-batch",
            name="Onion",
            category="produce",
            default_base_unit="g",
        )
        db.add_all([item_a, item_b])
        await db.flush()

        lot_a = InventoryLot(
            household_id=household.id,
            canonical_item_id=item_a.id,
            initial_qty=Decimal("100.000"),
            remaining_qty=Decimal("100.000"),
            base_unit="g",
        )
        lot_b = InventoryLot(
            household_id=household.id,
            canonical_item_id=item_b.id,
            initial_qty=Decimal("20.000"),
            remaining_qty=Decimal("20.000"),
            base_unit="g",
        )
        db.add_all([lot_a, lot_b])
        await db.commit()

        # Two requests for the same canonical_item_id must not double-deduct or go negative
        results = await batch_allocate_fefo(
            db,
            household_id=household.id,
            requests=[
                BatchFEFORequest(canonical_item_id=item_a.id, required_qty=Decimal("60.000")),
                BatchFEFORequest(canonical_item_id=item_a.id, required_qty=Decimal("40.000")),
            ],
            actor_user_id=user.id,
        )
        await db.commit()
        assert len(results) == 2
        await db.refresh(lot_a)
        assert lot_a.remaining_qty == Decimal("0.000")
        assert lot_a.lot_status == "depleted"

        # Insufficient stock on second item must raise 409 and rollback cleanly
        with pytest.raises(AppError) as exc_info:
            await batch_allocate_fefo(
                db,
                household_id=household.id,
                requests=[
                    BatchFEFORequest(canonical_item_id=item_b.id, required_qty=Decimal("10.000")),
                    BatchFEFORequest(canonical_item_id=item_b.id, required_qty=Decimal("15.000")),
                ],
                actor_user_id=user.id,
            )
        assert exc_info.value.code == "INSUFFICIENT_STOCK"
        await db.rollback()
        await db.refresh(lot_b)
        assert lot_b.remaining_qty == Decimal("20.000")
        assert lot_b.lot_status == "active"

    await engine.dispose()


@pytest.mark.asyncio
async def test_duplicate_completion_rejected_and_cook_plus_waste_accumulated(tmp_path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'batch_dup.db'}"
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sm = async_sessionmaker(engine, expire_on_commit=False)

    async with sm() as db:
        user = User(clerk_user_id="clk_batch_3", display_name="Batch Tester 3")
        db.add(user)
        await db.flush()
        household = Household(name="Batch Home 3", created_by=user.id)
        db.add(household)
        await db.flush()

        item = CanonicalItem(
            household_id=household.id,
            slug="potato-dup",
            name="Potato",
            category="produce",
            default_base_unit="g",
        )
        db.add(item)
        await db.flush()

        lot = InventoryLot(
            household_id=household.id,
            canonical_item_id=item.id,
            initial_qty=Decimal("200.000"),
            remaining_qty=Decimal("200.000"),
            base_unit="g",
        )
        db.add(lot)
        await db.flush()

        plan = CookingPlan(
            household_id=household.id,
            created_by=user.id,
            recipe_title="Aloo Fry",
            planned_servings=2,
            status="planned",
            ingredients=[
                {
                    "line_id": "line-0",
                    "canonical_item_id": str(item.id),
                    "name": item.name,
                    "planned_qty": "100.000",
                    "base_unit": "g",
                }
            ],
        )
        db.add(plan)
        await db.commit()

        req = CompleteCookingPlanRequest(
            items=[
                CompleteIngredientInput(
                    line_id="line-0",
                    canonical_item_id=item.id,
                    actual_qty=Decimal("100.000"),
                    waste_qty=Decimal("25.000"),
                )
            ]
        )
        out = await complete_cooking_plan(
            db,
            household_id=household.id,
            user_id=user.id,
            plan_id=plan.id,
            payload=req,
        )
        assert out.status == "completed"
        await db.refresh(lot)
        assert lot.remaining_qty == Decimal("75.000")

        # Duplicate completion must be rejected and must not deduct again
        with pytest.raises(AppError) as exc_info:
            await complete_cooking_plan(
                db,
                household_id=household.id,
                user_id=user.id,
                plan_id=plan.id,
                payload=req,
            )
        assert exc_info.value.code == "INVALID_STATE_TRANSITION"
        await db.refresh(lot)
        assert lot.remaining_qty == Decimal("75.000")

        ledger_rows = (
            await db.execute(
                select(StockLedger).where(
                    StockLedger.household_id == household.id,
                    StockLedger.reference_id == plan.id,
                )
            )
        ).scalars().all()
        assert len(ledger_rows) == 2
        assert {row.event_type for row in ledger_rows} == {"COOK_OUT", "WASTE_OUT"}
        assert sum(row.quantity_delta for row in ledger_rows) == Decimal("-125.000")


@pytest.mark.asyncio
async def test_unit_mismatch_never_deducts_incompatible_lot_and_session_confirm_preserves_atomicity(
    tmp_path,
) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'unit_mismatch.db'}"
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with session_maker() as db:
        user = User(clerk_user_id="clk_unit_test", display_name="Chef")
        db.add(user)
        await db.flush()
        household = Household(name="Unit Home", created_by=user.id)
        db.add(household)
        await db.flush()
        db.add(HouseholdMember(household_id=household.id, user_id=user.id, role="owner"))

        banana = CanonicalItem(
            slug="organic-bananas",
            name="Organic Bananas",
            category="produce",
            default_base_unit="kg",
        )
        oats = CanonicalItem(
            slug="rolled-oats",
            name="Rolled Oats",
            category="pantry",
            default_base_unit="g",
        )
        db.add_all([banana, oats])
        await db.flush()

        # Pantry has bananas ONLY in kg (1.2 kg), oats in kg (1.0 kg)
        lot_banana_kg = InventoryLot(
            household_id=household.id,
            canonical_item_id=banana.id,
            remaining_qty=Decimal("1.200"),
            initial_qty=Decimal("1.200"),
            base_unit="kg",
            lot_status="active",
        )
        lot_oats_kg = InventoryLot(
            household_id=household.id,
            canonical_item_id=oats.id,
            remaining_qty=Decimal("1.000"),
            initial_qty=Decimal("1.000"),
            base_unit="kg",
            lot_status="active",
        )
        db.add_all([lot_banana_kg, lot_oats_kg])
        await db.flush()

        plan = CookingPlan(
            household_id=household.id,
            created_by=user.id,
            recipe_title="Banana Oats",
            status="planned",
            planned_servings=2,
            ingredients=[
                {
                    "line_id": "line-oats",
                    "canonical_item_id": str(oats.id),
                    "name": "Rolled Oats",
                    "planned_qty": "100.000",
                    "base_unit": "g",
                },
                {
                    "line_id": "line-banana",
                    "canonical_item_id": str(banana.id),
                    "name": "Organic Bananas",
                    "planned_qty": "2.000",
                    "base_unit": "pcs",
                },
            ],
        )
        db.add(plan)
        await db.commit()
        hid, uid, pid, banana_id, oats_id = household.id, user.id, plan.id, banana.id, oats.id

        # 1. Attempting to deduct 2 pcs bananas against 1.2 kg lot MUST fail & roll back oats too
        with pytest.raises(AppError) as err_info:
            await complete_cooking_plan(
                db,
                household_id=hid,
                user_id=uid,
                plan_id=pid,
                payload=CompleteCookingPlanRequest(
                    items=[
                        CompleteIngredientInput(
                            line_id="line-oats",
                            canonical_item_id=oats_id,
                            actual_qty=Decimal("100.000"),
                        ),
                        CompleteIngredientInput(
                            line_id="line-banana",
                            canonical_item_id=banana_id,
                            actual_qty=Decimal("2.000"),
                        ),
                    ]
                ),
            )
        assert err_info.value.code == "INSUFFICIENT_STOCK"
        await db.rollback()
        await db.refresh(lot_banana_kg)
        await db.refresh(lot_oats_kg)
        assert lot_banana_kg.remaining_qty == Decimal("1.200")
        assert lot_oats_kg.remaining_qty == Decimal("1.000")

        # 2. Session-confirmed 2 pcs bananas + compatible 100 g oats (from 1.0 kg lot):
        #    - Deducts 0.100 kg from oats (compatible mass unit conversion)
        #    - Deducts 0 from banana 1.2 kg lot and records session_confirmed provenance
        out = await complete_cooking_plan(
            db,
            household_id=hid,
            user_id=uid,
            plan_id=pid,
            payload=CompleteCookingPlanRequest(
                items=[
                    CompleteIngredientInput(
                        line_id="line-oats",
                        canonical_item_id=oats_id,
                        actual_qty=Decimal("100.000"),
                    ),
                    CompleteIngredientInput(
                        line_id="line-banana",
                        canonical_item_id=banana_id,
                        actual_qty=Decimal("0.000"),
                        omitted=True,
                        session_confirmed=True,
                        confirmed_qty=Decimal("2.000"),
                        confirmed_unit="pcs",
                    ),
                ]
            ),
        )
        assert out.status == "completed"
        await db.refresh(lot_banana_kg)
        await db.refresh(lot_oats_kg)
        assert lot_banana_kg.remaining_qty == Decimal("1.200")
        assert lot_oats_kg.remaining_qty == Decimal("0.900")
        banana_ing = next(i for i in out.ingredients if i["line_id"] == "line-banana")
        assert banana_ing["session_confirmed"] is True
        assert banana_ing["confirmed_qty"] == "2.000"
        assert banana_ing["confirmed_unit"] == "pcs"
        assert banana_ing["total_deducted_qty"] == "0"

    await engine.dispose()

    await engine.dispose()
