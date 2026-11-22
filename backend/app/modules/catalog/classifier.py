"""Layered food identification, normalization, and state-aware classification engine."""

from __future__ import annotations

import difflib
import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.food_catalogue_data import (
    CANONICAL_FOOD_CATALOGUE,
    BudgetTreatment,
    CanonicalFoodRecord,
    FoodState,
)
from app.modules.inventory.models import CanonicalItem, ItemAlias

MatchingMethod = Literal[
    "exact_canonical",
    "exact_alias",
    "user_alias",
    "normalized_token",
    "fuzzy_similarity",
    "non_food_rule",
    "context_fallback",
    "hf_llm",
    "uncertain",
]

ClassificationStatus = Literal[
    "pantry_eligible",
    "prepared_meal",
    "non_food",
    "ambiguous_review",
]

_EDIBLE_OIL_PROTECT_RE = re.compile(
    r"\b(?:soyabean|soybean|sunflower|mustard|olive|groundnut|peanut|sesame|gingelly|rice\s*bran|canola|vegetable|cooking|palmolein|refined)\s+oil\b|\bcoconut\s+oil\b|\bghee\b",
    re.IGNORECASE,
)
_NON_FOOD_RE = re.compile(
    r"\b(?:matchbox|match\s*box|matchhox|hair\s*oil|shampoo|conditioner|body\s*wash|hand\s*wash|face\s*wash|soap|detergent|dishwash|scrubber|utensil|channi|sieve|toothpaste|toothbrush|tissue|toilet\s*paper|napkin|aluminium\s*foil|cling\s*wrap|diaper|sanitary|battery|batteries|candle|incense|agarbatti|phenyl|floor\s*cleaner|mosquito|repellent)\b",
    re.IGNORECASE,
)

_PACK_NOISE_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:kg|kgs|g|gm|gms|gram|grams|ml|l|ltr|litre|litres|pcs|pc|oz|lb|lbs|fl\s*oz|gal|pack|packs)\b",
    re.IGNORECASE,
)
_PACKAGING_WORDS_RE = re.compile(
    r"\b(?:bb|pouch|carton|bottle|btl|pack|pkt|jar|tin|box|tetrapack|tetra\s*pack|refill|imported|fresh|organic|pure|premium|popular|royal)\b",
    re.IGNORECASE,
)
_RETURN_NOISE_RE = re.compile(
    r"\b(?:\d+\s*days?\s*retu[rnmu]{1,3}|non[\s-]*returnable|returnable)\b", re.I
)
_GROCERY_PACK_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:kg|g|gm|gms|ml|l|ltr|oz|lb|pack|pkt)\b", re.I)


class FoodClassificationResult(BaseModel):
    """Structured classification output for a single receipt item or food query."""

    original_description: str
    normalized_name: str
    matched_canonical_name: str | None = None
    canonical_food_id: str | None = None
    category: str = "other"
    secondary_category: str | None = None
    food_state: FoodState = "unknown"
    is_prepared_food: bool = False
    pantry_eligible: bool = False
    classification_status: ClassificationStatus = "ambiguous_review"
    budget_treatment: BudgetTreatment = "requires_user_review"
    default_base_unit: Literal["g", "ml", "pcs"] = "pcs"
    confidence: float = 0.0
    classification_confidence: float = 0.0
    matching_confidence: float = 0.0
    matching_status: Literal["matched_existing", "new_product_candidate", "ambiguous"] = "ambiguous"
    eligibility_reason: str = "AMBIGUOUS_PRODUCT"
    extracted_brand: str | None = None
    extracted_pack_size: str | None = None
    matching_method: MatchingMethod = "uncertain"
    source: str = "unmatched"
    needs_review: bool = True
    review_reason: str | None = None


_ABBREV_MAP = {
    "pkt": "packet",
    "btl": "bottle",
    "choco": "chocolate",
    "tom": "tomato",
    "veg": "vegetable",
    "sf": "sunflower",
    "chaki": "chakki",
    "basamati": "basmati",
    "panner": "paneer",
    "tometo": "tomato",
}

