from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.modules.auth.models import Household, User
from app.modules.catalog.ingredient_identity_service import (
    are_ingredients_incompatible,
    resolve_ingredient_identity,
)
from app.modules.catalog.ingredient_mapping_service import (
    confirm_ingredient_mapping,
    resolve_or_suggest_mapping,
)
from app.modules.catalog.ingredient_merge_service import (
    merge_canonical_ingredients,
    rollback_canonical_ingredient_merge,
)
from app.modules.inventory.models import (
    CanonicalIngredient,
    CanonicalItem,
    IngredientMapping,
    InventoryLot,
)

# 30 equivalent ingredient-description pairs: (input_text, expected_normalized_name_in_seed)
EQUIVALENT_PAIRS: list[tuple[str, str]] = [
    ("Tomato", "tomato"),
    ("Fresh Tomato", "tomato"),
    ("Fresh Red Tomatoes 1kg", "tomato"),
    ("Tamatar", "tomato"),
    ("Onion", "onion"),
    ("Red Onions 2kg", "onion"),
    ("Pyaz", "onion"),
    ("Potato", "potato"),
    ("Fresh Potatoes 1kg", "potato"),
    ("Aloo", "potato"),
    ("Cumin Seeds", "cumin_seeds"),
    ("Jeera", "cumin_seeds"),
    ("Semolina", "semolina"),
    ("Rava", "semolina"),
    ("Sooji", "semolina"),
    ("Badam", "almonds"),
    ("Almond", "almonds"),
    ("Palak", "spinach"),
    ("Fresh Spinach 250g", "spinach"),
    ("Dahi", "curd"),
    ("Curd", "curd"),
    ("Yogurt", "curd"),
    ("Whole Wheat Flour", "whole_wheat_flour"),
    ("Whole Wheat Atta", "whole_wheat_flour"),
    ("Atta 5kg", "whole_wheat_flour"),
    ("Arhar Dal", "toor_dal"),
    ("Tur Dal", "toor_dal"),
    ("Haldi", "turmeric_powder"),
    ("Rai", "mustard_seeds"),
    ("Til", "sesame_seeds"),
]

# 20 multilingual / transliterated equivalents: (input_text, expected_normalized_name)
TRANSLITERATED_PAIRS: list[tuple[str, str]] = [
    ("Tamatar 500g", "tomato"),
    ("Pyaz 1kg", "onion"),
    ("Kanda 1kg", "onion"),
    ("Aloo 2kg", "potato"),
    ("Batata 1kg", "potato"),
    ("Palak", "spinach"),
    ("Haldi", "turmeric_powder"),
    ("Jeera", "cumin_seeds"),
    ("Rai", "mustard_seeds"),
    ("Til", "sesame_seeds"),
    ("Badam", "almonds"),
    ("Kaju", "cashews"),
    ("Dahi", "curd"),
    ("Doodh", "milk"),
    ("Atta", "whole_wheat_flour"),
    ("Rava", "semolina"),
    ("Sooji", "semolina"),
    ("Suji", "semolina"),
    ("Arhar Dal", "toor_dal"),
    ("Kabuli Chana", "chickpeas"),
]

# 20 OCR-corrupted descriptions: (input_text, expected_normalized_name)
OCR_CORRUPTED_CASES: list[tuple[str, str]] = [
    ('" Fresh Red Tomatoes!! 1kg', "tomato"),
    ("1 * Cumin Seeds (Jeera) 100g", "cumin_seeds"),
    ("• Whole Wheat Atta 5kg", "whole_wheat_flour"),
    ('" Badam 500 g.', "almonds"),
    ("1 Arhar Dal 1 KG", "toor_dal"),
    ("T0mat0 500g", "tomato"),
    ("TURMERIC P0WDER 200g", "turmeric_powder"),
    ("CHANA D4L 1kg", "chana_dal"),
    ("1 * ONI0N 2kg", "onion"),
    ("P0TAT0 1kg", "potato"),
    ("• PALAK 250g", "spinach"),
    ("S0NA MAS00RI RICE 5kg", "sona_masoori_rice"),
    ("BASMAT1 RICE 5kg", "basmati_rice"),
    ("SUNFL0WER 0IL 1L", "sunflower_oil"),
    ("MUSTARD 01L 1L", "mustard_oil"),
    ("C0R1ANDER P0WDER 200g", "coriander_powder"),
    ("GARAM MASALA.", "garam_masala"),
    ("1 PEANUTS 500g", "peanuts"),
    ('" CASHEWS 250g', "cashews"),
    ("FLAX SEED5 200g", "flax_seeds"),
]

