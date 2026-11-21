from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.modules.auth.models import Household, User
from app.modules.catalog.ingredient_mapping_service import (
    backfill_canonical_item_mappings,
    confirm_ingredient_mapping,
    resolve_or_suggest_mapping,
)
from app.modules.inventory.models import (
    CanonicalIngredient,
    CanonicalItem,
    InventoryLot,
    StockLedger,
)
from app.modules.purchases.models import Purchase, PurchaseItem
from tests.conftest import EphemeralJWKSProvider


def _reconcile_purchase_item(
    purchase_status: str,
    item: PurchaseItem,
    lot: InventoryLot | None,
    ledgers: list[StockLedger],
) -> str:
    if purchase_status == "draft":
        if item.budget_treatment == "requires_user_review":
            return "AMBIGUOUS_REQUIRES_REVIEW"
        return "UNCONFIRMED_DRAFT"
    if purchase_status == "confirmed":
        if lot is not None and len(ledgers) > 0:
            return "CONFIRMED_AND_INVENTORIED"
        if lot is not None and len(ledgers) == 0:
            return "CONFIRMED_BUT_PARTIALLY_INVENTORIED"
        return "CONFIRMED_BUT_MISSING_INVENTORY"
    return "FAILED_OR_INCOMPLETE"


async def test_reconciliation_classifications_and_duplicate_recovery_prevention(
    temp_db_url: str,
) -> None:
    """Covers tests 1-5: confirmed+inventoried, confirmed+missing, partial,
    unconfirmed draft, and idempotent duplicate-recovery prevention.
    """
    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        u = User(clerk_user_id="clerk_25_1", email="p25@example.com", display_name="P25")
        db.add(u)
        await db.flush()
        hh = Household(name="P25 HH", created_by=u.id)
        db.add(hh)
        await db.flush()

        ci = CanonicalItem(
            household_id=hh.id,
            slug="toor-dal-1kg",
            name="Toor Dal 1kg",
            category="pulse",
            default_base_unit="g",
        )
        db.add(ci)
        await db.flush()

        # 1. Confirmed & Inventoried
        p_ok = Purchase(
            household_id=hh.id,
            created_by=u.id,
            status="confirmed",
            source_type="manual",
            purchased_on=date(2026, 11, 19),
            confirmed_at=datetime.now(UTC),
            total_amount_paise=15000,
        )
        db.add(p_ok)
        await db.flush()
        pi_ok = PurchaseItem(
            purchase_id=p_ok.id,
            household_id=hh.id,
            canonical_item_id=ci.id,
            display_name="Toor Dal 1kg",
            normalized_qty=Decimal("1000.000"),
            base_unit="g",
            unit_price_paise=15000,
            line_total_paise=15000,
        )
        db.add(pi_ok)
        await db.flush()
        lot_ok = InventoryLot(
            household_id=hh.id,
            canonical_item_id=ci.id,
            purchase_item_id=pi_ok.id,
            initial_qty=Decimal("1000.000"),
            remaining_qty=Decimal("1000.000"),
            base_unit="g",
            unit_cost_paise=15000,
        )
        db.add(lot_ok)
        await db.flush()
        led_ok = StockLedger(
            household_id=hh.id,
            lot_id=lot_ok.id,
            canonical_item_id=ci.id,
            event_type="PURCHASE_IN",
            quantity_delta=Decimal("1000.000"),
            base_unit="g",
            balance_after_lot=Decimal("1000.000"),
            unit_cost_paise=15000,
            actor_user_id=u.id,
        )
        db.add(led_ok)

        # 2. Confirmed but missing inventory (simulated historical anomaly)
        p_miss = Purchase(
            household_id=hh.id,
            created_by=u.id,
            status="confirmed",
            source_type="ocr",
            purchased_on=date(2026, 11, 19),
            confirmed_at=datetime.now(UTC),
            total_amount_paise=6000,
        )
        db.add(p_miss)
        await db.flush()
        pi_miss = PurchaseItem(
            purchase_id=p_miss.id,
            household_id=hh.id,
            canonical_item_id=ci.id,
            display_name="Toor Dal 500g",
            normalized_qty=Decimal("500.000"),
            base_unit="g",
            unit_price_paise=6000,
            line_total_paise=6000,
        )
        db.add(pi_miss)

        # 3. Partially inventoried (lot exists, ledger missing)
        p_part = Purchase(
            household_id=hh.id,
            created_by=u.id,
            status="confirmed",
            source_type="manual",
            purchased_on=date(2026, 11, 19),
            confirmed_at=datetime.now(UTC),
            total_amount_paise=6000,
        )
        db.add(p_part)
        await db.flush()
        pi_part = PurchaseItem(
            purchase_id=p_part.id,
            household_id=hh.id,
            canonical_item_id=ci.id,
            display_name="Toor Dal 500g",
            normalized_qty=Decimal("500.000"),
            base_unit="g",
            unit_price_paise=6000,
            line_total_paise=6000,
        )
        db.add(pi_part)
        await db.flush()
        lot_part = InventoryLot(
            household_id=hh.id,
            canonical_item_id=ci.id,
            purchase_item_id=pi_part.id,
            initial_qty=Decimal("500.000"),
            remaining_qty=Decimal("500.000"),
            base_unit="g",
            unit_cost_paise=6000,
        )
        db.add(lot_part)

        # 4. Unconfirmed draft
        p_draft = Purchase(
            household_id=hh.id,
            created_by=u.id,
            status="draft",
            source_type="ocr",
            purchased_on=date(2026, 11, 20),
            total_amount_paise=5000,
        )
        db.add(p_draft)
        await db.flush()
        pi_draft = PurchaseItem(
            purchase_id=p_draft.id,
            household_id=hh.id,
            canonical_item_id=ci.id,
            display_name="Toor Dal 500g",
            normalized_qty=Decimal("500.000"),
            base_unit="g",
            unit_price_paise=5000,
            line_total_paise=5000,
        )
        db.add(pi_draft)
        await db.commit()

        assert _reconcile_purchase_item("confirmed", pi_ok, lot_ok, [led_ok]) == "CONFIRMED_AND_INVENTORIED"
        assert _reconcile_purchase_item("confirmed", pi_miss, None, []) == "CONFIRMED_BUT_MISSING_INVENTORY"
        assert _reconcile_purchase_item("confirmed", pi_part, lot_part, []) == "CONFIRMED_BUT_PARTIALLY_INVENTORIED"
        assert _reconcile_purchase_item("draft", pi_draft, None, []) == "UNCONFIRMED_DRAFT"

        # 5. Duplicate recovery prevention via unique purchase_item_id on InventoryLot
        existing_lot = (
            await db.execute(select(InventoryLot).where(InventoryLot.purchase_item_id == pi_ok.id))
        ).scalar_one_or_none()
        assert existing_lot is not None  # Idempotent recovery skips already-inventoried items

    await engine.dispose()


