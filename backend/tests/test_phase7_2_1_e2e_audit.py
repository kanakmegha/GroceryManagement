"""Phase 7.2.1 End-to-End Integration Audit tests (isolated temp DB, never touches dev_local.db)."""

from datetime import date, timedelta
from decimal import Decimal
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import build_async_engine, build_session_maker
from app.infra.ocr.schemas import normalize_metric_unit
from app.modules.catalog.ingredient_catalogue import get_ingredient_catalogue
from app.modules.catalog.ingredient_catalogue_sync import sync_catalogue_to_db
from app.modules.catalog.ingredient_mapping_service import (
    confirm_ingredient_mapping,
    resolve_or_suggest_mapping,
)
from app.modules.catalog.ingredient_resolver import IngredientResolver
from app.modules.inventory.models import (
    CanonicalIngredient,
    CanonicalItem,
    IngredientAlias,
    InventoryLot,
    StockLedger,
)
from app.modules.purchases.models import PurchaseItem
from app.modules.recipes.grounding import classify_recommendation_type, reconcile_ingredient


async def _create_and_confirm_receipt(
    client: AsyncClient,
    headers: dict[str, str],
    db: AsyncSession,
    merchant: str,
    lines: list[dict],
    *,
    idem_key: str,
) -> dict:
    payload = {
        "merchant_name": merchant,
        "purchased_on": date.today().isoformat(),
        "currency_code": "INR",
        "source_type": "manual",
        "document_type": "receipt",
        "items": [
            {
                "raw_text": ln["raw_text"],
                "display_name": ln["display_name"],
                "normalized_qty": ln["qty"],
                "base_unit": ln["unit"],
                "line_total_paise": ln.get("paise", 5000),
                "category": ln.get("category", "pantry"),
                "expires_on": (date.today() + timedelta(days=10)).isoformat(),
            }
            for ln in lines
        ],
    }
    create_resp = await client.post("/api/v1/purchases", headers=headers, json=payload)
    assert create_resp.status_code == 201, create_resp.text
    purchase_id = create_resp.json()["id"]

    # Confirm purchase with Idempotency-Key
    conf_headers = {**headers, "Idempotency-Key": idem_key}
    conf_resp = await client.post(
        f"/api/v1/purchases/{purchase_id}/confirm",
        headers=conf_headers,
        json={"receipt_category": "grocery"},
    )
    assert conf_resp.status_code == 200, conf_resp.text
    return conf_resp.json()