# 20 distinct-ingredient negative pairs: (norm_a, norm_b)
DISTINCT_NEGATIVE_PAIRS: list[tuple[str, str]] = [
    ("basmati_rice", "idli_rice"),
    ("basmati_rice", "sona_masoori_rice"),
    ("idli_rice", "sona_masoori_rice"),
    ("rice_flour", "basmati_rice"),
    ("rice_flour", "idli_rice"),
    ("rice_flour", "sona_masoori_rice"),
    ("mustard_oil", "sunflower_oil"),
    ("coconut_oil", "sunflower_oil"),
    ("tomato", "tomato_puree"),
    ("milk", "coconut_milk"),
    ("whole_wheat_flour", "rice_flour"),
    ("toor_dal", "moong_dal"),
    ("toor_dal", "chana_dal"),
    ("chana_dal", "urad_dal"),
    ("moong_dal", "urad_dal"),
    ("almonds", "cashews"),
    ("onion", "garlic"),
    ("ginger", "turmeric_powder"),
    ("cumin_seeds", "mustard_seeds"),
    ("apple", "banana"),
]

# 15 processing/form distinction cases: (input_a, input_b) that must NOT auto-match as same
FORM_DISTINCTION_PAIRS: list[tuple[str, str]] = [
    ("Whole Urad", "Split Urad"),
    ("Whole Moong", "Split Yellow Moong"),
    ("Whole Coriander", "Coriander Powder"),
    ("Fresh Ginger", "Ginger Powder"),
    ("Tomato", "Tomato Puree"),
    ("Tomato", "Tomato Paste"),
    ("Milk", "Milk Powder"),
    ("Coconut", "Coconut Milk"),
    ("Coconut", "Coconut Oil"),
    ("Basmati Rice", "Rice Flour"),
    ("Idli Rice", "Rice Flour"),
    ("Whole Wheat", "Whole Wheat Flour"),
    ("Mustard Seeds", "Mustard Oil"),
    ("Groundnut", "Peanut Butter"),
    ("Garlic", "Garlic Paste"),
]

# 15 brand/product-to-ingredient mapping examples: (product_name, category, base_unit,
# expected_ing_norm)
BRAND_PRODUCT_CASES: list[tuple[str, str, str, str]] = [
    ("Aavin Full Cream Milk 500ml", "dairy", "ml", "milk"),
    ("Amul Gold Milk 1L", "dairy", "ml", "milk"),
    ("Nandini Toned Milk 500ml", "dairy", "ml", "milk"),
    ("India Gate Basmati Rice 5kg", "grain", "g", "basmati_rice"),
    ("Daawat Basmati Rice 1kg", "grain", "g", "basmati_rice"),
    ("Udhaiyam Urad Dal 1kg", "pulse", "g", "urad_dal"),
    ("Tata Sampann Toor Dal 1kg", "pulse", "g", "toor_dal"),
    ("Tata Sampann Chana Dal 1kg", "pulse", "g", "chana_dal"),
    ("Fortune Sunlite Sunflower Oil 1L", "oil", "ml", "sunflower_oil"),
    ("Dhara Kachi Ghani Mustard Oil 1L", "oil", "ml", "mustard_oil"),
    ("Aashirvaad Whole Wheat Atta 5kg", "grain", "g", "whole_wheat_flour"),
    ("Everest Turmeric Powder 200g", "spice", "g", "turmeric_powder"),
    ("MDH Garam Masala 100g", "spice", "g", "garam_masala"),
    ("Amul Butter 500g", "dairy", "g", "butter"),
    ("Mother Dairy Paneer 200g", "dairy", "g", "paneer"),
]


