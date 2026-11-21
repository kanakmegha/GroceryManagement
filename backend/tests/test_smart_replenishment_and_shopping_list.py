"""Comprehensive tests for Smart Grocery Replenishment Engine and Unified Shopping List."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select

from app.modules.inventory.models import CanonicalItem, InventoryLot, StockLedger
from app.modules.replenishment.engine import _to_local_date
from tests.conftest import EphemeralJWKSProvider


@pytest.mark.asyncio
async def test_replenishment_patterns_fefo_depletion_units_exclusions_and_isolation(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    token_a = jwks_provider.make_token(sub="user_rep_a", email="repa@example.com", name="RepA")
    hdr_a = {"Authorization": f"Bearer {token_a}"}
    me_a = await client.get("/api/v1/auth/me", headers=hdr_a)
    assert me_a.status_code == 200
    hh_a = UUID(me_a.json()["active_household"]["id"])
    user_a = UUID(me_a.json()["user"]["id"])
    hdr_a["X-Household-Id"] = str(hh_a)

    token_b = jwks_provider.make_token(sub="user_rep_b", email="repb@example.com", name="RepB")
    hdr_b = {"Authorization": f"Bearer {token_b}"}
    me_b = await client.get("/api/v1/auth/me", headers=hdr_b)
    hdr_b["X-Household-Id"] = me_b.json()["active_household"]["id"]

    async def confirm_grocery_item(purchased_on: str, name: str, qty: float, unit: str, cat: str = "dairy") -> str:
        res = await client.post(
            "/api/v1/purchases",
            headers=hdr_a,
            json={
                "merchant_name": "FreshMart",
                "purchased_on": purchased_on,
                "receipt_category": "grocery",
                "items": [
                    {
                        "display_name": name,
                        "category": cat,
                        "qty": qty,
                        "unit": unit,
                        "unit_price_paise": 5000,
                        "line_total_paise": 5000,
                        "track_inventory": True,
                    }
                ],
                "total_amount_paise": 5000,
            },
        )
        assert res.status_code == 201
        pid = res.json()["id"]
        c_res = await client.post(f"/api/v1/purchases/{pid}/confirm", headers=hdr_a)
        assert c_res.status_code == 200
        lots = c_res.json().get("created_lots", [])
        return lots[0]["canonical_item_id"] if lots else ""

    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    # 1. Repeated purchases of Milk (every 4 days: 5 purchases) -> high confidence, then deplete via COOK_OUT + WASTE_OUT
    milk_cid = await confirm_grocery_item((today - timedelta(days=16)).isoformat(), "Toned Milk", 1000, "ml")
    await confirm_grocery_item((today - timedelta(days=12)).isoformat(), "Toned Milk", 1000, "ml")
    await confirm_grocery_item((today - timedelta(days=8)).isoformat(), "Toned Milk", 1000, "ml")
    await confirm_grocery_item((today - timedelta(days=4)).isoformat(), "Toned Milk", 1000, "ml")
    await confirm_grocery_item(today.isoformat(), "Toned Milk", 1000, "ml")

    # 2. Single-purchase cold start: Olive Oil
    oil_cid = await confirm_grocery_item((today - timedelta(days=5)).isoformat(), "Olive Oil", 1, "l", cat="pantry")

    # 3. Irregular purchases of Tomatoes (3 days, 11 days) -> low confidence
    tom_cid = await confirm_grocery_item((today - timedelta(days=15)).isoformat(), "Fresh Tomatoes", 500, "g", cat="produce")
    await confirm_grocery_item((today - timedelta(days=12)).isoformat(), "Fresh Tomatoes", 500, "g", cat="produce")
    await confirm_grocery_item((today - timedelta(days=1)).isoformat(), "Fresh Tomatoes", 500, "g", cat="produce")

    # 4. Non-food item (Hair Oil) + Restaurant receipt -> both must be excluded
    await confirm_grocery_item(today.isoformat(), "Dabur Almond Hair Oil", 1, "pcs", cat="personal_care")
    rest_res = await client.post(
        "/api/v1/purchases",
        headers=hdr_a,
        json={
            "merchant_name": "Udupi Sagar",
            "purchased_on": today.isoformat(),
            "receipt_category": "restaurant",
            "items": [
                {
                    "display_name": "Masala Dosa",
                    "category": "general",
                    "qty": 2,
                    "unit": "pcs",
                    "unit_price_paise": 10000,
                    "line_total_paise": 20000,
                    "track_inventory": False,
                }
            ],
            "total_amount_paise": 20000,
        },
    )
    assert rest_res.status_code == 201
    assert (await client.post(f"/api/v1/purchases/{rest_res.json()['id']}/confirm", headers=hdr_a)).status_code == 200

    # Manipulate lots & ledger deterministically for Milk (all depleted), Tomatoes (1 expired lot + 1 active partial lot + COOK_OUT/WASTE_OUT), and Incompatible unit item
    async with client._transport.app.state.session_maker() as db:  # type: ignore[attr-defined]
        # Deplete all Milk lots and record COOK_OUT + WASTE_OUT
        milk_lots = (
            await db.execute(select(InventoryLot).where(InventoryLot.canonical_item_id == UUID(milk_cid)))
        ).scalars().all()
        for lot in milk_lots:
            lot.remaining_qty = Decimal("0")
            lot.lot_status = "depleted"
        db.add(
            StockLedger(
                household_id=hh_a,
                lot_id=milk_lots[0].id,
                canonical_item_id=UUID(milk_cid),
                event_type="COOK_OUT",
                quantity_delta=Decimal("-4500"),
                base_unit="ml",
                balance_after_lot=Decimal("0"),
                unit_cost_paise=0,
                actor_user_id=user_a,
                created_at=datetime.now(UTC) - timedelta(days=5),
            )
        )
        db.add(
            StockLedger(
                household_id=hh_a,
                lot_id=milk_lots[0].id,
                canonical_item_id=UUID(milk_cid),
                event_type="WASTE_OUT",
                quantity_delta=Decimal("-500"),
                base_unit="ml",
                balance_after_lot=Decimal("0"),
                unit_cost_paise=0,
                actor_user_id=user_a,
                created_at=datetime.now(UTC) - timedelta(days=1),
            )
        )

        # Tomatoes: expire first 2 lots, keep 3rd lot with 200g remaining & consumption 100g/day -> 2 days remaining -> buy_now
        tom_lots = (
            await db.execute(
                select(InventoryLot)
                .where(InventoryLot.canonical_item_id == UUID(tom_cid))
                .order_by(InventoryLot.purchased_at.asc())
            )
        ).scalars().all()
        tom_lots[0].expires_on = today - timedelta(days=2)  # expired -> excluded from usable stock
        tom_lots[1].remaining_qty = Decimal("0")
        tom_lots[1].lot_status = "depleted"
        tom_lots[2].remaining_qty = Decimal("200")
        db.add(
            StockLedger(
                household_id=hh_a,
                lot_id=tom_lots[2].id,
                canonical_item_id=UUID(tom_cid),
                event_type="ADJUSTMENT_OUT",
                quantity_delta=Decimal("-1000"),
                base_unit="g",
                balance_after_lot=Decimal("200"),
                unit_cost_paise=0,
                actor_user_id=user_a,
                created_at=datetime.now(UTC) - timedelta(days=9),
            )
        )
        db.add(
            CanonicalItem(
                household_id=hh_a,
                slug="legacy-hair-oil",
                name="Legacy Almond Hair Oil",
                category="general",
                default_base_unit="pcs",
                pantry_eligible=True,
                budget_treatment="pantry_inventory",
            )
        )
        await db.commit()

    # Fetch recommendations
    rec_res = await client.get("/api/v1/replenishment/recommendations?refresh=true", headers=hdr_a)
    assert rec_res.status_code == 200
    data = rec_res.json()

    buy_now_by_id = {x["canonical_item_id"]: x for x in data["buy_now"]}
    regular_by_id = {x["canonical_item_id"]: x for x in data["regular_purchases"]}
    all_names = [
        x["item_name"]
        for grp in (data["buy_now"], data["buy_soon"], data["regular_purchases"], data["dismissed"])
        for x in grp
    ]

    # Non-food & restaurant excluded
    assert "Dabur Almond Hair Oil" not in all_names
    assert "Masala Dosa" not in all_names
    assert any(d["issue_type"] == "excluded_non_food" for d in data["data_quality_issues"])

    # Milk: depleted, median 4 days, high confidence, in buy_now
    milk_rec = buy_now_by_id[milk_cid]
    assert milk_rec["stock_status"] == "depleted"
    assert milk_rec["median_interval_days"] == 4.0
    assert milk_rec["confidence"] == "high"
    assert milk_rec["purchase_count"] == 5

    # Tomatoes: low confidence -> suppressed from actionable buy_now into needs_more_data, while metrics remain computed
    needs_more_by_id = {x["canonical_item_id"]: x for x in data.get("needs_more_data", [])}
    tom_rec = needs_more_by_id[tom_cid]
    assert tom_cid not in buy_now_by_id
    assert tom_rec["available_qty"] == 200.0
    assert tom_rec["expired_qty"] == 500.0
    assert tom_rec["estimated_days_remaining"] == 2.0
    assert tom_rec["confidence"] == "low"  # irregular 3 vs 11 day intervals

    # Olive Oil: single purchase -> insufficient_data -> suppressed from actionable lists into needs_more_data
    oil_rec = needs_more_by_id[oil_cid]
    assert oil_rec["confidence"] == "insufficient_data"
    assert oil_rec["stock_status"] == "adequate"
    assert oil_rec["recommended_qty"] is None
    assert oil_cid not in regular_by_id

    # New purchase resets depletion prediction for Milk (8000 ml -> SUFFICIENT stock -> suppressed in Phase 5B)
    await confirm_grocery_item(today.isoformat(), "Toned Milk", 8000, "ml")
    rec_after = (await client.get("/api/v1/replenishment/recommendations?refresh=true", headers=hdr_a)).json()
    assert milk_cid not in {x["canonical_item_id"] for x in rec_after["buy_now"]}
    assert milk_cid not in {x["canonical_item_id"] for x in rec_after["regular_purchases"]}
    suppressed_after = {x["canonical_item_id"]: x for x in rec_after["needs_more_data"]}
    assert milk_cid in suppressed_after
    assert suppressed_after[milk_cid]["normalized_stock_status"] == "SUFFICIENT"
    assert suppressed_after[milk_cid]["eligibility_status"] == "SUPPRESSED"

    # Household isolation: Household B sees 0 items
    rec_b = (await client.get("/api/v1/replenishment/recommendations?refresh=true", headers=hdr_b)).json()
    assert rec_b["total_tracked_items"] == 0


@pytest.mark.asyncio
async def test_shopping_list_deduplication_units_feedback_restore_and_no_pantry_mutation(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    token = jwks_provider.make_token(sub="user_shop", email="shop@example.com", name="Shop")
    hdr = {"Authorization": f"Bearer {token}"}
    me = await client.get("/api/v1/auth/me", headers=hdr)
    hdr["X-Household-Id"] = me.json()["active_household"]["id"]

    # Create a canonical item via confirmed purchase
    p = await client.post(
        "/api/v1/purchases",
        headers=hdr,
        json={
            "merchant_name": "Mart",
            "purchased_on": date.today().isoformat(),
            "receipt_category": "grocery",
            "items": [
                {
                    "display_name": "Basmati Rice",
                    "category": "grains",
                    "qty": 1000,
                    "unit": "g",
                    "unit_price_paise": 12000,
                    "line_total_paise": 12000,
                    "track_inventory": True,
                }
            ],
            "total_amount_paise": 12000,
        },
    )
    assert p.status_code == 201
    c = await client.post(f"/api/v1/purchases/{p.json()['id']}/confirm", headers=hdr)
    rice_cid = c.json()["created_lots"][0]["canonical_item_id"]

    # Add Rice from predicted_replenishment (500 g) and then from recipe_missing (1 kg) -> merged to 1500 g, 2 sources preserved
    add1 = await client.post(
        "/api/v1/shopping-list/items",
        headers=hdr,
        json={
            "canonical_item_id": rice_cid,
            "item_name": "Basmati Rice",
            "quantity": 500,
            "unit": "g",
            "source": "predicted_replenishment",
            "source_ref_id": rice_cid,
        },
    )
    assert add1.status_code == 201

    add2 = await client.post(
        "/api/v1/shopping-list/items",
        headers=hdr,
        json={
            "canonical_item_id": rice_cid,
            "item_name": "Basmati Rice",
            "quantity": 1,
            "unit": "kg",
            "source": "recipe_missing",
            "source_ref_id": "rec_biryani",
        },
    )
    assert add2.status_code == 201
    merged = add2.json()
    assert merged["id"] == add1.json()["id"]
    assert merged["quantity"] == 1500.0
    assert merged["unit"] == "g"
    assert len(merged["sources"]) == 2

    # Incompatible unit addition (2 pcs) -> must NOT silently convert; surfaces unit_conflict_note
    add3 = await client.post(
        "/api/v1/shopping-list/items",
        headers=hdr,
        json={
            "canonical_item_id": rice_cid,
            "item_name": "Basmati Rice",
            "quantity": 2,
            "unit": "pcs",
            "source": "manual",
        },
    )
    assert add3.status_code == 201
    assert add3.json()["quantity"] == 1500.0
    assert add3.json()["unit_conflict_note"] is not None

    # Mark shopping list item as purchased -> MUST NOT create any new inventory lots
    lots_before = (await client.get("/api/v1/inventory/lots?status=active", headers=hdr)).json()["items"]
    patch_res = await client.patch(
        f"/api/v1/shopping-list/items/{merged['id']}",
        headers=hdr,
        json={"status": "purchased"},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["status"] == "purchased"
    lots_after = (await client.get("/api/v1/inventory/lots?status=active", headers=hdr)).json()["items"]
    assert len(lots_after) == len(lots_before)

    # Dismiss recommendation via feedback -> moves to dismissed, then restore -> returns to active
    fb_res = await client.post(
        f"/api/v1/replenishment/{rice_cid}/feedback",
        headers=hdr,
        json={"action": "dismissed", "reason_snapshot": "Have enough in storage"},
    )
    assert fb_res.status_code == 201
    recs_dismissed = (await client.get("/api/v1/replenishment/recommendations?refresh=true", headers=hdr)).json()
    assert rice_cid in {x["canonical_item_id"] for x in recs_dismissed["dismissed"]}

    rest_res = await client.post(f"/api/v1/replenishment/{rice_cid}/restore", headers=hdr)
    assert rest_res.status_code == 204
    recs_restored = (await client.get("/api/v1/replenishment/recommendations?refresh=true", headers=hdr)).json()
    assert rice_cid not in {x["canonical_item_id"] for x in recs_restored["dismissed"]}


@pytest.mark.asyncio
async def test_replenishment_ist_midnight_and_utc_date_boundary(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
) -> None:
    """Verify household-local IST calendar dates are used consistently across midnight IST
    and when naive SQLite UTC timestamps have a different UTC date vs IST date.
    """
    ist = ZoneInfo("Asia/Kolkata")
    fallback = date(2026, 11, 21)

    # 1. Just before midnight IST: 2026-11-20 23:59 IST == 2026-11-20 18:29 UTC
    before_mid_aware = datetime(2026, 11, 20, 18, 29, 0, tzinfo=UTC)
    before_mid_naive = datetime(2026, 11, 20, 18, 29, 0)
    assert _to_local_date(before_mid_aware, ist, fallback) == date(2026, 11, 20)
    assert _to_local_date(before_mid_naive, ist, fallback) == date(2026, 11, 20)

    # 2. Just after midnight IST: 2026-11-21 00:01 IST == 2026-11-20 18:31 UTC (UTC date != IST date)
    after_mid_aware = datetime(2026, 11, 20, 18, 31, 0, tzinfo=UTC)
    after_mid_naive = datetime(2026, 11, 20, 18, 31, 0)
    assert _to_local_date(after_mid_aware, ist, fallback) == date(2026, 11, 21)
    assert _to_local_date(after_mid_naive, ist, fallback) == date(2026, 11, 21)

    # 3. End-to-end: naive UTC timestamp whose UTC date is 9 days ago in UTC (but 8 days ago in IST)
    # Wait: if we set created_at to today_ist 00:30 IST (= yesterday_ist 19:00 UTC),
    # in IST it is today_ist (span_days == 1, NOT 2).
    token = jwks_provider.make_token(sub="user_ist_tz", email="ist@example.com", name="ISTUser")
    hdr = {"Authorization": f"Bearer {token}"}
    me = await client.get("/api/v1/auth/me", headers=hdr)
    assert me.status_code == 200
    hh_id = UUID(me.json()["active_household"]["id"])
    user_id = UUID(me.json()["user"]["id"])
    hdr["X-Household-Id"] = str(hh_id)

    today_ist = datetime.now(ist).date()
    p_res = await client.post(
        "/api/v1/purchases",
        headers=hdr,
        json={
            "merchant_name": "IST Mart",
            "purchased_on": today_ist.isoformat(),
            "receipt_category": "grocery",
            "items": [
                {
                    "display_name": "Fresh Curd",
                    "category": "dairy",
                    "qty": 500,
                    "unit": "g",
                    "unit_price_paise": 4000,
                    "line_total_paise": 4000,
                    "track_inventory": True,
                }
            ],
            "total_amount_paise": 4000,
        },
    )
    assert p_res.status_code == 201
    c_res = await client.post(f"/api/v1/purchases/{p_res.json()['id']}/confirm", headers=hdr)
    assert c_res.status_code == 200
    lot_info = c_res.json()["created_lots"][0]
    cid = lot_info["canonical_item_id"]

    # Record a 100g COOK_OUT at 00:30 IST today (which is 19:00 UTC on today_ist - 1 day)
    ist_0030 = datetime(today_ist.year, today_ist.month, today_ist.day, 0, 30, 0, tzinfo=ist)
    utc_equiv_naive = ist_0030.astimezone(UTC).replace(tzinfo=None)
    assert utc_equiv_naive.date() == today_ist - timedelta(days=1)

    async with client._transport.app.state.session_maker() as db:  # type: ignore[attr-defined]
        lot = (
            await db.execute(select(InventoryLot).where(InventoryLot.canonical_item_id == UUID(cid)))
        ).scalar_one()
        lot.remaining_qty = Decimal("400")
        db.add(
            StockLedger(
                household_id=hh_id,
                lot_id=lot.id,
                canonical_item_id=UUID(cid),
                event_type="COOK_OUT",
                quantity_delta=Decimal("-100"),
                base_unit="g",
                balance_after_lot=Decimal("400"),
                unit_cost_paise=0,
                actor_user_id=user_id,
                created_at=utc_equiv_naive,
            )
        )
        await db.commit()

    recs = (await client.get("/api/v1/replenishment/recommendations?refresh=true", headers=hdr)).json()
    all_items = {
        x["canonical_item_id"]: x
        for grp in (
            recs["buy_now"],
            recs["buy_soon"],
            recs["regular_purchases"],
            recs["dismissed"],
            recs.get("needs_more_data", []),
        )
        for x in grp
    }
    curd_rec = all_items[cid]
    # Because 00:30 IST today is today_ist (span_days=1), avg_daily_consumption == 100.0 g/day (not 50.0)
    assert curd_rec["avg_daily_consumption"] == 100.0
    assert curd_rec["estimated_days_remaining"] == 4.0
