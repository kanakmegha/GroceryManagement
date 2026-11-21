from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.modules.auth.models import Household, User
from app.modules.catalog.product_identity_service import (
    detect_duplicate_product_candidates,
    learn_confirmed_product_alias,
    resolve_product_identity,
)
from app.modules.inventory.models import CanonicalItem, InventoryLot
from app.modules.purchases.models import PurchaseItem
from tests.conftest import EphemeralJWKSProvider


async def test_product_identity_resolution_rules_and_isolation(temp_db_url: str) -> None:
    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        u1 = User(clerk_user_id="clerk_3a_1", email="3a1@example.com", display_name="3A1")
        u2 = User(clerk_user_id="clerk_3a_2", email="3a2@example.com", display_name="3A2")
        db.add_all([u1, u2])
        await db.flush()
        hh1 = Household(name="HH1", created_by=u1.id)
        hh2 = Household(name="HH2", created_by=u2.id)
        db.add_all([hh1, hh2])
        await db.flush()

        chini = CanonicalItem(
            household_id=hh1.id,
            slug="chini-df",
            name="CHINI DF",
            category="general",
            default_base_unit="g",
        )
        turmeric = CanonicalItem(
            household_id=hh1.id,
            slug="turmeric-powder",
            name="Turmeric Powder",
            category="spice",
            default_base_unit="g",
        )
        amul_fc_500 = CanonicalItem(
            household_id=hh1.id,
            slug="amul-full-cream-milk-500ml",
            name="Amul Full Cream Milk 500ml",
            category="dairy",
            default_base_unit="ml",
        )
        basmati = CanonicalItem(
            household_id=hh1.id,
            slug="india-gate-basmati-rice-5kg",
            name="India Gate Basmati Rice 5kg",
            category="grain",
            default_base_unit="g",
        )
        # Duplicate candidate for detection test (not merged)
        chini_dup = CanonicalItem(
            household_id=hh1.id,
            slug="1-chinldf",
            name="1 CHINLDF",
            category="general",
            default_base_unit="g",
        )
        db.add_all([chini, turmeric, amul_fc_500, basmati, chini_dup])
        await db.commit()

        # 1. Punctuation & OCR character substitution -> HIGH match to CHINI DF
        r_punct = await resolve_product_identity(
            db, household_id=hh1.id, raw_description='" CHINIDF. 1 KG 47 47.00', base_unit="g"
        )
        assert r_punct.outcome == "match_existing"
        assert r_punct.tier == "high"
        assert r_punct.resolved_canonical_item_id in {chini.id, chini_dup.id}

        r_ocr = await resolve_product_identity(
            db, household_id=hh1.id, raw_description="1 CHINLDF 2 KG 53 106.00", base_unit="g"
        )
        assert r_ocr.outcome == "match_existing"
        assert r_ocr.tier == "high"

        # 2. Abbreviation expansion ("Turmeric Pdr" -> "Turmeric Powder")
        r_abbr = await resolve_product_identity(
            db, household_id=hh1.id, raw_description="Turmeric Pdr 200g", base_unit="g"
        )
        assert r_abbr.outcome == "match_existing"
        assert r_abbr.resolved_canonical_item_id == turmeric.id

        # 3. Different package size ("Amul Full Cream Milk 1L" vs "500ml") -> review_required, NOT auto-merged
        r_pack = await resolve_product_identity(
            db, household_id=hh1.id, raw_description="Amul Full Cream Milk 1L", base_unit="ml"
        )
        assert r_pack.outcome == "review_required"
        assert r_pack.tier == "medium"

        # 4. Different brand ("Aavin Full Cream Milk 500ml" vs "Amul ...") -> create_new
        r_brand = await resolve_product_identity(
            db, household_id=hh1.id, raw_description="Aavin Full Cream Milk 500ml", base_unit="ml"
        )
        assert r_brand.outcome == "create_new"

        # 5. Different cooking variant ("Amul Toned Milk 500ml" vs "Full Cream"; "Idli Rice" vs "Basmati")
        r_var_milk = await resolve_product_identity(
            db, household_id=hh1.id, raw_description="Amul Toned Milk 500ml", base_unit="ml"
        )
        assert r_var_milk.outcome == "create_new"
        r_var_rice = await resolve_product_identity(
            db, household_id=hh1.id, raw_description="India Gate Idli Rice 5kg", base_unit="g"
        )
        assert r_var_rice.outcome == "create_new"

        # 6. Ambiguous/fuzzy description never triggers HIGH auto-merge
        r_fuzzy = await resolve_product_identity(
            db, household_id=hh1.id, raw_description="Organic Turmeric Mix", base_unit="g"
        )
        assert r_fuzzy.outcome != "match_existing"

        # 7. Learn confirmed alias & household isolation
        await learn_confirmed_product_alias(
            db,
            household_id=hh1.id,
            canonical_item_id=chini.id,
            raw_description="CHINI DESI FINE 1KG",
        )
        await db.commit()

        r_alias_hh1 = await resolve_product_identity(
            db, household_id=hh1.id, raw_description="CHINI DESI FINE 1KG", base_unit="g"
        )
        assert r_alias_hh1.outcome == "match_existing"
        assert r_alias_hh1.resolution_method == "approved_alias"
        assert r_alias_hh1.resolved_canonical_item_id == chini.id

        r_alias_hh2 = await resolve_product_identity(
            db, household_id=hh2.id, raw_description="CHINI DESI FINE 1KG", base_unit="g"
        )
        assert r_alias_hh2.outcome == "create_new"

        # 8. No inventory mutation during resolution
        assert (await db.execute(select(InventoryLot))).scalars().all() == []

        # 9. Duplicate candidate detection clusters chini + chini_dup without merging
        clusters = await detect_duplicate_product_candidates(db, household_id=hh1.id)
        chini_clusters = [c for c in clusters if c.cluster_key == "chini df"]
        assert len(chini_clusters) == 1
        assert {it.canonical_item_id for it in chini_clusters[0].items} == {chini.id, chini_dup.id}

    await engine.dispose()


