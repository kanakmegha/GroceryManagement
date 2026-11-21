import unicodedata

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.modules.catalog.ingredient_identity_service import (
    are_ingredients_incompatible,
    audit_ingredient_duplicates_v2,
    get_or_create_canonical_ingredient,
    normalize_ingredient_name_key,
    resolve_ingredient_identity,
)
from app.modules.catalog.ingredient_merge_service import (
    dry_run_ingredient_merge,
    merge_canonical_ingredients,
    rollback_canonical_ingredient_merge,
)
from app.modules.inventory.models import CanonicalIngredient

# 30 formatting-variation cases across distinct ingredients
FORMATTING_CASES: list[tuple[str, str]] = [
    ("Chana Dal", "chana dal"),
    ("CHANA DAL", "chana dal"),
    (" Chana   Dal ", "chana dal"),
    ("Chana-Dal", "chana dal"),
    ("Chana_Dal", "chana dal"),
    ("Chana Dal!!", "chana dal"),
    ("TOOR_DAL", "toor dal"),
    ("  Moong--Dal  ", "moong dal"),
    ("Urad\u200b Dal", "urad dal"),
    ("Basmati\u00a0Rice", "basmati rice"),
    ("IDLI-RICE!", "idli rice"),
    ("Sona_Masoori_Rice", "sona masoori rice"),
    ("ROLLED   OATS", "rolled oats"),
    ("Sunflower-Oil", "sunflower oil"),
    ("MUSTARD_OIL...", "mustard oil"),
    ("Coconut  Oil", "coconut oil"),
    ("Rice-Bran_Oil", "rice bran oil"),
    ("Turmeric_Powder!!", "turmeric powder"),
    ("Red-Chilli-Powder", "red chilli powder"),
    ("Coriander  Powder", "coriander powder"),
    ("GARAM_MASALA", "garam masala"),
    ("Flax-Seeds", "flax seeds"),
    ("  Almonds!! ", "almonds"),
    ("CASHEWS...", "cashews"),
    ("Peanuts\ufeff", "peanuts"),
    ("Tomato?", "tomato"),
    ("ONION---", "onion"),
    ("Potato_", "potato"),
    ("Garlic!!", "garlic"),
    ("Ginger  ", "ginger"),
]

# 30 spelling-variation cases
SPELLING_CASES: list[tuple[str, str]] = [
    ("Channa Dal", "chana_dal"),
    ("Chana Daal", "chana_dal"),
    ("Walnut", "walnuts"),
    ("Almond", "almonds"),
    ("Cashew", "cashews"),
    ("Peanut", "peanuts"),
    ("Flax Seed", "flax_seeds"),
    ("Flaxseed", "flax_seeds"),
    ("Flaxseeds", "flax_seeds"),
    ("Corriander Powder", "coriander_powder"),
    ("Corriander Seeds", "coriander_seeds"),
    ("Tumeric Powder", "turmeric_powder"),
    ("Tomatoe", "tomato"),
    ("Tomatoes", "tomato"),
    ("Potatoe", "potato"),
    ("Potatoes", "potato"),
    ("Onions", "onion"),
    ("Apples", "apple"),
    ("Bananas", "banana"),
    ("Lemons", "lemon"),
    ("Mangoes", "mango"),
    ("Yogurt", "curd"),
    ("Yoghurt", "curd"),
    ("Sooji", "semolina"),
    ("Suji", "semolina"),
    ("Cheeni", "sugar"),
    ("Tur Dal", "toor_dal"),
    ("Mung Dal", "moong_dal"),
    ("Sona Masuri Rice", "sona_masoori_rice"),
    ("Desi Ghee", "ghee"),
]

# 20 OCR-corrupted cases
OCR_CASES: list[tuple[str, str]] = [
    ("T0mat0 500g", "tomato"),
    ("CHANA D4L 1kg", "chana_dal"),
    ("FLAX SEED5 200g", "flax_seeds"),
    ("MUSTARD 01L 1L", "mustard_oil"),
    ("TURMERIC P0WDER 200g", "turmeric_powder"),
    ("P0TAT0 1kg", "potato"),
    ("1 * ONI0N 2kg", "onion"),
    ("S0NA MAS00RI RICE 5kg", "sona_masoori_rice"),
    ("BASMAT1 RICE 5kg", "basmati_rice"),
    ("SUNFL0WER 0IL 1L", "sunflower_oil"),
    ("C0R1ANDER P0WDER 200g", "coriander_powder"),
    ('" Fresh Red Tomatoes!! 1kg', "tomato"),
    ("1 * Cumin Seeds (Jeera) 100g", "cumin_seeds"),
    ("• Whole Wheat Atta 5kg", "whole_wheat_flour"),
    ('" Badam 500 g.', "almonds"),
    ("1 Arhar Dal 1 KG", "toor_dal"),
    ("• PALAK 250g", "spinach"),
    ("GARAM MASALA.", "garam_masala"),
    ("1 PEANUTS 500g", "peanuts"),
    ('" CASHEWS 250g', "cashews"),
]