_UNKNOWN_GROCERY_RULES: tuple[
    tuple[re.Pattern[str], str, str, Literal["g", "ml", "pcs"], FoodState, bool], ...
] = (
    (
        re.compile(
            r"\b(?:sunflower|mustard|groundnut|olive|coconut|sesame|refined|cooking|rice\s*bran|soyabean)\s+oil\b|\bghee\b|\bvanaspati\b",
            re.I,
        ),
        "cooking_essentials",
        "cooking_oil",
        "ml",
        "processed",
        False,
    ),
    (
        re.compile(
            r"\b(?:milk|curd|dahi|paneer|butter|cheese|yogurt|lassi|chaas|buttermilk|cream)\b", re.I
        ),
        "dairy",
        "milk_and_dairy",
        "ml",
        "fresh",
        False,
    ),
    (re.compile(r"\beggs?\b", re.I), "dairy", "eggs", "pcs", "fresh", False),
    (
        re.compile(
            r"\b(?:rice|basmati|sona\s*masoori|ponni|kolam|poha|quinoa|oats|millet|ragi|jowar|bajra)\b",
            re.I,
        ),
        "grain",
        "rice_and_grains",
        "g",
        "dried",
        False,
    ),
    (
        re.compile(r"\b(?:atta|chakki|maida|suji|rava|besan|flour)\b", re.I),
        "grain",
        "flour",
        "g",
        "dried",
        False,
    ),
    (
        re.compile(r"\b(?:dal|toor|moong|masoor|chana|urad|rajma|chole|lentils|pulses)\b", re.I),
        "pulses",
        "lentils_and_pulses",
        "g",
        "dried",
        False,
    ),
    (
        re.compile(
            r"\b(?:tomato|potato|onion|peas|matar|palak|spinach|bhindi|okra|garlic|ginger|carrot|capsicum|cabbage|cauliflower|broccoli|cucumber|brinjal|beans)\b",
            re.I,
        ),
        "vegetable",
        "fresh_vegetables",
        "g",
        "fresh",
        False,
    ),
    (
        re.compile(
            r"\b(?:apple|banana|mango|mosambi|orange|pomegranate|grapes|papaya|watermelon|lemon|lime|kiwi|berry|guava)\b",
            re.I,
        ),
        "fruit",
        "fresh_fruits",
        "g",
        "fresh",
        False,
    ),
    (
        re.compile(
            r"\b(?:masala|turmeric|haldi|mirchi|jeera|cumin|dhania|cardamom|pepper|clove|cinnamon|spice|salt|sugar|jaggery|gud)\b",
            re.I,
        ),
        "spices",
        "spices_and_seasoning",
        "g",
        "dried",
        False,
    ),
    (
        re.compile(
            r"\b(?:idli\s*mix|dosa\s*mix|noodles|maggi|pasta|vermicelli|ketchup|sauce|jam|honey|pickle|achaar|vinegar|peanut\s*butter)\b",
            re.I,
        ),
        "packaged_grocery",
        "pantry_staples",
        "g",
        "processed",
        False,
    ),
    (
        re.compile(
            r"\b(?:biscuit|cookies|chips|namkeen|chocolate|truffles|bread|rusk|muesli|cornflakes|cereal)\b",
            re.I,
        ),
        "packaged_grocery",
        "snacks_and_bakery",
        "g",
        "processed",
        False,
    ),
    (
        re.compile(r"\b(?:tea|chai|coffee|juice|coke|cola|soda|water|drink)\b", re.I),
        "beverage",
        "beverages",
        "ml",
        "processed",
        False,
    ),
)


def _expand_abbreviations(text: str) -> str:
    tokens = text.split()
    return " ".join(_ABBREV_MAP.get(t.lower(), t) for t in tokens)