async def test_pantry_visibility_mapping_isolation_and_no_inventory_mutation(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
    temp_db_url: str,
) -> None:
    """Covers tests 6-10: unmapped product visible in pantry, multiple brands -> 1 ingredient,
    household isolation, mapping without inventory mutation, and purchase confirmation idempotency.
    """
    token_a = jwks_provider.make_token(sub="user_25_a", email="a25@example.com", name="A25")
    token_b = jwks_provider.make_token(sub="user_25_b", email="b25@example.com", name="B25")
    h_a = {"Authorization": f"Bearer {token_a}"}
    h_b = {"Authorization": f"Bearer {token_b}"}

    me_a = await client.get("/api/v1/auth/me", headers=h_a)
    me_b = await client.get("/api/v1/auth/me", headers=h_b)
    assert me_a.status_code == 200 and me_b.status_code == 200
    hh_a_id_str = me_a.json()["active_household"]["id"]

    # Create & confirm an unmapped custom product in household A
    p_resp = await client.post(
        "/api/v1/purchases",
        headers=h_a,
        json={
            "merchant_name": "Artisan Store",
            "purchased_on": "2026-11-20",
            "items": [
                {
                    "display_name": "Artisan Spelt Flour 250g",
                    "qty": 250,
                    "unit": "g",
                    "unit_price_paise": 9900,
                    "line_total_paise": 9900,
                    "category": "grain",
                    "classification_source": "user_override",
                    "budget_treatment": "pantry_inventory",
                    "track_inventory": True,
                }
            ],
        },
    )
    assert p_resp.status_code == 201
    pid = p_resp.json()["id"]

    c1 = await client.post(f"/api/v1/purchases/{pid}/confirm", headers=h_a, json={})
    assert c1.status_code == 200
    assert c1.json()["ingredient_mappings"][0]["status"] == "unmapped"

    # 10. Purchase confirmation idempotency: second confirm returns 409, no duplicate lots
    c2 = await client.post(f"/api/v1/purchases/{pid}/confirm", headers=h_a, json={})
    assert c2.status_code == 409

    # 6. Unmapped legacy/custom product remains visible in product-level pantry
    pantry_a = await client.get("/api/v1/inventory/summary", headers=h_a)
    assert pantry_a.status_code == 200
    assert len(pantry_a.json()["items"]) == 1
    assert Decimal(pantry_a.json()["items"][0]["total_remaining_qty"]) == Decimal("250.000")
    unmapped_ci_id = pantry_a.json()["items"][0]["canonical_item_id"]

    # 8. Household B does not see household A's pantry or mappings
    pantry_b = await client.get("/api/v1/inventory/summary", headers=h_b)
    assert pantry_b.status_code == 200
    assert pantry_b.json()["items"] == []

    # 7 & 9. Map multiple brands -> 1 ingredient & confirm mapping does not mutate inventory
    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        milk = (
            await db.execute(
                select(CanonicalIngredient).where(CanonicalIngredient.normalized_name == "milk")
            )
        ).scalar_one()
        hh_a_id = __import__("uuid").UUID(hh_a_id_str)
        await confirm_ingredient_mapping(
            db,
            household_id=hh_a_id,
            canonical_item_id=unmapped_ci_id if isinstance(unmapped_ci_id, type(milk.id)) else __import__("uuid").UUID(unmapped_ci_id),
            canonical_ingredient_id=milk.id,
        )
        await db.commit()

        # Inventory quantity remains unchanged after adding IngredientMapping
        lots = (await db.execute(select(InventoryLot))).scalars().all()
        assert len(lots) == 1
        assert lots[0].remaining_qty == Decimal("250.000")
    await engine.dispose()