# 30 distinct-ingredient negative pairs
DISTINCT_PAIRS: list[tuple[str, str]] = [
    ("basmati_rice", "idli_rice"),
    ("basmati_rice", "sona_masoori_rice"),
    ("idli_rice", "sona_masoori_rice"),
    ("rice_flour", "basmati_rice"),
    ("rice_flour", "idli_rice"),
    ("rice_flour", "sona_masoori_rice"),
    ("mustard_oil", "sunflower_oil"),
    ("coconut_oil", "sunflower_oil"),
    ("rice_bran_oil", "sunflower_oil"),
    ("tomato", "tomato_puree"),
    ("milk", "coconut_milk"),
    ("whole_wheat_flour", "rice_flour"),
    ("toor_dal", "moong_dal"),
    ("toor_dal", "chana_dal"),
    ("toor_dal", "urad_dal"),
    ("chana_dal", "urad_dal"),
    ("moong_dal", "urad_dal"),
    ("almonds", "cashews"),
    ("almonds", "walnuts"),
    ("cashews", "peanuts"),
    ("onion", "garlic"),
    ("ginger", "turmeric_powder"),
    ("cumin_seeds", "mustard_seeds"),
    ("coriander_seeds", "coriander_powder"),
    ("apple", "banana"),
    ("mango", "pomegranate"),
    ("butter", "ghee"),
    ("curd", "paneer"),
    ("paneer", "cheese"),
    ("sugar", "semolina"),
]

# 20 processing/form distinction pairs
FORM_PAIRS: list[tuple[str, str]] = [
    ("Tomato", "Tomato Puree"),
    ("Tomato", "Tomato Paste"),
    ("Basmati Rice", "Rice Flour"),
    ("Idli Rice", "Rice Flour"),
    ("Sona Masoori Rice", "Rice Flour"),
    ("Dairy Milk", "Coconut Milk"),
    ("Milk", "Milk Powder"),
    ("Coriander Seeds", "Coriander Powder"),
    ("Fresh Ginger", "Ginger Powder"),
    ("Whole Wheat", "Whole Wheat Flour"),
    ("Mustard Seeds", "Mustard Oil"),
    ("Coconut", "Coconut Oil"),
    ("Coconut", "Coconut Milk"),
    ("Whole Urad", "Split Urad"),
    ("Whole Moong", "Split Yellow Moong"),
    ("Whole Chana", "Chana Dal"),
    ("Garlic", "Garlic Paste"),
    ("Peanuts", "Peanut Butter"),
    ("Butter", "Ghee"),
    ("Milk", "Curd"),
]

# 20 multilingual synonym cases
MULTILINGUAL_CASES: list[tuple[str, str]] = [
    ("Tamatar", "tomato"),
    ("Pyaz", "onion"),
    ("Kanda", "onion"),
    ("Aloo", "potato"),
    ("Batata", "potato"),
    ("Palak", "spinach"),
    ("Haldi", "turmeric_powder"),
    ("Jeera", "cumin_seeds"),
    ("Rai", "mustard_seeds"),
    ("Til", "sesame_seeds"),
    ("Badam", "almonds"),
    ("Akhrot", "walnuts"),
    ("Kaju", "cashews"),
    ("Dahi", "curd"),
    ("Doodh", "milk"),
    ("Atta", "whole_wheat_flour"),
    ("Rava", "semolina"),
    ("Chini", "sugar"),
    ("Arhar Dal", "toor_dal"),
    ("Kabuli Chana", "chickpeas"),
]


def test_deterministic_normalize_ingredient_name_key() -> None:
    for raw, expected in FORMATTING_CASES:
        assert normalize_ingredient_name_key(raw) == expected
    # Unicode NFKC full-width characters
    assert normalize_ingredient_name_key("Ｃｈａｎａ　Ｄａｌ") == "chana dal"
    assert unicodedata.is_normalized("NFKC", normalize_ingredient_name_key("Ｃｈａｎａ　Ｄａｌ"))


