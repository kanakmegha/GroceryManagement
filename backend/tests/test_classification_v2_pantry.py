"""Tests for Product Classification V2, Subcategory Persistence & Automatic Pantry Eligibility."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.core.security import ClerkClaims, get_current_claims
from app.main import create_app
from app.modules.catalog.classifier import FoodClassificationService


@pytest.fixture
def migrated_db(tmp_path: Path) -> Path:
    from alembic import command
    from alembic.config import Config

    db_path = tmp_path / "cls_v2.db"
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")
    command.upgrade(cfg, "head")
    return db_path


def _setup_app(db_path: Path) -> tuple[Any, dict[str, str], dict[str, str]]:
    settings = Settings(DATABASE_URL=f"sqlite+aiosqlite:///{db_path}", EXPERIMENTAL_OCR_ENABLED=False, EXPERIMENTAL_OCR_PRIMARY=False)
    app = create_app(settings)
    user = {"sub": "v2_u1", "email": "u1@v2.test", "name": "U1"}

    async def _claims() -> ClerkClaims:
        return ClerkClaims(sub=user["sub"], email=user["email"], display_name=user["name"], azp=None, raw={})

    app.dependency_overrides[get_current_claims] = _claims
    return app, {"Authorization": "Bearer t"}, user


def test_classifier_v2_scenarios() -> None:
    # 1. Existing catalogue product
    r1 = FoodClassificationService.classify_item("Amul Taaza Milk 500ml")
    assert r1.pantry_eligible is True
    assert r1.eligibility_reason == "MATCHED_EXISTING_PRODUCT"
    assert r1.matching_status == "matched_existing"

    # 2. Unknown branded product -> separated high classification_conf, low matching_conf, eligible!
    r2 = FoodClassificationService.classify_item("Local Brand Rice Bran Oil 1L")
    assert r2.category == "cooking_essentials"
    assert r2.secondary_category == "cooking_oil"
    assert r2.extracted_brand == "Local Brand"
    assert r2.extracted_pack_size == "1L"
    assert r2.matching_status == "new_product_candidate"
    assert r2.classification_confidence >= 0.80
    assert r2.matching_confidence == 0.0
    assert r2.eligibility_reason == "NEW_PRODUCT_CLASSIFIED"
    assert r2.pantry_eligible is True
    assert r2.needs_review is False

    # 3. Unknown unbranded product
    r3 = FoodClassificationService.classify_item("Refined Rice Bran Oil 1L")
    assert r3.category == "cooking_essentials"
    assert r3.secondary_category == "cooking_oil"
    assert r3.pantry_eligible is True

    # 4. Regional grocery product & spelling/abbreviation variation
    r4 = FoodClassificationService.classify_item("Kaveri Sona Masoori Rice 5kg")
    assert r4.category == "grain"
    assert r4.secondary_category == "rice"
    assert r4.pantry_eligible is True

    r4b = FoodClassificationService.classify_item("Fresh Tom Pkt 500g")
    assert r4b.category == "vegetable"
    assert r4b.pantry_eligible is True

    # 5. Genuinely ambiguous identity -> AMBIGUOUS_PRODUCT, needs_review=True
    r5 = FoodClassificationService.classify_item("Misc Item 42")
    assert r5.pantry_eligible is False
    assert r5.needs_review is True
    assert r5.matching_status == "ambiguous"
    assert r5.eligibility_reason == "AMBIGUOUS_PRODUCT"


@pytest.mark.asyncio
async def test_end_to_end_unknown_branded_subcategory_price_history_and_isolation(migrated_db: Path) -> None:
    app, headers, user = _setup_app(migrated_db)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            me = await client.get("/api/v1/auth/me", headers=headers)
            hh_a = me.json()["households"][0]["id"]
            h_a = {**headers, "X-Household-Id": hh_a}

            # Receipt 1: 5 diverse scenarios (existing catalogue, unknown branded, regional, abbreviation, ambiguous)
            receipt_1 = (
                "SUPER MART\n"
                "Amul Taaza Milk 500ml 1 x 28.00 28.00\n"
                "Local Brand Rice Bran Oil 1L 1 x 165.00 165.00\n"
                "Kaveri Sona Masoori Rice 5kg 1 x 340.00 340.00\n"
                "Fresh Tom Pkt 500g 1 x 30.00 30.00\n"
                "Misc Item 42 1 x 50.00 50.00\n"
                "Total: 613.00\n"
            )
            r_ocr1 = await client.post("/api/v1/purchases/ocr", headers=h_a, json={"raw_text": receipt_1})
            assert r_ocr1.status_code == 201
            d1 = r_ocr1.json()
            assert len(d1["items"]) >= 4

            # Before confirm: inventory is empty
            inv_pre = await client.get("/api/v1/inventory/lots", headers=h_a)
            assert inv_pre.json()["items"] == []

            # Confirm draft 1 -> 4 clear grocery items enter pantry; ambiguous item (if parsed) is excluded
            c1 = await client.post(f"/api/v1/purchases/drafts/{d1['id']}/confirm", headers={**h_a, "Idempotency-Key": "k-v2-1"}, json={})
            assert c1.status_code == 200
            assert len(c1.json()["created_lots"]) == 4

            # Re-confirm idempotency -> 409, no duplicate lots
            c1_dup = await client.post(f"/api/v1/purchases/drafts/{d1['id']}/confirm", headers={**h_a, "Idempotency-Key": "k-v2-1"}, json={})
            assert c1_dup.status_code == 409

            # Verify Pantry API includes subcategory (secondary_category) on unknown branded Rice Bran Oil
            summary = (await client.get("/api/v1/inventory/summary", headers=h_a)).json()["items"]
            oil_item = next(x for x in summary if "rice bran oil" in x["name"].lower())
            assert oil_item["category"] == "cooking_essentials"
            assert oil_item["secondary_category"] == "cooking_oil"

            # Receipt 2: Same unknown branded product purchased at a different price + different pack size (2L)
            # Uses existing alias learned from confirmed Receipt 1, prevents duplicate canonical item, preserves 2 price history rows!
            receipt_2 = (
                "KIRANA BAZAAR\n"
                "Local Brand Rice Bran Oil 2L 1 x 310.00 310.00\n"
                "Total: 310.00\n"
            )
            r_ocr2 = await client.post("/api/v1/purchases/ocr", headers=h_a, json={"raw_text": receipt_2})
            d2 = r_ocr2.json()
            c2 = await client.post(f"/api/v1/purchases/drafts/{d2['id']}/confirm", headers={**h_a, "Idempotency-Key": "k-v2-2"}, json={})
            assert c2.status_code == 200

            # Price history for Sunflower Oil has 2 distinct historical entries (165.00/L and 155.00/L)
            ph = (await client.get(f"/api/v1/inventory/items/{oil_item['canonical_item_id']}/price-history", headers=h_a)).json()
            assert len(ph["entries"]) == 2
            assert ph["latest_price_paise_per_std"] != ph["entries"][1]["normalized_std_price_paise"]

            # Household isolation: User B sees 0 pantry items and 404 on User A's price history
            user.update({"sub": "v2_u2", "email": "u2@v2.test", "name": "U2"})
            me_b = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer t"})
            hh_b = me_b.json()["households"][0]["id"]
            h_b = {"Authorization": "Bearer t", "X-Household-Id": hh_b}
            assert (await client.get("/api/v1/inventory/summary", headers=h_b)).json()["items"] == []
            assert (await client.get(f"/api/v1/inventory/items/{oil_item['canonical_item_id']}/price-history", headers=h_b)).status_code == 404