@pytest.mark.asyncio
async def test_phase7_2_1_e2e_synthetic_receipts_aggregation_and_idempotency(
    client: AsyncClient,
    jwks_provider,
    temp_db_url: str,
) -> None:
    sub = f"user-721-{uuid.uuid4().hex[:8]}"
    headers = {
        "Authorization": f"Bearer {jwks_provider.make_token(sub=sub, email=f'{sub}@example.com')}"
    }
    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    hh_id = uuid.UUID(me.json()["active_household"]["id"])

    engine = build_async_engine(temp_db_url)
    sm = build_session_maker(engine)

    # Pre-sync catalogue + add a historical non-catalogue CanonicalIngredient to verify preservation
    async with sm() as db:
        await sync_catalogue_to_db(db)
        hist_ing = CanonicalIngredient(
            name="Historical Spice Mix",
            normalized_name="historical_spice_mix",
            category="spices_seasonings",
            standard_unit="g",
        )
        db.add(hist_ing)
        await db.commit()

    # Receipt A
    async with sm() as db:
        conf_a = await _create_and_confirm_receipt(
            client,
            headers,
            db,
            "BigBasket",
            [
                {"raw_text": "Amul Gold Full Cream Milk 1 L", "display_name": "Amul Gold Full Cream Milk", "qty": "1000.000", "unit": "ml", "category": "dairy"},
                {"raw_text": "Fresh Potato 2 KG", "display_name": "Fresh Potato", "qty": "2000.000", "unit": "g", "category": "produce"},
                {"raw_text": "Aashirvaad Atta 5 KG", "display_name": "Aashirvaad Atta", "qty": "5000.000", "unit": "g", "category": "pantry"},
                {"raw_text": "Mother Dairy Toned Milk 500 ML", "display_name": "Mother Dairy Toned Milk", "qty": "500.000", "unit": "ml", "category": "dairy"},
                {"raw_text": "Cherry Tomato 250 G", "display_name": "Cherry Tomato", "qty": "250.000", "unit": "g", "category": "produce"},
            ],
            idem_key="receipt-a-idem-1",
        )
        # Re-confirm with same Idempotency-Key -> rejected as 409 Conflict (never duplicates stock)
        conf_a_dup = await client.post(
            f"/api/v1/purchases/{conf_a['purchase_id']}/confirm",
            headers={**headers, "Idempotency-Key": "receipt-a-idem-1"},
            json={"receipt_category": "grocery"},
        )
        assert conf_a_dup.status_code == 409

    # Receipt B
    async with sm() as db:
        conf_b = await _create_and_confirm_receipt(
            client,
            headers,
            db,
            "Reliance Fresh",
            [
                {"raw_text": "Another Brand Full Cream Milk 500 ML", "display_name": "Another Brand Full Cream Milk", "qty": "500.000", "unit": "ml", "category": "dairy"},
                {"raw_text": "Aloo 1 KG", "display_name": "Aloo", "qty": "1000.000", "unit": "g", "category": "produce"},
                {"raw_text": "Toned Milk 1 L", "display_name": "Toned Milk", "qty": "1000.000", "unit": "ml", "category": "dairy"},
                {"raw_text": "Desi Tomato 500 G", "display_name": "Desi Tomato", "qty": "500.000", "unit": "g", "category": "produce"},
            ],
            idem_key="receipt-b-idem-1",
        )

    # Confirm auto-mapped mappings via API (simulating user confirmation on review)
    for mapping_out in conf_a["ingredient_mappings"] + conf_b["ingredient_mappings"]:
        if mapping_out["canonical_ingredient_id"] and not mapping_out["user_confirmed"]:
            resp = await client.post(
                "/api/v1/inventory/ingredients/mappings/confirm",
                headers=headers,
                json={
                    "canonical_item_id": mapping_out["canonical_item_id"],
                    "canonical_ingredient_id": mapping_out["canonical_ingredient_id"],
                    "allow_override": False,
                },
            )
            assert resp.status_code == 200

    # Check single pantry summary
    summary = (await client.get("/api/v1/inventory/ingredients/summary", headers=headers)).json()
    by_name = {i["name"]: i for i in summary["ingredients"]}

    # Empty catalogue entries must NOT appear
    assert len(by_name) == 6
    assert Decimal(str(by_name["Full Cream Milk"]["total_remaining_qty"])) == Decimal("1500.000")
    assert Decimal(str(by_name["Toned Milk"]["total_remaining_qty"])) == Decimal("1500.000")
    assert Decimal(str(by_name["Potato"]["total_remaining_qty"])) == Decimal("3000.000")
    assert Decimal(str(by_name["Whole Wheat Flour"]["total_remaining_qty"])) == Decimal("5000.000")
    assert Decimal(str(by_name["Cherry Tomato"]["total_remaining_qty"])) == Decimal("250.000")
    assert Decimal(str(by_name["Desi Tomato"]["total_remaining_qty"])) == Decimal("500.000")

    # Verify Recipe Regression against actual pantry stock
    pantry_payload = [
        {
            "canonical_item_id": str(uuid.uuid4()),
            "family_id": "milk",
            "variant_id": "milk__toned_milk",
            "is_unspecified_variant": False,
            "name": "Toned Milk",
            "qty": 1500.0,
            "base_unit": "ml",
        },
        {
            "canonical_item_id": str(uuid.uuid4()),
            "family_id": "tomato",
            "variant_id": "tomato__desi_tomato",
            "is_unspecified_variant": False,
            "name": "Desi Tomato",
            "qty": 500.0,
            "base_unit": "g",
        },
    ]
    r_milk = reconcile_ingredient({"name": "Milk", "required_qty": "500", "base_unit": "ml"}, pantry_payload)
    assert r_milk is not None and r_milk["status"] == "available"
    r_cherry = reconcile_ingredient({"name": "Cherry Tomato", "required_qty": "100", "base_unit": "g"}, pantry_payload)
    assert r_cherry is not None and r_cherry["status"] == "missing"
    assert classify_recommendation_type([r_milk], [], pantry_payload) == "COOK_NOW"

    # Verify historical non-catalogue record is preserved & sync is idempotent
    async with sm() as db:
        sync_again = await sync_catalogue_to_db(db)
        assert sync_again["created"] == 0
        hist = (
            await db.execute(
                select(CanonicalIngredient).where(
                    CanonicalIngredient.normalized_name == "historical_spice_mix"
                )
            )
        ).scalar_one_or_none()
        assert hist is not None

    await engine.dispose()