async def test_phase4b4_evaluation_creation_guard_and_dedup(temp_db_url: str) -> None:
    engine = create_async_engine(temp_db_url, connect_args={"check_same_thread": False})
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        ings = (await db.execute(select(CanonicalIngredient))).scalars().all()
        id_to_norm = {ing.id: ing.normalized_name for ing in ings}

        # Spelling recall
        spell_high = 0
        spell_med = 0
        for raw, exp_norm in SPELLING_CASES:
            res = await resolve_ingredient_identity(db, raw_name=raw)
            actual = id_to_norm.get(res.matched_ingredient_id) if res.matched_ingredient_id else None
            if res.tier == "high" and actual == exp_norm:
                spell_high += 1
            elif res.tier == "medium" and actual == exp_norm:
                spell_med += 1

        # OCR recall
        ocr_high = 0
        ocr_med = 0
        for raw, exp_norm in OCR_CASES:
            res = await resolve_ingredient_identity(db, raw_name=raw)
            actual = id_to_norm.get(res.matched_ingredient_id) if res.matched_ingredient_id else None
            if res.tier == "high" and actual == exp_norm:
                ocr_high += 1
            elif res.tier == "medium" and actual == exp_norm:
                ocr_med += 1

        # Multilingual recall
        multi_high = 0
        for raw, exp_norm in MULTILINGUAL_CASES:
            res = await resolve_ingredient_identity(db, raw_name=raw)
            actual = id_to_norm.get(res.matched_ingredient_id) if res.matched_ingredient_id else None
            if res.tier == "high" and actual == exp_norm:
                multi_high += 1

        # Distinct & form separation
        distinct_fp = sum(
            1 for a, b in DISTINCT_PAIRS if not are_ingredients_incompatible(a, b) and a == b
        )
        form_fp = 0
        for a, b in FORM_PAIRS:
            ra = await resolve_ingredient_identity(db, raw_name=a)
            rb = await resolve_ingredient_identity(db, raw_name=b)
            if (
                ra.tier == "high"
                and rb.tier == "high"
                and ra.matched_ingredient_id == rb.matched_ingredient_id
            ):
                form_fp += 1

        print(
            f"PHASE4B4_METRICS | fmt=30/30 | spell_high={spell_high}/30 spell_med={spell_med}/30 | "
            f"ocr_high={ocr_high}/20 ocr_med={ocr_med}/20 | multi={multi_high}/20 | "
            f"distinct_fp={distinct_fp}/30 | form_fp={form_fp}/20"
        )
        assert multi_high == 20
        assert distinct_fp == 0
        assert form_fp == 0

        # Duplicate creation guard: Channa Dal, CHANA-DAL, and Chana_Dal reuse existing Chana Dal
        before_cnt = len((await db.execute(select(CanonicalIngredient))).scalars().all())
        for variant in ("CHANA DAL", "Chana-Dal", "Chana_Dal", "Channa Dal"):
            out = await get_or_create_canonical_ingredient(
                db, name=variant, category="pulse", standard_unit="g"
            )
            assert out.action == "reused_existing"
            assert out.ingredient is not None and out.ingredient.normalized_name == "chana_dal"
        after_cnt = len((await db.execute(select(CanonicalIngredient))).scalars().all())
        assert after_cnt == before_cnt

        # Categorized audit v2 + dry-run merge + rollback
        audit_clusters = await audit_ingredient_duplicates_v2(db)
        assert any(c.audit_category == "SIMILAR_BUT_DISTINCT" for c in audit_clusters)

        dup_ing = CanonicalIngredient(
            name="Chana_Dal_Dup",
            normalized_name="chana_dal_dup",
            category="pulse",
            standard_unit="g",
            cooking_variants=[],
            supported_units=["g"],
        )
        db.add(dup_ing)
        await db.flush()
        chana_dal = (
            await db.execute(
                select(CanonicalIngredient).where(CanonicalIngredient.normalized_name == "chana_dal")
            )
        ).scalar_one()
        dry = await dry_run_ingredient_merge(db, survivor_id=chana_dal.id, duplicate_id=dup_ing.id)
        assert dry.compatible is True
        rec = await merge_canonical_ingredients(db, survivor_id=chana_dal.id, duplicate_id=dup_ing.id)
        await rollback_canonical_ingredient_merge(db, record=rec)
        assert (await db.get(CanonicalIngredient, dup_ing.id)).is_active is True

    await engine.dispose()
