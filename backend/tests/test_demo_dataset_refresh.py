from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.db import Base
from app.modules.auth.models import Household, HouseholdMember, User
from app.modules.cooking.models import CookingPlan, MealHistory
from app.modules.demo.dataset_builder import (
    DEMO_CLERK_USER_ID,
    DEMO_HOUSEHOLD_ID,
    DEMO_USER_ID,
    build_demo_dataset,
    scan_dataset_for_pii,
)
from app.modules.demo.static_daily_plan import build_static_demo_daily_plan
from app.modules.discovery.daily_models import DailyMealPlan, DailyMealPlanRecipe
from app.modules.insights.models import HouseholdBudget
from app.modules.inventory.models import (
    CanonicalIngredient,
    CanonicalItem,
    InventoryLot,
    ItemAlias,
    StockLedger,
)
from app.modules.purchases.models import FoodConsumption, Purchase, PurchaseItem
from app.modules.recipes.quality_validator import validate_recipe_quality
from scripts.refresh_demo_dataset import execute_demo_refresh, run_demo_dry_run


async def _seed_temp_source_db(db_path: Path) -> tuple[UUID, UUID, UUID]:
    """Create a temporary SQLite DB with realistic household data + synthetic PII to sanitize."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    now = datetime(2026, 11, 25, 6, 0, tzinfo=UTC)
    async with sm() as db:
        ci_global = CanonicalIngredient(
            id=uuid4(),
            name="Moong Dal",
            normalized_name="moong dal",
            category="lentils",
            standard_unit="g",
            cooking_variants=[],
            supported_units=["g"],
            is_active=True,
            is_unspecified_variant=False,
            created_at=now,
            updated_at=now,
        )
        db.add(ci_global)

        real_user = User(
            id=uuid4(),
            clerk_user_id="user_2abcREALCLERK99999",
            email="private.owner@example.com",
            display_name="Real Owner Name",
            created_at=now,
            updated_at=now,
        )
        db.add(real_user)
        await db.flush()

        real_hh = Household(
            id=uuid4(),
            name="Real Private Home",
            currency_code="INR",
            created_by=real_user.id,
            created_at=now,
            updated_at=now,
        )
        db.add(real_hh)
        await db.flush()
        db.add(
            HouseholdMember(
                household_id=real_hh.id,
                user_id=real_user.id,
                role="owner",
                joined_at=now,
            )
        )

        # Self-referential canonical items
        c_parent = CanonicalItem(
            id=uuid4(),
            household_id=real_hh.id,
            slug="moong-dal",
            name="Moong Dal",
            category="lentils",
            food_state="raw",
            is_prepared_food=False,
            pantry_eligible=True,
            budget_treatment="pantry_inventory",
            language="en",
            verified=True,
            default_base_unit="g",
            low_stock_threshold_qty=Decimal("100.000"),
            is_active=True,
            created_at=now,
        )
        c_merged = CanonicalItem(
            id=uuid4(),
            household_id=real_hh.id,
            slug="yellow-moong",
            name="Yellow Moong",
            category="lentils",
            food_state="raw",
            is_prepared_food=False,
            pantry_eligible=True,
            budget_treatment="pantry_inventory",
            language="en",
            verified=True,
            default_base_unit="g",
            low_stock_threshold_qty=Decimal("0.000"),
            merged_into_id=c_parent.id,
            is_active=False,
            created_at=now,
        )
        db.add_all([c_parent, c_merged])
        await db.flush()

        db.add(
            ItemAlias(
                id=uuid4(),
                household_id=real_hh.id,
                canonical_item_id=c_parent.id,
                normalized_alias="moong dal 1kg",
                source="receipt",
                alias_kind="display",
                created_at=now,
            )
        )

        # Purchase packed with synthetic PII that MUST be stripped
        purchase = Purchase(
            id=uuid4(),
            household_id=real_hh.id,
            created_by=real_user.id,
            status="confirmed",
            source_type="receipt_ocr",
            document_type="grocery_receipt",
            ocr_adapter="apple_vision",
            merchant_name="Real Store 9876543210 MG Road",
            receipt_number="INV-998877",
            order_id="ORD-554433",
            purchased_on=date(2026, 11, 20),
            currency_code="INR",
            receipt_category="grocery",
            payment_method="UPI GPay",
            subtotal_paise=18000,
            discount_paise=0,
            additional_charges_paise=0,
            cgst_paise=0,
            sgst_paise=0,
            igst_paise=0,
            other_tax_paise=0,
            tax_paise=0,
            rounding_paise=0,
            total_amount_paise=18000,
            raw_ocr_payload={
                "raw_text": (
                    "Deliver to Flat 402, Sector 12, Call +91 9876543210, "
                    "private.owner@example.com, Paid via UPI"
                )
            },
            missing_fields_json=["gstin"],
            attachments_json=[{"path": "/Users/private/receipt.jpg"}],
            confirmed_at=now,
            created_at=now,
            updated_at=now,
        )
        # Also add a draft purchase that should be excluded from curated demo
        draft_purchase = Purchase(
            id=uuid4(),
            household_id=real_hh.id,
            created_by=real_user.id,
            status="draft",
            source_type="manual",
            document_type="grocery_receipt",
            merchant_name="Draft Secret Store",
            purchased_on=date(2026, 11, 21),
            currency_code="INR",
            receipt_category="grocery",
            discount_paise=0,
            additional_charges_paise=0,
            cgst_paise=0,
            sgst_paise=0,
            igst_paise=0,
            other_tax_paise=0,
            tax_paise=0,
            rounding_paise=0,
            total_amount_paise=5000,
            created_at=now,
            updated_at=now,
        )
        db.add_all([purchase, draft_purchase])
        await db.flush()

        p_item = PurchaseItem(
            id=uuid4(),
            purchase_id=purchase.id,
            household_id=real_hh.id,
            line_index=0,
            raw_text="MOONG DAL 1KG Call 9876543210 Flat 402",
            canonical_item_id=c_parent.id,
            display_name="Moong Dal 1kg",
            normalized_name="moong dal",
            category="lentils",
            match_confidence="high",
            normalized_qty=Decimal("1000.000"),
            base_unit="g",
            unit_price_paise=18000,
            line_total_paise=18000,
            discount_paise=0,
            tax_paise=0,
            track_inventory=True,
            created_at=now,
        )
        db.add(p_item)
        await db.flush()

        lot = InventoryLot(
            id=uuid4(),
            household_id=real_hh.id,
            canonical_item_id=c_parent.id,
            purchase_item_id=p_item.id,
            initial_qty=Decimal("1000.000"),
            remaining_qty=Decimal("850.000"),
            base_unit="g",
            unit_cost_paise=18000,
            purchased_at=now,
            lot_status="active",
            created_at=now,
            updated_at=now,
        )
        db.add(lot)
        await db.flush()

        db.add(
            StockLedger(
                id=uuid4(),
                household_id=real_hh.id,
                lot_id=lot.id,
                canonical_item_id=c_parent.id,
                event_type="purchase_in",
                quantity_delta=Decimal("1000.000"),
                base_unit="g",
                balance_after_lot=Decimal("1000.000"),
                unit_cost_paise=18000,
                reference_type="purchase",
                reference_id=purchase.id,
                actor_user_id=real_user.id,
                notes="Personal note with phone 9876543210",
                created_at=now,
            )
        )
        db.add(
            HouseholdBudget(
                id=uuid4(),
                household_id=real_hh.id,
                period_type="monthly",
                overall_limit_paise=1500000,
                grocery_limit_paise=1000000,
                outside_food_limit_paise=500000,
                effective_from=date(2026, 11, 1),
                created_by=real_user.id,
                updated_by=real_user.id,
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            FoodConsumption(
                id=uuid4(),
                household_id=real_hh.id,
                purchase_id=purchase.id,
                consumed_on=date(2026, 11, 20),
                meal_type="lunch",
                dining_mode="delivery",
                merchant_name="Personal Cafe Road 9876543210",
                amount_paise=45000,
                currency_code="INR",
                items_summary=[
                    {"name": "Thali", "qty": 1, "unit": "pcs", "line_total_paise": 45000}
                ],
                notes="Delivered to Flat 402",
                created_at=now,
            )
        )

        cp = CookingPlan(
            id=uuid4(),
            household_id=real_hh.id,
            created_by=real_user.id,
            recipe_title="Yellow Moong Dal Tadka",
            status="completed",
            planned_servings=2,
            ingredients=[
                {
                    "canonical_item_id": str(c_parent.id),
                    "name": "Moong Dal",
                    "quantity": "150 g",
                }
            ],
            deductions=[
                {
                    "lot_id": str(lot.id),
                    "canonical_item_id": str(c_parent.id),
                    "name": "Moong Dal",
                    "deducted_qty": "150",
                    "base_unit": "g",
                }
            ],
            started_at=now,
            completed_at=now,
        )
        db.add(cp)
        await db.flush()
        db.add(
            MealHistory(
                id=uuid4(),
                household_id=real_hh.id,
                cooking_plan_id=cp.id,
                cooked_by=real_user.id,
                recipe_title="Yellow Moong Dal Tadka",
                servings=2,
                ingredients_used=[
                    {
                        "canonical_item_id": str(c_parent.id),
                        "name": "Moong Dal",
                        "quantity": "150 g",
                    }
                ],
                substitutions=[],
                cooked_at=now,
                rating=5,
                feedback_tags=["tasty"],
                feedback_comment="Cooked for my family at Sector 12",
                would_make_again=True,
            )
        )
        await db.commit()
        real_hh_id = real_hh.id
        real_user_id = real_user.id
        global_ci_id = ci_global.id

    await engine.dispose()
    return real_hh_id, real_user_id, global_ci_id


def test_static_eight_recipe_plan_passes_strict_quality_validator() -> None:
    plan_row, slot_rows = build_static_demo_daily_plan(
        demo_household_id=DEMO_HOUSEHOLD_ID,
        raw_inventory_payload=[
            {
                "canonical_item_id": str(uuid4()),
                "name": "Moong Dal",
                "quantity": 500,
                "base_unit": "g",
            },
            {
                "canonical_item_id": str(uuid4()),
                "name": "Potato",
                "quantity": 500,
                "base_unit": "g",
            },
        ],
        plan_date=date(2026, 11, 25),
    )
    assert plan_row["status"] == "ready"
    assert len(slot_rows) == 8
    for slot in slot_rows:
        q = validate_recipe_quality(slot["recipe_data"], strict_ai_generated=True)
        assert q.is_valid, f"{slot['recipe_data']['title']} failed: {q.issues}"


def test_pii_assertion_gate_catches_leaked_pii() -> None:
    dirty = {
        "purchases": [
            {"id": uuid4(), "merchant_name": "Contact owner@example.com or 9876543210"},
        ]
    }
    violations = scan_dataset_for_pii(dirty)
    assert any("email" in v for v in violations)
    assert any("phone" in v for v in violations)


def test_refuses_source_household_equal_to_demo_household() -> None:
    with pytest.raises(ValueError, match="must never equal DEMO_HOUSEHOLD_ID"):
        build_demo_dataset(
            source_household_id=DEMO_HOUSEHOLD_ID,
            source_rows={},
            global_canonical_ingredient_ids=set(),
        )


@pytest.mark.asyncio
async def test_demo_refresh_sanitization_idempotency_fk_integrity_and_source_immutability(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "demo_test.db"
    real_hh_id, real_user_id, _global_ci_id = await _seed_temp_source_db(db_path)

    # Snapshot real household rows in SQLite before any refresh
    def _hash_real_household() -> str:
        c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        h = hashlib.sha256()
        for tbl in (
            "users",
            "households",
            "canonical_items",
            "purchases",
            "purchase_items",
            "inventory_lots",
            "stock_ledger",
            "cooking_plans",
            "meal_history",
        ):
            if tbl == "users":
                rows = c.execute(
                    f'SELECT * FROM "{tbl}" WHERE id = ?', (real_user_id.hex,)
                ).fetchall()
            elif tbl == "households":
                rows = c.execute(
                    f'SELECT * FROM "{tbl}" WHERE id = ?', (real_hh_id.hex,)
                ).fetchall()
            else:
                rows = c.execute(
                    f'SELECT * FROM "{tbl}" WHERE household_id = ?', (real_hh_id.hex,)
                ).fetchall()
            h.update(tbl.encode())
            h.update(json.dumps(rows, default=str).encode())
        c.close()
        return h.hexdigest()

    real_hash_before = _hash_real_household()

    # 1. Dry-run passes with zero PII violations and zero FK issues
    report, fk_issues = run_demo_dry_run(
        db_path, source_household_id=real_hh_id, plan_date=date(2026, 11, 25)
    )
    assert report.ok, report.pii_violations
    assert fk_issues == []
    assert report.table_counts["purchases"] == 1  # draft purchase excluded
    assert report.table_counts["daily_meal_plans"] == 1
    assert report.table_counts["daily_meal_plan_recipes"] == 8

    # Verify explicit sanitization on purchases, purchase_items, stock_ledger, meal_history
    demo_purchase = report.dataset["purchases"][0]
    assert demo_purchase["raw_ocr_payload"] == {}
    assert demo_purchase["attachments_json"] == []
    assert demo_purchase["receipt_number"] is None
    assert demo_purchase["order_id"] is None
    assert demo_purchase["payment_method"] is None
    assert "9876543210" not in demo_purchase["merchant_name"]
    assert demo_purchase["created_by"] == DEMO_USER_ID
    assert report.dataset["purchase_items"][0]["raw_text"] == "Moong Dal 1kg"
    assert report.dataset["stock_ledger"][0]["notes"] is None
    assert report.dataset["meal_history"][0]["feedback_comment"] is None
    assert report.dataset["food_consumptions"][0]["notes"] is None

    # 2. First execute refresh
    target_url = f"sqlite+aiosqlite:///{db_path}"
    _r1, counts1, src_ok1 = await execute_demo_refresh(
        db_path, target_url, source_household_id=real_hh_id, plan_date=date(2026, 11, 25)
    )
    assert src_ok1 is True
    assert counts1["purchases"] == 1
    assert counts1["daily_meal_plan_recipes"] == 8

    # 3. Second consecutive refresh -> deterministic UUIDs, identical counts, zero duplicates
    _r2, counts2, src_ok2 = await execute_demo_refresh(
        db_path, target_url, source_household_id=real_hh_id, plan_date=date(2026, 11, 25)
    )
    assert src_ok2 is True
    assert counts1 == counts2

    # 4. Verify real household is byte-for-byte unchanged
    assert _hash_real_household() == real_hash_before

    # 5. Verify DB state of demo household & self-referential FK preserved
    eng = create_async_engine(target_url)
    sm = async_sessionmaker(eng, expire_on_commit=False)
    async with sm() as db:
        demo_user = (await db.execute(select(User).where(User.id == DEMO_USER_ID))).scalar_one()
        assert demo_user.clerk_user_id == DEMO_CLERK_USER_ID
        assert demo_user.email is None

        demo_items = (
            (
                await db.execute(
                    select(CanonicalItem).where(CanonicalItem.household_id == DEMO_HOUSEHOLD_ID)
                )
            )
            .scalars()
            .all()
        )
        assert len(demo_items) == 2
        merged_items = [i for i in demo_items if i.merged_into_id is not None]
        assert len(merged_items) == 1
        assert merged_items[0].merged_into_id in {i.id for i in demo_items}

        demo_plan = (
            await db.execute(
                select(DailyMealPlan).where(DailyMealPlan.household_id == DEMO_HOUSEHOLD_ID)
            )
        ).scalar_one()
        assert demo_plan.status == "ready"
        demo_slots = (
            (
                await db.execute(
                    select(DailyMealPlanRecipe).where(
                        DailyMealPlanRecipe.daily_meal_plan_id == demo_plan.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(demo_slots) == 8
    await eng.dispose()