async def test_phase4b_evaluation_dataset_metrics(temp_db_url: str) -> None:
    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        ings = (await db.execute(select(CanonicalIngredient))).scalars().all()
        id_to_norm = {ing.id: ing.normalized_name for ing in ings}

        # 1. Evaluate 30 equivalent pairs + 20 transliterated pairs + 20 OCR-corrupted cases
        eq_hits = 0
        for raw, exp_norm in EQUIVALENT_PAIRS:
            res = await resolve_ingredient_identity(db, raw_name=raw)
            actual = (
                id_to_norm.get(res.matched_ingredient_id) if res.matched_ingredient_id else None
            )
            if res.tier == "high" and actual == exp_norm:
                eq_hits += 1

        trans_hits = 0
        for raw, exp_norm in TRANSLITERATED_PAIRS:
            res = await resolve_ingredient_identity(db, raw_name=raw)
            actual = (
                id_to_norm.get(res.matched_ingredient_id) if res.matched_ingredient_id else None
            )
            if res.tier == "high" and actual == exp_norm:
                trans_hits += 1

        ocr_hits = 0
        ocr_review = 0
        for raw, exp_norm in OCR_CORRUPTED_CASES:
            res = await resolve_ingredient_identity(db, raw_name=raw)
            actual = (
                id_to_norm.get(res.matched_ingredient_id) if res.matched_ingredient_id else None
            )
            if res.tier == "high" and actual == exp_norm:
                ocr_hits += 1
            elif res.tier == "medium":
                ocr_review += 1

        # 2. Evaluate 20 distinct-ingredient negative pairs -> 0 false-positive merges
        distinct_fp = 0
        for norm_a, norm_b in DISTINCT_NEGATIVE_PAIRS:
            if not are_ingredients_incompatible(norm_a, norm_b):
                # Check if resolve_ingredient_identity would ever conflate them
                res_a = await resolve_ingredient_identity(db, raw_name=norm_a.replace("_", " "))
                res_b = await resolve_ingredient_identity(db, raw_name=norm_b.replace("_", " "))
                if (
                    res_a.tier == "high"
                    and res_b.tier == "high"
                    and res_a.matched_ingredient_id == res_b.matched_ingredient_id
                ):
                    distinct_fp += 1

        # 3. Evaluate 15 form-distinction pairs -> 0 false-positive HIGH conflations
        form_fp = 0
        for raw_a, raw_b in FORM_DISTINCTION_PAIRS:
            ra = await resolve_ingredient_identity(db, raw_name=raw_a)
            rb = await resolve_ingredient_identity(db, raw_name=raw_b)
            if (
                ra.tier == "high"
                and rb.tier == "high"
                and ra.matched_ingredient_id == rb.matched_ingredient_id
            ):
                form_fp += 1

        # 4. Evaluate 15 brand/product-to-ingredient mapping examples
        brand_hits = 0
        for idx, (pname, cat, unit, exp_norm) in enumerate(BRAND_PRODUCT_CASES):
            ci = CanonicalItem(
                household_id=None,
                slug=f"eval-brand-{idx}",
                name=pname,
                category=cat,
                default_base_unit=unit,
            )
            db.add(ci)
            await db.flush()
            mres = await resolve_or_suggest_mapping(
                db, household_id=None, canonical_item=ci, auto_map_high_confidence=False
            )
            if (
                mres.tier == "high"
                and mres.suggested_ingredient
                and mres.suggested_ingredient.normalized_name == exp_norm
            ):
                brand_hits += 1

        print(
            f"EVAL_METRICS | eq={eq_hits}/30 | trans={trans_hits}/20 | "
            f"ocr_high={ocr_hits}/20 ocr_med={ocr_review}/20 | "
            f"distinct_fp={distinct_fp}/20 | form_fp={form_fp}/15 | brand={brand_hits}/15"
        )
        assert eq_hits == 30
        assert trans_hits == 20
        assert distinct_fp == 0
        assert form_fp == 0
        assert brand_hits >= 14  # Udhaiyam Urad Dal / Mother Dairy Paneer etc.

    await engine.dispose()


async def test_phase4b_merge_rollback_and_conflict_safety(temp_db_url: str) -> None:
    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        u1 = User(clerk_user_id="clerk_4b_1", email="4b1@example.com", display_name="4B1")
        u2 = User(clerk_user_id="clerk_4b_2", email="4b2@example.com", display_name="4B2")
        db.add_all([u1, u2])
        await db.flush()
        hh1 = Household(name="HH1", created_by=u1.id)
        hh2 = Household(name="HH2", created_by=u2.id)
        db.add_all([hh1, hh2])
        await db.flush()

        surv = (
            await db.execute(
                select(CanonicalIngredient).where(CanonicalIngredient.normalized_name == "tomato")
            )
        ).scalar_one()
        dup = CanonicalIngredient(
            name="Tamatar",
            normalized_name="tamatar_dup",
            category="vegetable",
            subcategory="nightshade",
            standard_unit="g",
            cooking_variants=[],
            supported_units=["g", "kg"],
        )
        db.add(dup)
        await db.flush()

        p1 = CanonicalItem(
            household_id=hh1.id,
            slug="p1-tom",
            name="Local Tamatar 1kg",
            category="vegetable",
            default_base_unit="g",
        )
        p2 = CanonicalItem(
            household_id=hh2.id,
            slug="p2-tom",
            name="Farm Tomato 1kg",
            category="vegetable",
            default_base_unit="g",
        )
        db.add_all([p1, p2])
        await db.flush()

        lot1 = InventoryLot(
            household_id=hh1.id,
            canonical_item_id=p1.id,
            initial_qty=Decimal("1000.000"),
            remaining_qty=Decimal("1000.000"),
            base_unit="g",
            unit_cost_paise=3000,
        )
        db.add(lot1)
        await db.flush()

        await confirm_ingredient_mapping(
            db, household_id=hh1.id, canonical_item_id=p1.id, canonical_ingredient_id=dup.id
        )
        await confirm_ingredient_mapping(
            db, household_id=hh2.id, canonical_item_id=p2.id, canonical_ingredient_id=surv.id
        )
        await db.commit()

        rec = await merge_canonical_ingredients(db, survivor_id=surv.id, duplicate_id=dup.id)
        await db.commit()
        assert (await db.get(InventoryLot, lot1.id)).remaining_qty == Decimal("1000.000")
        assert (await db.get(CanonicalItem, p1.id)).is_active is True

        await rollback_canonical_ingredient_merge(db, record=rec)
        await db.commit()
        assert (await db.get(CanonicalIngredient, dup.id)).is_active is True
        m_restored = (
            await db.execute(
                select(IngredientMapping).where(IngredientMapping.household_id == hh1.id)
            )
        ).scalar_one()
        assert m_restored.canonical_ingredient_id == dup.id

    await engine.dispose()
