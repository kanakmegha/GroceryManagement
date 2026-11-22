from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.errors import AppError
from app.modules.auth.models import Household, User
from app.modules.catalog.ingredient_mapping_service import confirm_ingredient_mapping
from app.modules.catalog.product_merge_service import (
    dry_run_product_merge,
    merge_canonical_products,
    rollback_canonical_product_merge,
)
from app.modules.inventory.models import (
    CanonicalIngredient,
    CanonicalItem,
    CanonicalMergeLog,
    InventoryLot,
    ItemAlias,
    StockLedger,
)
from app.modules.purchases.models import Purchase, PurchaseItem


async def test_chini_df_merge_preservation_rollback_and_isolation(temp_db_url: str) -> None:
    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        u1 = User(clerk_user_id="clerk_3b_1", email="3b1@example.com", display_name="3B1")
        u2 = User(clerk_user_id="clerk_3b_2", email="3b2@example.com", display_name="3B2")
        db.add_all([u1, u2])
        await db.flush()
        hh1 = Household(name="HH1", created_by=u1.id)
        hh2 = Household(name="HH2", created_by=u2.id)
        db.add_all([hh1, hh2])
        await db.flush()

        # Create CHINI DF survivor + 3 duplicates totaling 9000g across 4 lots
        c_surv = CanonicalItem(
            household_id=hh1.id,
            slug="chini-df",
            name="CHINI DF",
            category="general",
            default_base_unit="g",
        )
        c_d1 = CanonicalItem(
            household_id=hh1.id,
            slug="chinidf",
            name='" CHINIDF.',
            category="general",
            default_base_unit="g",
        )
        c_d2 = CanonicalItem(
            household_id=hh1.id,
            slug="1-chinldf",
            name="1 CHINLDF",
            category="general",
            default_base_unit="g",
        )
        c_d3 = CanonicalItem(
            household_id=hh1.id,
            slug="chinldf",
            name="CHINLDF",
            category="general",
            default_base_unit="g",
        )
        c_other_hh = CanonicalItem(
            household_id=hh2.id,
            slug="chini-df-hh2",
            name="CHINI DF",
            category="general",
            default_base_unit="g",
        )
        db.add_all([c_surv, c_d1, c_d2, c_d3, c_other_hh])
        await db.flush()

        records_spec = [
            (c_surv, "1 CHINI DF 2 KG 67 134.00", Decimal("4000.000"), 6700),
            (c_d1, '" CHINIDF. 1 KG 47 47.00', Decimal("1000.000"), 4700),
            (c_d2, "1 CHINLDF 2 KG 47 94.00", Decimal("2000.000"), 4700),
            (c_d3, "1 CHINLDF 2 KG 53 106.00", Decimal("2000.000"), 5300),
        ]
        lot_ids = []
        pi_ids = []
        ledger_ids = []
        for idx, (ci, raw, qty, up) in enumerate(records_spec):
            p = Purchase(
                household_id=hh1.id,
                created_by=u1.id,
                status="confirmed",
                source_type="ocr",
                purchased_on=date(2026, 11, 10 + idx),
                total_amount_paise=up,
            )
            db.add(p)
            await db.flush()
            pi = PurchaseItem(
                purchase_id=p.id,
                household_id=hh1.id,
                canonical_item_id=ci.id,
                display_name=ci.name,
                raw_text=raw,
                normalized_qty=qty,
                base_unit="g",
                unit_price_paise=up,
                line_total_paise=up,
            )
            db.add(pi)
            await db.flush()
            lot = InventoryLot(
                household_id=hh1.id,
                canonical_item_id=ci.id,
                purchase_item_id=pi.id,
                initial_qty=qty,
                remaining_qty=qty,
                base_unit="g",
                unit_cost_paise=up,
            )
            db.add(lot)
            await db.flush()
            led = StockLedger(
                household_id=hh1.id,
                lot_id=lot.id,
                canonical_item_id=ci.id,
                event_type="PURCHASE_IN",
                quantity_delta=qty,
                base_unit="g",
                balance_after_lot=qty,
                unit_cost_paise=up,
                actor_user_id=u1.id,
            )
            db.add(led)
            await db.flush()
            db.add(
                ItemAlias(
                    household_id=hh1.id,
                    canonical_item_id=ci.id,
                    normalized_alias=f"alias_{idx}_{ci.slug}",
                    source="user_confirmed",
                )
            )
            pi_ids.append(pi.id)
            lot_ids.append(lot.id)
            ledger_ids.append(led.id)
        await db.commit()

        # 8. Household isolation: cross-household merge rejected
        with pytest.raises(AppError) as exc_hh:
            await merge_canonical_products(
                db,
                household_id=hh1.id,
                survivor_id=c_surv.id,
                duplicate_ids=[c_other_hh.id],
            )
        assert exc_hh.value.code == "FORBIDDEN"

        # Dry-run reports 3 reassignments & 9000.000g unchanged without mutating
        dry = await dry_run_product_merge(
            db,
            household_id=hh1.id,
            survivor_id=c_surv.id,
            duplicate_ids=[c_d1.id, c_d2.id, c_d3.id],
        )
        assert dry.purchase_items_to_reassign == 3
        assert dry.inventory_lots_to_reassign == 3
        assert dry.stock_ledger_to_reassign == 3
        assert dry.total_remaining_qty_before == Decimal("9000.000")
        assert dry.total_remaining_qty_after == Decimal("9000.000")

        # 1-6 & 12. Execute merge
        res = await merge_canonical_products(
            db,
            household_id=hh1.id,
            survivor_id=c_surv.id,
            duplicate_ids=[c_d1.id, c_d2.id, c_d3.id],
            merged_by=u1.id,
        )
        await db.commit()
        assert res.total_remaining_qty_after == Decimal("9000.000")
        assert res.price_history_count_after == 4
        assert len(res.merge_log_ids) == 3

        # Verify all lots, ledgers, purchase items, aliases preserved & point to survivor
        surv_lots = (
            (
                await db.execute(
                    select(InventoryLot).where(InventoryLot.canonical_item_id == c_surv.id)
                )
            )
            .scalars()
            .all()
        )
        assert {lot.id for lot in surv_lots} == set(lot_ids)
        surv_pis = (
            (
                await db.execute(
                    select(PurchaseItem).where(PurchaseItem.canonical_item_id == c_surv.id)
                )
            )
            .scalars()
            .all()
        )
        assert {pi.id for pi in surv_pis} == set(pi_ids)
        assert {pi.raw_text for pi in surv_pis} == {r[1] for r in records_spec}
        surv_leds = (
            (
                await db.execute(
                    select(StockLedger).where(StockLedger.canonical_item_id == c_surv.id)
                )
            )
            .scalars()
            .all()
        )
        assert {led.id for led in surv_leds} == set(ledger_ids)
        surv_aliases = (
            (await db.execute(select(ItemAlias).where(ItemAlias.canonical_item_id == c_surv.id)))
            .scalars()
            .all()
        )
        assert len(surv_aliases) == 4

        # Duplicates are soft-merged, never deleted
        d1_ref = await db.get(CanonicalItem, c_d1.id)
        assert (
            d1_ref is not None and d1_ref.is_active is False and d1_ref.merged_into_id == c_surv.id
        )

        # 10. Repeated merge rejection
        with pytest.raises(AppError) as exc_rep:
            await merge_canonical_products(
                db,
                household_id=hh1.id,
                survivor_id=c_surv.id,
                duplicate_ids=[c_d1.id],
            )
        assert exc_rep.value.code == "ALREADY_MERGED"

        # 11. Rollback restores original FKs and active status
        await rollback_canonical_product_merge(
            db, household_id=hh1.id, merge_log_id=res.merge_log_ids[0]
        )
        await db.commit()
        d1_restored = await db.get(CanonicalItem, c_d1.id)
        assert (
            d1_restored is not None
            and d1_restored.is_active is True
            and d1_restored.merged_into_id is None
        )
        log_row = await db.get(CanonicalMergeLog, res.merge_log_ids[0])
        assert log_row is not None and log_row.reversed_at is not None

    await engine.dispose()