def _extract_pack_and_brand(
    original: str, product_match: re.Match[str] | None
) -> tuple[str | None, str | None]:
    pack_m = _GROCERY_PACK_RE.search(original)
    pack_size = pack_m.group(0).strip() if pack_m else None
    brand: str | None = None
    if product_match and product_match.start() > 1:
        prefix = original[: product_match.start()].strip(" -–:,")
        # Only take short leading brand phrases (1-3 words), never numeric codes
        words = [w for w in prefix.split() if any(c.isalpha() for c in w)]
        if 1 <= len(words) <= 3:
            brand = " ".join(words)
    return brand, pack_size


def _classify_unknown_grocery_product(
    original_description: str,
    normalized_name: str,
    receipt_category: str,
) -> FoodClassificationResult | None:
    if receipt_category == "restaurant" and not _GROCERY_PACK_RE.search(original_description):
        return None
    expanded = _expand_abbreviations(normalized_name)
    for pat, cat, subcat, unit, fstate, is_prep in _UNKNOWN_GROCERY_RULES:
        m = pat.search(expanded) or pat.search(original_description)
        if m:
            brand, pack_size = _extract_pack_and_brand(original_description, m)
            return FoodClassificationResult(
                original_description=original_description,
                normalized_name=expanded,
                matched_canonical_name=None,
                canonical_food_id=None,
                category=cat,
                secondary_category=subcat,
                food_state=fstate,
                is_prepared_food=is_prep,
                pantry_eligible=True,
                classification_status="pantry_eligible",
                budget_treatment="pantry_inventory",
                default_base_unit=unit,
                confidence=0.86,
                classification_confidence=0.86,
                matching_confidence=0.0,
                matching_status="new_product_candidate",
                eligibility_reason="NEW_PRODUCT_CLASSIFIED",
                extracted_brand=brand,
                extracted_pack_size=pack_size,
                matching_method="context_fallback",
                source="domain_rules_v2",
                needs_review=False,
                review_reason=None,
            )
    return None


class FoodNameNormalizer:
    """Deterministic normalizer stripping pack sizes, packaging noise, and punctuation."""

    @staticmethod
    def normalize(raw_name: str) -> str:
        text = (raw_name or "").strip()
        if not text:
            return ""
        text = _RETURN_NOISE_RE.sub(" ", text)
        text = re.sub(r"\([^)]*\)", " ", text)
        text = _PACK_NOISE_RE.sub(" ", text)
        text = _PACKAGING_WORDS_RE.sub(" ", text)
        text = re.sub(r"[^a-zA-Z0-9\s]", " ", text)
        text = re.sub(r"\s+", " ", text).strip().lower()
        return text


class _CatalogueIndex:
    """In-memory index over CANONICAL_FOOD_CATALOGUE for fast O(1)/layered matching."""

    def __init__(self, records: tuple[CanonicalFoodRecord, ...]) -> None:
        self.records = records
        self.by_canonical: dict[str, CanonicalFoodRecord] = {}
        self.by_alias: dict[str, CanonicalFoodRecord] = {}
        self.token_sets: list[tuple[frozenset[str], CanonicalFoodRecord]] = []

        for rec in records:
            c_norm = FoodNameNormalizer.normalize(rec.canonical_name) or rec.canonical_name.lower()
            self.by_canonical[c_norm] = rec
            self.by_canonical[rec.canonical_name.lower()] = rec

            all_aliases = rec.aliases + rec.regional_names + rec.transliterations
            for al in all_aliases:
                al_raw = al.strip().lower()
                al_norm = FoodNameNormalizer.normalize(al)
                if al_raw:
                    self.by_alias.setdefault(al_raw, rec)
                if al_norm:
                    self.by_alias.setdefault(al_norm, rec)

            toks = frozenset(c_norm.split())
            if toks:
                self.token_sets.append((toks, rec))
            for al in all_aliases:
                al_toks = frozenset(FoodNameNormalizer.normalize(al).split())
                if al_toks:
                    self.token_sets.append((al_toks, rec))