def test_phase7_2_1_generic_receipt_and_quantity_normalization() -> None:
    resolver = IngredientResolver()
    # Generic descriptions must NOT guess specific variants
    r_milk = resolver.resolve_text("Milk")
    r_tom = resolver.resolve_text("Tomato")
    assert r_milk.variant_id == "milk__unspecified" and r_milk.is_unspecified_variant is True
    assert r_tom.variant_id == "tomato__unspecified" and r_tom.is_unspecified_variant is True

    # Quantity extraction normalization checks
    assert normalize_metric_unit(1, "L") == (1000.0, "ml")
    assert normalize_metric_unit(500, "ml") == (500.0, "ml")
    assert normalize_metric_unit(2 * 500, "ml") == (1000.0, "ml")
    assert normalize_metric_unit(1, "kg") == (1000.0, "g")
    assert normalize_metric_unit(250, "g") == (250.0, "g")
    assert normalize_metric_unit(1, "dozen") == (12.0, "pcs")
    assert normalize_metric_unit(6, "pcs") == (6.0, "pcs")
    assert normalize_metric_unit(1, "pack") == (1.0, "pcs")
    assert normalize_metric_unit(4, "pcs") == (4.0, "pcs")


@pytest.mark.asyncio
async def test_phase7_2_1_household_learning_conflict_and_isolation(
    client: AsyncClient,
    jwks_provider,
    temp_db_url: str,
) -> None:
    sub_a = f"hh-a-{uuid.uuid4().hex[:8]}"
    sub_b = f"hh-b-{uuid.uuid4().hex[:8]}"
    hdr_a = {"Authorization": f"Bearer {jwks_provider.make_token(sub=sub_a, email=f'{sub_a}@example.com')}"}
    hdr_b = {"Authorization": f"Bearer {jwks_provider.make_token(sub=sub_b, email=f'{sub_b}@example.com')}"}
    hh_a = uuid.UUID((await client.get("/api/v1/auth/me", headers=hdr_a)).json()["active_household"]["id"])
    hh_b = uuid.UUID((await client.get("/api/v1/auth/me", headers=hdr_b)).json()["active_household"]["id"])

    engine = build_async_engine(temp_db_url)
    sm = build_session_maker(engine)
    async with sm() as db:
        await sync_catalogue_to_db(db)
        unknown_item = CanonicalItem(
            household_id=hh_a,
            slug=f"unk-{uuid.uuid4().hex[:6]}",
            name="Xyz Special House Masala",
            category="spices_seasonings",
            default_base_unit="g",
        )
        db.add(unknown_item)
        await db.flush()

        # 1. Unknown requires review & is NOT learned automatically
        res1 = await resolve_or_suggest_mapping(db, household_id=hh_a, canonical_item=unknown_item)
        assert res1.status in ("unmapped", "suggested")
        assert res1.user_confirmed is False

        # 2. User confirms -> saved at household A scope
        garam = (await db.execute(select(CanonicalIngredient).where(CanonicalIngredient.variant_id == "garam_masala__unspecified"))).scalar_one()
        turmeric = (await db.execute(select(CanonicalIngredient).where(CanonicalIngredient.variant_id == "turmeric__unspecified"))).scalar_one()
        await confirm_ingredient_mapping(
            db, household_id=hh_a, canonical_item_id=unknown_item.id, canonical_ingredient_id=garam.id
        )
        await db.commit()

        # 3. Next receipt in HH A resolves automatically; HH B does NOT inherit
        resolver = IngredientResolver()
        next_a = await resolver.resolve_for_household(db, household_id=hh_a, receipt_text="Xyz Special House Masala")
        next_b = await resolver.resolve_for_household(db, household_id=hh_b, receipt_text="Xyz Special House Masala")
        assert next_a.variant_id == "garam_masala__unspecified"
        assert next_a.match_source == "household_learned_alias"
        assert next_b.requires_review is True

        # 4. Conflicting mapping without allow_override does NOT silently override
        with pytest.raises(Exception) as exc_info:
            await confirm_ingredient_mapping(
                db,
                household_id=hh_a,
                canonical_item_id=unknown_item.id,
                canonical_ingredient_id=turmeric.id,
                allow_override=False,
            )
        assert getattr(exc_info.value, "code", None) == "MAPPING_CONFLICT"

    await engine.dispose()