async def test_merge_ingredient_mapping_conflict_handling(temp_db_url: str) -> None:
    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        u = User(clerk_user_id="clerk_3b_c", email="3bc@example.com", display_name="3BC")
        db.add(u)
        await db.flush()
        hh = Household(name="HHC", created_by=u.id)
        db.add(hh)
        await db.flush()

        c1 = CanonicalItem(
            household_id=hh.id, slug="c1", name="Item 1", category="grain", default_base_unit="g"
        )
        c2 = CanonicalItem(
            household_id=hh.id, slug="c2", name="Item 2", category="grain", default_base_unit="g"
        )
        db.add_all([c1, c2])
        await db.flush()

        ing_basmati = (
            await db.execute(
                select(CanonicalIngredient).where(
                    CanonicalIngredient.normalized_name == "basmati_rice"
                )
            )
        ).scalar_one()
        ing_idli = (
            await db.execute(
                select(CanonicalIngredient).where(
                    CanonicalIngredient.normalized_name == "idli_rice"
                )
            )
        ).scalar_one()

        await confirm_ingredient_mapping(
            db, household_id=hh.id, canonical_item_id=c1.id, canonical_ingredient_id=ing_basmati.id
        )
        await confirm_ingredient_mapping(
            db, household_id=hh.id, canonical_item_id=c2.id, canonical_ingredient_id=ing_idli.id
        )
        await db.commit()

        # Dry-run flags conflict; merge rejects with 409 MAPPING_CONFLICT
        dry = await dry_run_product_merge(
            db, household_id=hh.id, survivor_id=c1.id, duplicate_ids=[c2.id]
        )
        assert len(dry.ingredient_mapping_conflicts) == 1

        with pytest.raises(AppError) as exc:
            await merge_canonical_products(
                db, household_id=hh.id, survivor_id=c1.id, duplicate_ids=[c2.id]
            )
        assert exc.value.code == "MAPPING_CONFLICT"

    await engine.dispose()