_INDEX = _CatalogueIndex(CANONICAL_FOOD_CATALOGUE)


def _apply_purchase_context(
    rec: CanonicalFoodRecord | None,
    *,
    original_description: str,
    normalized_name: str,
    receipt_category: str,
    confidence: float,
    method: MatchingMethod,
) -> FoodClassificationResult:
    has_grocery_pack = bool(_GROCERY_PACK_RE.search(original_description))

    if rec is None:
        if receipt_category == "restaurant" and not has_grocery_pack:
            return FoodClassificationResult(
                original_description=original_description,
                normalized_name=normalized_name,
                category="cooked_meal",
                food_state="cooked",
                is_prepared_food=True,
                pantry_eligible=False,
                classification_status="prepared_meal",
                budget_treatment="outside_food_expenditure",
                confidence=0.55,
                classification_confidence=0.55,
                matching_confidence=0.0,
                matching_status="ambiguous",
                eligibility_reason="PREPARED_MEAL",
                matching_method="context_fallback",
                source="restaurant_context",
                needs_review=True,
                review_reason="Restaurant item without grocery pack; excluded from pantry.",
            )
        unknown_cls = _classify_unknown_grocery_product(
            original_description, normalized_name, receipt_category
        )
        if unknown_cls is not None:
            return unknown_cls
        return FoodClassificationResult(
            original_description=original_description,
            normalized_name=normalized_name,
            category="other",
            food_state="unknown",
            is_prepared_food=False,
            pantry_eligible=False,
            classification_status="ambiguous_review",
            budget_treatment="requires_user_review",
            confidence=confidence,
            classification_confidence=confidence,
            matching_confidence=0.0,
            matching_status="ambiguous",
            eligibility_reason="AMBIGUOUS_PRODUCT",
            matching_method="uncertain",
            source="unmatched",
            needs_review=True,
            review_reason="Unrecognized product; review before adding to pantry.",
        )

    # Context-aware distinction: e.g., raw Paneer (grocery) vs Kadai Paneer (restaurant)
    is_prepared = rec.is_prepared_food
    pantry_eligible = rec.pantry_eligible
    budget_treatment: BudgetTreatment = rec.budget_treatment
    food_state: FoodState = rec.food_state

    if (
        receipt_category == "restaurant"
        and not has_grocery_pack
        and rec.category != "packaged_grocery"
    ):
        is_prepared = True
        pantry_eligible = False
        budget_treatment = "outside_food_expenditure"
        if food_state in {"fresh", "raw", "dried"}:
            food_state = "cooked"
    elif is_prepared and has_grocery_pack and receipt_category == "grocery":
        # Packaged ready-to-eat grocery (e.g., packaged ice cream or RTE pack)
        pantry_eligible = True
        budget_treatment = "pantry_inventory"

    needs_review = confidence < 0.70
    review_reason: str | None = None
    if not pantry_eligible and is_prepared:
        c_status: ClassificationStatus = "prepared_meal"
    elif not pantry_eligible:
        c_status = "non_food"
    elif needs_review:
        c_status = "ambiguous_review"
        pantry_eligible = False
        budget_treatment = "requires_user_review"
        review_reason = "Low-confidence food match; review before adding to pantry."
    else:
        c_status = "pantry_eligible"

    return FoodClassificationResult(
        original_description=original_description,
        normalized_name=normalized_name,
        matched_canonical_name=rec.canonical_name,
        canonical_food_id=rec.food_id,
        category=rec.category,
        secondary_category=rec.secondary_category,
        food_state=food_state,
        is_prepared_food=is_prepared,
        pantry_eligible=pantry_eligible,
        classification_status=c_status,
        budget_treatment=budget_treatment,
        default_base_unit=rec.default_base_unit,
        confidence=round(confidence, 3),
        classification_confidence=round(confidence, 3),
        matching_confidence=round(confidence, 3),
        matching_status="matched_existing" if pantry_eligible else "ambiguous",
        eligibility_reason="MATCHED_EXISTING_PRODUCT"
        if pantry_eligible
        else ("NON_FOOD" if c_status == "non_food" else "PREPARED_MEAL"),
        matching_method=method,
        source=rec.source_dataset,
        needs_review=needs_review,
        review_reason=review_reason,
    )