async def test_receipt_confirmation_reuses_product_and_preserves_provenance(
    client: httpx.AsyncClient,
    jwks_provider: EphemeralJWKSProvider,
    temp_db_url: str,
) -> None:
    token = jwks_provider.make_token(sub="user_3a_api", email="3aapi@example.com", name="3AAPI")
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 200

    # Purchase 1: "Turmeric Powder"
    p1 = await client.post(
        "/api/v1/purchases",
        headers=headers,
        json={
            "merchant_name": "Aggarwal Traders",
            "purchased_on": "2026-11-19",
            "items": [
                {
                    "raw_text": "1 Turmeric Powder 2 KG 67 134.00",
                    "display_name": "Turmeric Powder",
                    "qty": 2,
                    "unit": "kg",
                    "unit_price_paise": 6700,
                    "line_total_paise": 13400,
                    "category": "spice",
                }
            ],
        },
    )
    assert p1.status_code == 201
    c1 = await client.post(f"/api/v1/purchases/{p1.json()['id']}/confirm", headers=headers, json={})
    assert c1.status_code == 200
    ci_id_1 = c1.json()["created_lots"][0]["canonical_item_id"]

    # Purchase 2: Punctuation + leading line-number OCR variant '" Turmeric Powder.' -> resolves to SAME CanonicalItem ID!
    p2 = await client.post(
        "/api/v1/purchases",
        headers=headers,
        json={
            "merchant_name": "Aggarwal Traders",
            "purchased_on": "2026-11-20",
            "items": [
                {
                    "raw_text": '" Turmeric Powder. 1 KG 68 68.00',
                    "display_name": '" Turmeric Powder.',
                    "qty": 1,
                    "unit": "kg",
                    "unit_price_paise": 6800,
                    "line_total_paise": 6800,
                    "category": "spice",
                }
            ],
        },
    )
    assert p2.status_code == 201
    c2 = await client.post(f"/api/v1/purchases/{p2.json()['id']}/confirm", headers=headers, json={})
    assert c2.status_code == 200
    ci_id_2 = c2.json()["created_lots"][0]["canonical_item_id"]
    assert ci_id_2 == ci_id_1

    # Pantry shows 1 consolidated product with 3000g total
    inv = await client.get("/api/v1/inventory/summary", headers=headers)
    assert inv.status_code == 200
    assert len(inv.json()["items"]) == 1
    assert Decimal(inv.json()["items"][0]["total_remaining_qty"]) == Decimal("3000.000")

    # Verify raw_text & provenance preserved on second PurchaseItem
    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        pis = (await db.execute(select(PurchaseItem))).scalars().all()
        second_pi = [pi for pi in pis if pi.display_name == '" Turmeric Powder.'][0]
        assert second_pi.raw_text == '" Turmeric Powder. 1 KG 68 68.00'
        assert second_pi.resolution_method in {"approved_alias", "normalized_match", "user_selected"}
        assert second_pi.resolution_status in {"auto_matched", "user_confirmed"}
        assert second_pi.resolution_score is not None
    await engine.dispose()
