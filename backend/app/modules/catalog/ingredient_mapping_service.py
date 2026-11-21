"""Purchased-product (CanonicalItem) to CanonicalIngredient mapping service.

Confidence scores produced by the heuristic matcher are rule-based ranking
scores in [0.000, 1.000] and are NOT calibrated empirical probabilities.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.inventory.models import CanonicalIngredient, CanonicalItem, IngredientMapping

ConfidenceTier = Literal["high", "medium", "low"]
ResolutionStatus = Literal["reused_confirmed", "reused_suggested", "auto_mapped", "suggested", "unmapped"]

# Heuristic thresholds (not calibrated probabilities)
HIGH_CONFIDENCE_THRESHOLD = Decimal("0.900")
MEDIUM_CONFIDENCE_THRESHOLD = Decimal("0.650")

# Common brand & pack-size noise stripped during brand-agnostic matching.
# Brand is NEVER a mandatory attribute: unknown brands still match via ingredient tokens.
_KNOWN_BRAND_TOKENS = {
    "amul", "aavin", "nandini", "mother", "dairy", "heritage", "milky", "mist",
    "country", "delight", "akshayakalpa", "nestle", "britannia", "gowardhan",
    "india", "gate", "daawat", "kohinoor", "fortune", "saffola", "dhara",
    "sundrop", "gold", "winner", "svs", "idhayam", "mantra", "organic",
    "tattva", "tata", "sampann", "aashirvaad", "pillsbury", "shakti", "bhog",
    "everest", "mdh", "catch", "mtr", "aachi", "sakthi", "bb", "royal",
    "popular", "fresho", "super", "choice", "local", "brand", "fresh", "pure",
    "classic", "premium", "select", "special", "deluxe", "taaza", "shakti",
    "udhaiyam", "eastern", "prabhat", "fortune",
}

_PACK_NOISE_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:kg|g|gm|gms|gram|grams|l|ltr|litre|litres|liter|liters|ml|pcs|pc|pack|packs|pkt)\b",
    re.IGNORECASE,
)

# Tokens that indicate a processed/derived form that must NOT map to raw whole ingredients
_FLOUR_OR_POWDER_TOKENS = {"flour", "atta", "maida", "besan", "powder", "starch", "batter", "mix", "flakes", "poha"}
_PLANT_OR_ALT_MILK_PREFIXES = {"coconut", "almond", "soy", "soya", "oat", "oats", "cashew", "rice", "condensed", "buttermilk", "milkshake"}

# Variant descriptors preserved on the product/suggestion without splitting generic Milk
_MILK_VARIANT_TOKENS = {
    "full_cream": ("full cream", "full-cream", "fullcream", "whole milk", "gold"),
    "toned": ("double toned", "toned"),
    "skim": ("skim", "skimmed", "fat free", "slim"),
}

# Explicit high-precision synonym phrases -> CanonicalIngredient.normalized_name
_EXACT_SYNONYM_MAP: dict[str, str] = {
    "milk": "milk",
    "cow milk": "milk",
    "buffalo milk": "milk",
    "full cream milk": "milk",
    "toned milk": "milk",
    "double toned milk": "milk",
    "skim milk": "milk",
    "skimmed milk": "milk",
    "standardized milk": "milk",
    "curd": "curd",
    "dahi": "curd",
    "yogurt": "curd",
    "yoghurt": "curd",
    "paneer": "paneer",
    "malai paneer": "paneer",
    "butter": "butter",
    "salted butter": "butter",
    "unsalted butter": "butter",
    "cheese": "cheese",
    "cheddar cheese": "cheese",
    "mozzarella cheese": "cheese",
    "processed cheese": "cheese",
    "basmati rice": "basmati_rice",
    "sona masoori rice": "sona_masoori_rice",
    "sona masuri rice": "sona_masoori_rice",
    "idli rice": "idli_rice",
    "parboiled idli rice": "idli_rice",
    "whole wheat flour": "whole_wheat_flour",
    "wheat atta": "whole_wheat_flour",
    "chakki atta": "whole_wheat_flour",
    "atta": "whole_wheat_flour",
    "semolina": "semolina",
    "rava": "semolina",
    "sooji": "semolina",
    "suji": "semolina",
    "rolled oats": "rolled_oats",
    "oats": "rolled_oats",
    "toor dal": "toor_dal",
    "tur dal": "toor_dal",
    "arhar dal": "toor_dal",
    "moong dal": "moong_dal",
    "mung dal": "moong_dal",
    "chana dal": "chana_dal",
    "urad dal": "urad_dal",
    "urad dhuli": "urad_dal",
    "dhall orid": "urad_dal",
    "orid dhall": "urad_dal",
    "orid dal": "urad_dal",
    "chickpeas": "chickpeas",
    "kabuli chana": "chickpeas",
    "chole": "chickpeas",
    "onion": "onion",
    "onions": "onion",
    "red onion": "onion",
    "tomato": "tomato",
    "tomatoes": "tomato",
    "potato": "potato",
    "potatoes": "potato",
    "garlic": "garlic",
    "ginger": "ginger",
    "spinach": "spinach",
    "palak": "spinach",
    "banana": "banana",
    "bananas": "banana",
    "apple": "apple",
    "apples": "apple",
    "daily apple": "apple",
    "daily apple sebu": "apple",
    "imported daily apple sebu": "apple",
    "lemon": "lemon",
    "lemons": "lemon",
    "mango": "mango",
    "mangoes": "mango",
    "pomegranate": "pomegranate",
    "sunflower oil": "sunflower_oil",
    "refined sunflower oil": "sunflower_oil",
    "mustard oil": "mustard_oil",
    "kachi ghani mustard oil": "mustard_oil",
    "coconut oil": "coconut_oil",
    "virgin coconut oil": "coconut_oil",
    "rice bran oil": "rice_bran_oil",
    "ghee": "ghee",
    "cow ghee": "ghee",
    "desi ghee": "ghee",
    "turmeric powder": "turmeric_powder",
    "haldi powder": "turmeric_powder",
    "cumin seeds": "cumin_seeds",
    "jeera": "cumin_seeds",
    "red chilli powder": "red_chilli_powder",
    "chilli powder": "red_chilli_powder",
    "coriander powder": "coriander_powder",
    "dhania powder": "coriander_powder",
    "coriander seeds": "coriander_seeds",
    "dhaniya sabut": "coriander_seeds",
    "dhania sabut": "coriander_seeds",
    "sugar": "sugar",
    "chini": "sugar",
    "chini df": "sugar",
    "mustard seeds": "mustard_seeds",
    "rai": "mustard_seeds",
    "garam masala": "garam_masala",
    "almonds": "almonds",
    "badam": "almonds",
    "walnuts": "walnuts",
    "walnut": "walnuts",
    "akhrot": "walnuts",
    "cashews": "cashews",
    "kaju": "cashews",
    "peanuts": "peanuts",
    "groundnuts": "peanuts",
    "sesame seeds": "sesame_seeds",
    "til": "sesame_seeds",
    "flax seeds": "flax_seeds",
    "flaxseeds": "flax_seeds",
}


@dataclass(frozen=True)
class MatchCandidate:
    ingredient: CanonicalIngredient
    confidence: Decimal
    tier: ConfidenceTier
    detected_variant: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class MappingResolution:
    canonical_item_id: UUID
    status: ResolutionStatus
    tier: ConfidenceTier
    mapping: IngredientMapping | None = None
    suggested_ingredient: CanonicalIngredient | None = None
    matching_confidence: Decimal = Decimal("0.000")
    mapping_source: str | None = None
    user_confirmed: bool = False
    detected_variant: str | None = None
    reason: str = ""


@dataclass
class BackfillItemDecision:
    canonical_item_id: UUID
    canonical_item_name: str
    household_id: UUID | None
    action: Literal["reused", "auto_mapped", "needs_review_medium", "unmapped_low"]
    canonical_ingredient_id: UUID | None = None
    canonical_ingredient_name: str | None = None
    matching_confidence: Decimal = Decimal("0.000")
    detected_variant: str | None = None
    reason: str = ""


@dataclass
class BackfillReport:
    dry_run: bool
    total_items: int = 0
    reused_count: int = 0
    auto_mapped_count: int = 0
    medium_review_count: int = 0
    unmapped_low_count: int = 0
    decisions: list[BackfillItemDecision] = field(default_factory=list)


def _clean_tokens(raw_name: str, *, strip_brands: bool = True) -> list[str]:
    text = _PACK_NOISE_RE.sub(" ", raw_name.lower())
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    tokens = [t for t in text.split() if t]
    if strip_brands:
        filtered = [t for t in tokens if t not in _KNOWN_BRAND_TOKENS]
        if filtered:
            return filtered
    return tokens


def detect_cooking_variant(raw_name: str) -> str | None:
    lowered = raw_name.lower()
    for variant_key, phrases in _MILK_VARIANT_TOKENS.items():
        if any(p in lowered for p in phrases):
            return variant_key
    if "basmati" in lowered:
        return "basmati"
    if "idli" in lowered:
        return "idli"
    if "sona" in lowered or "masoori" in lowered:
        return "sona_masoori"
    if "cold pressed" in lowered or "kachi ghani" in lowered:
        return "cold_pressed"
    return None


def _passes_identity_guards(raw_name: str, candidate_norm: str) -> bool:
    """Enforce Phase 5 product identity distinctions."""
    lowered = raw_name.lower()
    raw_tokens = set(_clean_tokens(raw_name, strip_brands=False))

    # 1. Rice Flour / Atta / Powder must NEVER map to whole rice or whole grain
    if raw_tokens & _FLOUR_OR_POWDER_TOKENS:
        if candidate_norm in {"basmati_rice", "sona_masoori_rice", "idli_rice", "rolled_oats"}:
            return False

    # 2. Coconut Milk / Almond Milk / Soy Milk / Oat Milk / Milk Chocolate must NEVER map to dairy Milk
    if candidate_norm == "milk":
        if any(f"{prefix} milk" in lowered for prefix in _PLANT_OR_ALT_MILK_PREFIXES):
            return False
        if any(
            conf_phrase in lowered
            for conf_phrase in ("milk powder", "butter milk", "milk chocolate", "ice cream", "dairy milk")
        ):
            return False

    # 3. Coconut Oil vs Coconut Milk vs Coconut
    if candidate_norm == "coconut_oil" and "oil" not in raw_tokens:
        return False

    # 4. Rice varieties must remain distinct; generic "rice" is ambiguous
    if candidate_norm in {"basmati_rice", "sona_masoori_rice", "idli_rice"}:
        if candidate_norm == "basmati_rice" and "basmati" not in raw_tokens:
            return False
        if candidate_norm == "idli_rice" and "idli" not in raw_tokens:
            return False
        if candidate_norm == "sona_masoori_rice" and not ({"sona", "masoori", "masuri"} & raw_tokens):
            return False

    # 5. Oils must specify the oilseed/fat identity
    if candidate_norm == "sunflower_oil" and "sunflower" not in raw_tokens:
        return False
    if candidate_norm == "mustard_oil" and "mustard" not in raw_tokens:
        return False
    if candidate_norm == "rice_bran_oil" and not ({"rice", "bran"} <= raw_tokens):
        return False

    return True


def score_ingredient_candidates(
    item: CanonicalItem,
    ingredients: list[CanonicalIngredient],
) -> MatchCandidate | None:
    """Heuristically score CanonicalIngredient candidates for a CanonicalItem."""
    if not item.pantry_eligible or item.is_prepared_food or item.category == "non_food":
        return None

    by_norm = {ing.normalized_name: ing for ing in ingredients if ing.is_active}
    detected_variant = detect_cooking_variant(item.name)

    # Clean brand & pack noise
    tokens = _clean_tokens(item.name, strip_brands=True)
    cleaned_phrase = " ".join(tokens)
    snake_phrase = "_".join(tokens)

    # 1. Exact cleaned phrase or normalized_name match -> HIGH confidence
    target_norm = _EXACT_SYNONYM_MAP.get(cleaned_phrase) or (
        snake_phrase if snake_phrase in by_norm else None
    )
    if target_norm and target_norm in by_norm:
        ing = by_norm[target_norm]
        if _passes_identity_guards(item.name, ing.normalized_name):
            conf = Decimal("0.960") if item.category == ing.category else Decimal("0.920")
            return MatchCandidate(
                ingredient=ing,
                confidence=conf,
                tier="high",
                detected_variant=detected_variant,
                reason=f"exact_normalized_match:{cleaned_phrase}",
            )

    # 2. Sub-phrase match (e.g., "sunlite sunflower oil" -> "sunflower oil",
    #    "gold milk" -> "milk", "farm fresh onion" -> "onion")
    raw_lower = " ".join(_clean_tokens(item.name, strip_brands=False))
    best_phrase_match: tuple[int, CanonicalIngredient] | None = None
    for phrase, norm_name in _EXACT_SYNONYM_MAP.items():
        ing = by_norm.get(norm_name)
        if ing is None or not _passes_identity_guards(item.name, norm_name):
            continue
        if re.search(rf"\b{re.escape(phrase)}\b", cleaned_phrase) or re.search(
            rf"\b{re.escape(phrase)}\b", raw_lower
        ):
            phrase_len = len(phrase.split())
            if best_phrase_match is None or phrase_len > best_phrase_match[0]:
                best_phrase_match = (phrase_len, ing)

    if best_phrase_match is not None:
        phrase_len, ing = best_phrase_match
        # If there are extra unrecognized non-brand modifiers alongside a 1-word match,
        # treat multi-modifier cases carefully: category match + 1-word core = HIGH if <=1 extra token,
        # otherwise MEDIUM for user confirmation.
        extra_tokens = max(0, len(tokens) - phrase_len)
        if extra_tokens <= 1 or phrase_len >= 2:
            conf = Decimal("0.940") if item.category == ing.category else Decimal("0.900")
            return MatchCandidate(
                ingredient=ing,
                confidence=conf,
                tier="high",
                detected_variant=detected_variant,
                reason=f"phrase_match:{ing.normalized_name}",
            )
        return MatchCandidate(
            ingredient=ing,
            confidence=Decimal("0.780"),
            tier="medium",
            detected_variant=detected_variant,
            reason=f"partial_phrase_with_modifiers:{ing.normalized_name}",
        )

    # 3. Token overlap within the same category -> MEDIUM confidence (suggest only)
    token_set = set(tokens)
    best_overlap: tuple[float, CanonicalIngredient] | None = None
    for ing in ingredients:
        if not ing.is_active or not _passes_identity_guards(item.name, ing.normalized_name):
            continue
        if item.category != "general" and item.category != ing.category:
            continue
        ing_tokens = set(ing.normalized_name.split("_"))
        if not ing_tokens or not (token_set & ing_tokens):
            continue
        jaccard = len(token_set & ing_tokens) / len(token_set | ing_tokens)
        if jaccard >= 0.45 and (best_overlap is None or jaccard > best_overlap[0]):
            best_overlap = (jaccard, ing)

    if best_overlap is not None:
        return MatchCandidate(
            ingredient=best_overlap[1],
            confidence=Decimal("0.720"),
            tier="medium",
            detected_variant=detected_variant,
            reason=f"category_token_overlap:{best_overlap[1].normalized_name}",
        )

    return None


async def lookup_existing_mapping(
    db: AsyncSession,
    *,
    household_id: UUID | None,
    canonical_item_id: UUID,
) -> IngredientMapping | None:
    """Look up household mapping first, falling back to global mapping."""
    if household_id is not None:
        hh_stmt = select(IngredientMapping).where(
            IngredientMapping.household_id == household_id,
            IngredientMapping.canonical_item_id == canonical_item_id,
        )
        hh_map = (await db.execute(hh_stmt)).scalar_one_or_none()
        if hh_map is not None:
            return hh_map

    global_stmt = select(IngredientMapping).where(
        IngredientMapping.household_id.is_(None),
        IngredientMapping.canonical_item_id == canonical_item_id,
    )
    return (await db.execute(global_stmt)).scalar_one_or_none()


async def resolve_or_suggest_mapping(
    db: AsyncSession,
    *,
    household_id: UUID | None,
    canonical_item: CanonicalItem,
    auto_map_high_confidence: bool = True,
) -> MappingResolution:
    """Resolve existing mapping or evaluate taxonomy match policy for a CanonicalItem."""
    detected_variant = detect_cooking_variant(canonical_item.name)

    existing = await lookup_existing_mapping(
        db, household_id=household_id, canonical_item_id=canonical_item.id
    )
    if existing is not None:
        ing = await db.get(CanonicalIngredient, existing.canonical_ingredient_id)
        status: ResolutionStatus = (
            "reused_confirmed" if existing.user_confirmed else "reused_suggested"
        )
        return MappingResolution(
            canonical_item_id=canonical_item.id,
            status=status,
            tier="high" if existing.matching_confidence >= HIGH_CONFIDENCE_THRESHOLD else "medium",
            mapping=existing,
            suggested_ingredient=ing,
            matching_confidence=existing.matching_confidence,
            mapping_source=existing.mapping_source,
            user_confirmed=existing.user_confirmed,
            detected_variant=detected_variant,
            reason="existing_mapping_reused",
        )

    # Phase 7.2: Central authoritative IngredientResolver first
    from app.modules.catalog.ingredient_catalogue_sync import sync_catalogue_to_db
    from app.modules.catalog.ingredient_resolver import IngredientResolver

    resolver = IngredientResolver()
    res = await resolver.resolve_for_household(
        db,
        household_id=household_id,
        receipt_text=canonical_item.name,
        canonical_item_id=canonical_item.id,
    )
    if res.variant_id and not res.requires_review:
        target_ing = (
            await db.get(CanonicalIngredient, res.canonical_ingredient_id)
            if res.canonical_ingredient_id is not None
            else None
        )
        if target_ing is None:
            await sync_catalogue_to_db(db)
            target_ing = (
                await db.execute(
                    select(CanonicalIngredient).where(
                        CanonicalIngredient.variant_id == res.variant_id
                    )
                )
            ).scalar_one_or_none()
        if target_ing is not None:
            is_learned = res.match_source in (
                "confirmed_product_mapping",
                "household_learned_alias",
            )
            if auto_map_high_confidence:
                mapping = IngredientMapping(
                    household_id=household_id,
                    canonical_item_id=canonical_item.id,
                    canonical_ingredient_id=target_ing.id,
                    matching_confidence=res.confidence,
                    mapping_source="user_confirmed" if is_learned else "catalogue",
                    user_confirmed=is_learned,
                )
                db.add(mapping)
                await db.flush()
                return MappingResolution(
                    canonical_item_id=canonical_item.id,
                    status="auto_mapped",
                    tier="high",
                    mapping=mapping,
                    suggested_ingredient=target_ing,
                    matching_confidence=res.confidence,
                    mapping_source=mapping.mapping_source,
                    user_confirmed=is_learned,
                    detected_variant=res.variant_name or detected_variant,
                    reason=res.reason,
                )
            return MappingResolution(
                canonical_item_id=canonical_item.id,
                status="suggested",
                tier="high",
                suggested_ingredient=target_ing,
                matching_confidence=res.confidence,
                mapping_source="catalogue",
                user_confirmed=False,
                detected_variant=res.variant_name or detected_variant,
                reason=res.reason,
            )

    if res.requires_review and res.suggestions:
        top_var_id = res.suggestions[0].get("variant_id")
        if top_var_id:
            sug_ing = (
                await db.execute(
                    select(CanonicalIngredient).where(
                        CanonicalIngredient.variant_id == top_var_id
                    )
                )
            ).scalar_one_or_none()
            if sug_ing is not None:
                return MappingResolution(
                    canonical_item_id=canonical_item.id,
                    status="suggested",
                    tier="medium",
                    suggested_ingredient=sug_ing,
                    matching_confidence=Decimal("0.700"),
                    mapping_source="classifier_v2",
                    user_confirmed=False,
                    detected_variant=detected_variant,
                    reason=res.reason,
                )

    ingredients = list(
        (await db.execute(select(CanonicalIngredient).where(CanonicalIngredient.is_active.is_(True))))
        .scalars()
        .all()
    )
    candidate = score_ingredient_candidates(canonical_item, ingredients)
    if candidate is None:
        return MappingResolution(
            canonical_item_id=canonical_item.id,
            status="unmapped",
            tier="low",
            matching_confidence=Decimal("0.000"),
            detected_variant=detected_variant,
            reason="low_confidence_or_non_pantry",
        )

    if candidate.tier == "high" and auto_map_high_confidence:
        mapping = IngredientMapping(
            household_id=household_id,
            canonical_item_id=canonical_item.id,
            canonical_ingredient_id=candidate.ingredient.id,
            matching_confidence=candidate.confidence,
            mapping_source="catalogue",
            user_confirmed=False,
        )
        db.add(mapping)
        await db.flush()
        return MappingResolution(
            canonical_item_id=canonical_item.id,
            status="auto_mapped",
            tier="high",
            mapping=mapping,
            suggested_ingredient=candidate.ingredient,
            matching_confidence=candidate.confidence,
            mapping_source="catalogue",
            user_confirmed=False,
            detected_variant=candidate.detected_variant,
            reason=candidate.reason,
        )

    return MappingResolution(
        canonical_item_id=canonical_item.id,
        status="suggested",
        tier=candidate.tier,
        suggested_ingredient=candidate.ingredient,
        matching_confidence=candidate.confidence,
        mapping_source="classifier_v2",
        user_confirmed=False,
        detected_variant=candidate.detected_variant,
        reason=candidate.reason,
    )


async def confirm_ingredient_mapping(
    db: AsyncSession,
    *,
    household_id: UUID | None,
    canonical_item_id: UUID,
    canonical_ingredient_id: UUID,
    mapping_source: str = "user_confirmed",
    allow_override: bool = False,
) -> IngredientMapping:
    """Persist a user-confirmed mapping while preventing accidental conflicts."""
    item = await db.get(CanonicalItem, canonical_item_id)
    if item is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="CanonicalItem not found.")
    if household_id is not None and item.household_id is not None and item.household_id != household_id:
        raise AppError(
            status_code=403,
            code="FORBIDDEN",
            message="CanonicalItem belongs to another household.",
        )

    ingredient = await db.get(CanonicalIngredient, canonical_ingredient_id)
    if ingredient is None:
        raise AppError(status_code=404, code="NOT_FOUND", message="CanonicalIngredient not found.")

    if household_id is None:
        stmt = select(IngredientMapping).where(
            IngredientMapping.household_id.is_(None),
            IngredientMapping.canonical_item_id == canonical_item_id,
        )
    else:
        stmt = select(IngredientMapping).where(
            IngredientMapping.household_id == household_id,
            IngredientMapping.canonical_item_id == canonical_item_id,
        )

    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        if existing.canonical_ingredient_id == canonical_ingredient_id:
            existing.user_confirmed = True
            existing.mapping_source = mapping_source
            existing.matching_confidence = Decimal("1.000")
            if household_id is not None:
                from app.modules.catalog.ingredient_catalogue_sync import (
                    record_household_learned_alias,
                )

                await record_household_learned_alias(
                    db,
                    household_id=household_id,
                    phrase=item.name,
                    canonical_ingredient=ingredient,
                    source="user_correction",
                    provenance="confirm_ingredient_mapping",
                )
            await db.flush()
            return existing
        if not allow_override:
            raise AppError(
                status_code=409,
                code="MAPPING_CONFLICT",
                message=(
                    f"CanonicalItem {canonical_item_id} is already mapped to "
                    f"CanonicalIngredient {existing.canonical_ingredient_id}."
                ),
            )
        existing.canonical_ingredient_id = canonical_ingredient_id
        existing.user_confirmed = True
        existing.mapping_source = "user_correction"
        existing.matching_confidence = Decimal("1.000")
        if household_id is not None:
            from app.modules.catalog.ingredient_catalogue_sync import (
                record_household_learned_alias,
            )

            await record_household_learned_alias(
                db,
                household_id=household_id,
                phrase=item.name,
                canonical_ingredient=ingredient,
                source="user_correction",
                provenance="confirm_ingredient_mapping_override",
            )
        await db.flush()
        return existing

    mapping = IngredientMapping(
        household_id=household_id,
        canonical_item_id=canonical_item_id,
        canonical_ingredient_id=canonical_ingredient_id,
        matching_confidence=Decimal("1.000"),
        mapping_source=mapping_source,
        user_confirmed=True,
    )
    db.add(mapping)
    if household_id is not None:
        from app.modules.catalog.ingredient_catalogue_sync import (
            record_household_learned_alias,
        )

        await record_household_learned_alias(
            db,
            household_id=household_id,
            phrase=item.name,
            canonical_ingredient=ingredient,
            source="user_correction",
            provenance="confirm_ingredient_mapping",
        )
    await db.flush()
    return mapping


async def backfill_canonical_item_mappings(
    db: AsyncSession,
    *,
    dry_run: bool = True,
) -> BackfillReport:
    """Idempotently backfill high-confidence CanonicalItem -> CanonicalIngredient mappings.

    - Preserves all CanonicalItem IDs, PurchaseItem, InventoryLot, StockLedger, and price history.
    - Leaves medium/low confidence items unmapped for manual review.
    """
    report = BackfillReport(dry_run=dry_run)
    items = list(
        (
            await db.execute(
                select(CanonicalItem)
                .where(CanonicalItem.is_active.is_(True))
                .order_by(CanonicalItem.created_at, CanonicalItem.id)
            )
        )
        .scalars()
        .all()
    )
    # Phase 7.2: Central authoritative IngredientResolver first
    from app.modules.catalog.ingredient_catalogue_sync import sync_catalogue_to_db
    from app.modules.catalog.ingredient_resolver import IngredientResolver

    resolver = IngredientResolver()
    res = await resolver.resolve_for_household(
        db,
        household_id=household_id,
        receipt_text=canonical_item.name,
        canonical_item_id=canonical_item.id,
    )
    if res.variant_id and not res.requires_review:
        target_ing = (
            await db.get(CanonicalIngredient, res.canonical_ingredient_id)
            if res.canonical_ingredient_id is not None
            else None
        )
        if target_ing is None:
            await sync_catalogue_to_db(db)
            target_ing = (
                await db.execute(
                    select(CanonicalIngredient).where(
                        CanonicalIngredient.variant_id == res.variant_id
                    )
                )
            ).scalar_one_or_none()
        if target_ing is not None:
            is_learned = res.match_source in (
                "confirmed_product_mapping",
                "household_learned_alias",
            )
            if auto_map_high_confidence:
                mapping = IngredientMapping(
                    household_id=household_id,
                    canonical_item_id=canonical_item.id,
                    canonical_ingredient_id=target_ing.id,
                    matching_confidence=res.confidence,
                    mapping_source="user_confirmed" if is_learned else "catalogue",
                    user_confirmed=is_learned,
                )
                db.add(mapping)
                await db.flush()
                return MappingResolution(
                    canonical_item_id=canonical_item.id,
                    status="auto_mapped",
                    tier="high",
                    mapping=mapping,
                    suggested_ingredient=target_ing,
                    matching_confidence=res.confidence,
                    mapping_source=mapping.mapping_source,
                    user_confirmed=is_learned,
                    detected_variant=res.variant_name or detected_variant,
                    reason=res.reason,
                )
            return MappingResolution(
                canonical_item_id=canonical_item.id,
                status="suggested",
                tier="high",
                suggested_ingredient=target_ing,
                matching_confidence=res.confidence,
                mapping_source="catalogue",
                user_confirmed=False,
                detected_variant=res.variant_name or detected_variant,
                reason=res.reason,
            )

    if res.requires_review and res.suggestions:
        top_var_id = res.suggestions[0].get("variant_id")
        if top_var_id:
            sug_ing = (
                await db.execute(
                    select(CanonicalIngredient).where(
                        CanonicalIngredient.variant_id == top_var_id
                    )
                )
            ).scalar_one_or_none()
            if sug_ing is not None:
                return MappingResolution(
                    canonical_item_id=canonical_item.id,
                    status="suggested",
                    tier="medium",
                    suggested_ingredient=sug_ing,
                    matching_confidence=Decimal("0.700"),
                    mapping_source="classifier_v2",
                    user_confirmed=False,
                    detected_variant=detected_variant,
                    reason=res.reason,
                )

    ingredients = list(
        (await db.execute(select(CanonicalIngredient).where(CanonicalIngredient.is_active.is_(True))))
        .scalars()
        .all()
    )
    report.total_items = len(items)

    for item in items:
        existing = await lookup_existing_mapping(
            db, household_id=item.household_id, canonical_item_id=item.id
        )
        if existing is not None:
            ing = await db.get(CanonicalIngredient, existing.canonical_ingredient_id)
            report.reused_count += 1
            report.decisions.append(
                BackfillItemDecision(
                    canonical_item_id=item.id,
                    canonical_item_name=item.name,
                    household_id=item.household_id,
                    action="reused",
                    canonical_ingredient_id=existing.canonical_ingredient_id,
                    canonical_ingredient_name=ing.name if ing else None,
                    matching_confidence=existing.matching_confidence,
                    detected_variant=detect_cooking_variant(item.name),
                    reason="existing_mapping",
                )
            )
            continue

        candidate = score_ingredient_candidates(item, ingredients)
        if candidate is not None and candidate.tier == "high":
            if not dry_run:
                db.add(
                    IngredientMapping(
                        household_id=item.household_id,
                        canonical_item_id=item.id,
                        canonical_ingredient_id=candidate.ingredient.id,
                        matching_confidence=candidate.confidence,
                        mapping_source="catalogue",
                        user_confirmed=False,
                    )
                )
            report.auto_mapped_count += 1
            report.decisions.append(
                BackfillItemDecision(
                    canonical_item_id=item.id,
                    canonical_item_name=item.name,
                    household_id=item.household_id,
                    action="auto_mapped",
                    canonical_ingredient_id=candidate.ingredient.id,
                    canonical_ingredient_name=candidate.ingredient.name,
                    matching_confidence=candidate.confidence,
                    detected_variant=candidate.detected_variant,
                    reason=candidate.reason,
                )
            )
        elif candidate is not None and candidate.tier == "medium":
            report.medium_review_count += 1
            report.decisions.append(
                BackfillItemDecision(
                    canonical_item_id=item.id,
                    canonical_item_name=item.name,
                    household_id=item.household_id,
                    action="needs_review_medium",
                    canonical_ingredient_id=candidate.ingredient.id,
                    canonical_ingredient_name=candidate.ingredient.name,
                    matching_confidence=candidate.confidence,
                    detected_variant=candidate.detected_variant,
                    reason=candidate.reason,
                )
            )
        else:
            report.unmapped_low_count += 1
            report.decisions.append(
                BackfillItemDecision(
                    canonical_item_id=item.id,
                    canonical_item_name=item.name,
                    household_id=item.household_id,
                    action="unmapped_low",
                    matching_confidence=Decimal("0.000"),
                    detected_variant=detect_cooking_variant(item.name),
                    reason="low_confidence_or_non_pantry",
                )
            )

    if not dry_run:
        await db.flush()
    return report