class FoodClassificationService:
    """Layered food matcher: exact canonical -> exact alias -> normalized token -> fuzzy ->
    context.
    """

    @staticmethod
    def classify_item(
        raw_name: str,
        *,
        receipt_category: str = "grocery",
        extra_aliases: dict[str, CanonicalFoodRecord] | None = None,
    ) -> FoodClassificationResult:
        raw_lower = (raw_name or "").strip().lower()
        norm = FoodNameNormalizer.normalize(raw_name)
        explicit_non_food = bool(_NON_FOOD_RE.search(raw_lower)) and not bool(
            _EDIBLE_OIL_PROTECT_RE.search(raw_lower)
        )

        # 0. Household/user-corrected alias override (flag if it contradicts explicit non-food)
        if extra_aliases:
            for key in (raw_lower, norm):
                if key and key in extra_aliases:
                    res = _apply_purchase_context(
                        extra_aliases[key],
                        original_description=raw_name,
                        normalized_name=norm,
                        receipt_category=receipt_category,
                        confidence=0.99,
                        method="user_alias",
                    )
                    if explicit_non_food and res.pantry_eligible:
                        res.classification_status = "ambiguous_review"
                        res.pantry_eligible = False
                        res.budget_treatment = "requires_user_review"
                        res.needs_review = True
                        res.review_reason = (
                            "User alias conflicts with non-food product indicators; "
                            "review required."
                        )
                    return res

        # 0b. Explicit non-food household/personal-care detection (before food token matching)
        if explicit_non_food:
            return FoodClassificationResult(
                original_description=raw_name,
                normalized_name=norm,
                category="non_food",
                food_state="non_food",
                is_prepared_food=False,
                pantry_eligible=False,
                classification_status="non_food",
                budget_treatment="household_non_food",
                default_base_unit="pcs",
                confidence=0.95,
                matching_method="non_food_rule",
                source="non_food_rules",
                needs_review=False,
                review_reason="Non-food household or personal care item (excluded from pantry).",
            )

        # 1. Exact canonical name match
        for key in (raw_lower, norm):
            if key and key in _INDEX.by_canonical:
                return _apply_purchase_context(
                    _INDEX.by_canonical[key],
                    original_description=raw_name,
                    normalized_name=norm,
                    receipt_category=receipt_category,
                    confidence=0.98,
                    method="exact_canonical",
                )

        # 2. Exact alias / regional / transliteration match
        for key in (raw_lower, norm):
            if key and key in _INDEX.by_alias:
                return _apply_purchase_context(
                    _INDEX.by_alias[key],
                    original_description=raw_name,
                    normalized_name=norm,
                    receipt_category=receipt_category,
                    confidence=0.94,
                    method="exact_alias",
                )

        # 3. Normalized token-subset match (longest matching canonical/alias token set wins)
        input_tokens = frozenset(norm.split())
        if input_tokens:
            best_rec: CanonicalFoodRecord | None = None
            best_score = 0.0
            for cand_tokens, rec in _INDEX.token_sets:
                if cand_tokens.issubset(input_tokens):
                    score = len(cand_tokens) / max(len(input_tokens), 1)
                    if (
                        len(cand_tokens) > (len(best_rec.canonical_name.split()) if best_rec else 0)
                        or score > best_score
                    ):
                        best_score = score
                        best_rec = rec
            if best_rec is not None:
                conf = min(0.90, 0.72 + 0.18 * best_score)
                return _apply_purchase_context(
                    best_rec,
                    original_description=raw_name,
                    normalized_name=norm,
                    receipt_category=receipt_category,
                    confidence=conf,
                    method="normalized_token",
                )

        # 4. Fuzzy similarity match across canonical names and aliases
        if norm:
            best_ratio = 0.0
            best_fuzzy_rec: CanonicalFoodRecord | None = None
            for key, rec in _INDEX.by_alias.items():
                ratio = difflib.SequenceMatcher(None, norm, key).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_fuzzy_rec = rec
            for key, rec in _INDEX.by_canonical.items():
                ratio = difflib.SequenceMatcher(None, norm, key).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_fuzzy_rec = rec
            if best_fuzzy_rec is not None and best_ratio >= 0.80:
                return _apply_purchase_context(
                    best_fuzzy_rec,
                    original_description=raw_name,
                    normalized_name=norm,
                    receipt_category=receipt_category,
                    confidence=round(best_ratio * 0.9, 3),
                    method="fuzzy_similarity",
                )

        # 5. Low-confidence / uncertain
        return _apply_purchase_context(
            None,
            original_description=raw_name,
            normalized_name=norm,
            receipt_category=receipt_category,
            confidence=0.30,
            method="uncertain",
        )


async def classify_with_household_db(
    db: AsyncSession,
    *,
    household_id: UUID,
    raw_name: str,
    receipt_category: str = "grocery",
    merchant_name: str | None = None,
    use_llm_fallback: bool = True,
) -> FoodClassificationResult:
    """Hybrid classification: household alias -> deterministic catalogue -> HF LLM for uncertain
    items.
    """
    norm = FoodNameNormalizer.normalize(raw_name)
    raw_lower = (raw_name or "").strip().lower()
    candidates = [c for c in {raw_lower, norm} if c]

    if candidates:
        stmt = (
            select(ItemAlias, CanonicalItem)
            .join(CanonicalItem, CanonicalItem.id == ItemAlias.canonical_item_id)
            .where(
                or_(ItemAlias.household_id == household_id, ItemAlias.household_id.is_(None)),
                ItemAlias.normalized_alias.in_(candidates),
            )
            .order_by(ItemAlias.household_id.is_(None).asc())
            .limit(1)
        )
        row = (await db.execute(stmt)).first()
        if row is not None:
            alias_obj, item_obj = row
            bt: BudgetTreatment = (
                item_obj.budget_treatment
                if item_obj.budget_treatment
                in {
                    "pantry_inventory",
                    "outside_food_expenditure",
                    "budget_only_grocery",
                    "household_non_food",
                    "requires_user_review",
                }
                else "pantry_inventory"
            )
            cls_status: ClassificationStatus = (
                "non_food"
                if item_obj.category == "non_food" or bt == "household_non_food"
                else "prepared_meal"
                if item_obj.is_prepared_food or bt == "outside_food_expenditure"
                else "pantry_eligible"
                if item_obj.pantry_eligible
                else "ambiguous_review"
            )
            return FoodClassificationResult(
                original_description=raw_name,
                normalized_name=norm,
                matched_canonical_name=item_obj.name,
                canonical_food_id=str(item_obj.id),
                category=item_obj.category,
                secondary_category=item_obj.secondary_category,
                food_state=item_obj.food_state
                if item_obj.food_state
                in {
                    "raw",
                    "dried",
                    "fresh",
                    "frozen",
                    "processed",
                    "cooked",
                    "ready_to_eat",
                    "unknown",
                }
                else "unknown",
                is_prepared_food=item_obj.is_prepared_food,
                pantry_eligible=item_obj.pantry_eligible,
                classification_status=cls_status,
                budget_treatment=bt,
                default_base_unit=item_obj.default_base_unit
                if item_obj.default_base_unit in {"g", "ml", "pcs"}
                else "pcs",
                confidence=0.98,
                matching_method="user_alias"
                if alias_obj.source == "user_correction"
                else "exact_alias",
                source=alias_obj.source,
                needs_review=False,
            )

    det_res = FoodClassificationService.classify_item(raw_name, receipt_category=receipt_category)
    if det_res.matching_method != "uncertain" or not use_llm_fallback or not raw_lower:
        return det_res

    from app.infra.llm.hf_provider import HuggingFaceRecipeProvider

    llm_res = await HuggingFaceRecipeProvider().classify_receipt_item(
        raw_name=raw_name,
        receipt_category=receipt_category,
        merchant_name=merchant_name,
    )
    if llm_res is None:
        return det_res

    # Independent backend validation of LLM classification output
    if _NON_FOOD_RE.search(raw_name) or llm_res.food_state == "non_food":
        return FoodClassificationResult(
            original_description=raw_name,
            normalized_name=llm_res.normalized_name.strip().lower() or norm,
            category="non_food",
            food_state="unknown",
            is_prepared_food=False,
            pantry_eligible=False,
            classification_status="non_food",
            budget_treatment="household_non_food",
            default_base_unit="pcs",
            confidence=max(0.75, min(0.99, float(llm_res.confidence or 0.85))),
            matching_method="hf_llm",
            source="hf_llm",
            needs_review=False,
            review_reason=None,
        )

    if llm_res.food_state == "prepared_meal":
        return FoodClassificationResult(
            original_description=raw_name,
            normalized_name=llm_res.normalized_name.strip().lower() or norm,
            category=llm_res.category or "prepared_food",
            food_state="cooked",
            is_prepared_food=True,
            pantry_eligible=False,
            classification_status="prepared_meal",
            budget_treatment="outside_food_expenditure",
            default_base_unit="pcs",
            confidence=min(0.95, float(llm_res.confidence or 0.8)),
            matching_method="hf_llm",
            source="hf_llm",
            needs_review=False,
            review_reason=None,
        )

    if (
        llm_res.needs_review
        or llm_res.food_state == "unknown"
        or float(llm_res.confidence or 0.0) < 0.70
        or not llm_res.pantry_eligible
    ):
        return FoodClassificationResult(
            original_description=raw_name,
            normalized_name=llm_res.normalized_name.strip().lower() or norm,
            category=llm_res.category or "other",
            food_state="unknown",
            is_prepared_food=False,
            pantry_eligible=False,
            classification_status="ambiguous_review",
            budget_treatment="requires_user_review",
            default_base_unit="pcs",
            confidence=min(0.69, float(llm_res.confidence or 0.45)),
            matching_method="hf_llm",
            source="hf_llm",
            needs_review=True,
            review_reason=llm_res.explanation
            or "Uncertain product classification; please review before adding to pantry.",
        )

    mapped_state: FoodState = "raw" if llm_res.food_state == "raw" else "processed"
    return FoodClassificationResult(
        original_description=raw_name,
        normalized_name=llm_res.normalized_name.strip().lower() or norm,
        category=(llm_res.category or "packaged").strip().lower(),
        food_state=mapped_state,
        is_prepared_food=False,
        pantry_eligible=True,
        classification_status="pantry_eligible",
        budget_treatment="pantry_inventory",
        default_base_unit="pcs",
        confidence=min(0.95, float(llm_res.confidence)),
        matching_method="hf_llm",
        source="hf_llm",
        needs_review=False,
        review_reason=None,
    )


async def record_user_classification_correction(
    db: AsyncSession,
    *,
    household_id: UUID,
    raw_name: str,
    canonical_item_id: UUID,
) -> None:
    """Store an approved user classification correction as a household-scoped ItemAlias."""
    norm = FoodNameNormalizer.normalize(raw_name) or raw_name.strip().lower()
    if not norm:
        return
    existing = (
        await db.execute(
            select(ItemAlias).where(
                ItemAlias.household_id == household_id,
                ItemAlias.normalized_alias == norm,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            ItemAlias(
                household_id=household_id,
                canonical_item_id=canonical_item_id,
                normalized_alias=norm,
                source="user_correction",
                alias_kind="user_correction",
            )
        )
    else:
        existing.canonical_item_id = canonical_item_id
        existing.source = "user_correction"
        existing.alias_kind = "user_correction"
