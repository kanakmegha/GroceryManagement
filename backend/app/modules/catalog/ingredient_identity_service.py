"""Ingredient normalization, equivalence detection, and canonical naming service (Phase 4A).

Confidence scores are heuristic ranking scores in [0.000, 1.000] and are NOT
empirically calibrated probabilities.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.inventory.models import CanonicalIngredient, IngredientAlias, IngredientMapping

ConfidenceTier = Literal["high", "medium", "low"]

HIGH_THRESHOLD = Decimal("0.900")
MEDIUM_THRESHOLD = Decimal("0.650")

# Descriptive attributes preserved separately rather than discarded
_ATTRIBUTE_TOKENS = {
    "fresh",
    "red",
    "green",
    "yellow",
    "white",
    "black",
    "brown",
    "organic",
    "natural",
    "pure",
    "raw",
    "ripe",
    "baby",
    "small",
    "large",
    "desi",
    "hybrid",
    "premium",
    "select",
    "unpolished",
    "polished",
    "cold",
    "pressed",
}

# Physical/processing forms that alter cooking identity
_FORM_TOKENS = {
    "puree",
    "paste",
    "sauce",
    "ketchup",
    "powder",
    "flour",
    "atta",
    "maida",
    "besan",
    "flakes",
    "poha",
    "whole",
    "split",
    "crushed",
    "chopped",
    "dried",
    "frozen",
    "roasted",
    "milk",
    "oil",
    "ghee",
    "butter",
}

_PACK_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:kg|g|gm|gms|l|ltr|ltrs|ml|pcs|pc|pack|pkt)\b",
    re.IGNORECASE,
)

# Canonical naming dictionary: normalized_alias/transliteration -> (canonical_normalized_name,
# preferred_english_name, language)
CANONICAL_NAMING_DICTIONARY: dict[str, tuple[str, str, str]] = {
    "badam": ("almonds", "Almonds", "hi"),
    "almond": ("almonds", "Almonds", "en"),
    "almonds": ("almonds", "Almonds", "en"),
    "akhrot": ("walnuts", "Walnuts", "hi"),
    "walnut": ("walnuts", "Walnuts", "en"),
    "walnuts": ("walnuts", "Walnuts", "en"),
    "chini": ("sugar", "Sugar", "hi"),
    "cheeni": ("sugar", "Sugar", "hi"),
    "sugar": ("sugar", "Sugar", "en"),
    "atta": ("whole_wheat_flour", "Whole Wheat Flour", "hi"),
    "whole wheat atta": ("whole_wheat_flour", "Whole Wheat Flour", "hi"),
    "whole wheat flour": ("whole_wheat_flour", "Whole Wheat Flour", "en"),
    "rava": ("semolina", "Semolina", "hi"),
    "sooji": ("semolina", "Semolina", "hi"),
    "suji": ("semolina", "Semolina", "hi"),
    "semolina": ("semolina", "Semolina", "en"),
    "tamatar": ("tomato", "Tomato", "hi"),
    "tomato": ("tomato", "Tomato", "en"),
    "tomatoes": ("tomato", "Tomato", "en"),
    "pyaz": ("onion", "Onion", "hi"),
    "kanda": ("onion", "Onion", "hi"),
    "onion": ("onion", "Onion", "en"),
    "onions": ("onion", "Onion", "en"),
    "aloo": ("potato", "Potato", "hi"),
    "batata": ("potato", "Potato", "hi"),
    "potato": ("potato", "Potato", "en"),
    "potatoes": ("potato", "Potato", "en"),
    "palak": ("spinach", "Spinach", "hi"),
    "spinach": ("spinach", "Spinach", "en"),
    "haldi": ("turmeric_powder", "Turmeric Powder", "hi"),
    "turmeric powder": ("turmeric_powder", "Turmeric Powder", "en"),
    "jeera": ("cumin_seeds", "Cumin Seeds", "hi"),
    "cumin seeds": ("cumin_seeds", "Cumin Seeds", "en"),
    "rai": ("mustard_seeds", "Mustard Seeds", "hi"),
    "mustard seeds": ("mustard_seeds", "Mustard Seeds", "en"),
    "til": ("sesame_seeds", "Sesame Seeds", "hi"),
    "sesame seeds": ("sesame_seeds", "Sesame Seeds", "en"),
    "arhar dal": ("toor_dal", "Toor Dal", "hi"),
    "tur dal": ("toor_dal", "Toor Dal", "hi"),
    "toor dal": ("toor_dal", "Toor Dal", "en"),
    "kabuli chana": ("chickpeas", "Chickpeas", "hi"),
    "chickpeas": ("chickpeas", "Chickpeas", "en"),
    "dahi": ("curd", "Curd", "hi"),
    "curd": ("curd", "Curd", "en"),
    "yogurt": ("curd", "Curd", "en"),
    "milk": ("milk", "Milk", "en"),
    "doodh": ("milk", "Milk", "hi"),
    "kaju": ("cashews", "Cashews", "hi"),
    "cashews": ("cashews", "Cashews", "en"),
    "basmati rice": ("basmati_rice", "Basmati Rice", "en"),
    "idli rice": ("idli_rice", "Idli Rice", "en"),
    "sona masoori rice": ("sona_masoori_rice", "Sona Masoori Rice", "en"),
    "sunflower oil": ("sunflower_oil", "Sunflower Oil", "en"),
    "mustard oil": ("mustard_oil", "Mustard Oil", "en"),
    "coconut oil": ("coconut_oil", "Coconut Oil", "en"),
    "rice bran oil": ("rice_bran_oil", "Rice Bran Oil", "en"),
    "rice flour": ("rice_flour", "Rice Flour", "en"),
    "coconut milk": ("coconut_milk", "Coconut Milk", "en"),
    "tomato puree": ("tomato_puree", "Tomato Puree", "en"),
}

# Explicit incompatible pairs (must NEVER auto-merge)
_INCOMPATIBLE_PAIRS: set[frozenset[str]] = {
    frozenset({"basmati_rice", "idli_rice"}),
    frozenset({"basmati_rice", "sona_masoori_rice"}),
    frozenset({"idli_rice", "sona_masoori_rice"}),
    frozenset({"rice_flour", "basmati_rice"}),
    frozenset({"rice_flour", "idli_rice"}),
    frozenset({"rice_flour", "sona_masoori_rice"}),
    frozenset({"coconut_milk", "milk"}),
    frozenset({"mustard_oil", "sunflower_oil"}),
    frozenset({"coconut_oil", "sunflower_oil"}),
    frozenset({"tomato_puree", "tomato"}),
}


@dataclass(frozen=True)
class NormalizedIngredientInfo:
    raw_input: str
    core_normalized_name: str
    preferred_name: str
    synonyms: list[str]
    language: str
    transliterations: list[str]
    attributes: list[str]
    physical_form: str | None
    packaging_qty: str | None
    ocr_noise_tokens: list[str]
    ocr_candidates: list[str] = field(default_factory=list)
    normalization_ops: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class IngredientResolutionResult:
    normalized: NormalizedIngredientInfo
    matched_ingredient_id: UUID | None
    matched_ingredient_name: str | None
    tier: ConfidenceTier
    confidence: Decimal
    method: str
    incompatible_with: list[str] = field(default_factory=list)


@dataclass
class IngredientDuplicateCandidate:
    cluster_key: str
    proposed_canonical_name: str
    category: str
    tier: ConfidenceTier
    confidence: Decimal
    reason: str
    potential_incompatibilities: list[str]
    ingredient_ids: list[UUID]
    original_names: list[str]
    mapped_product_counts: dict[str, int]
    household_mapping_count: int


_OCR_DIGIT_SUBS: dict[str, tuple[str, ...]] = {
    "0": ("o",),
    "1": ("i", "l"),
    "4": ("a",),
    "5": ("s",),
    "8": ("b",),
}


# Approved spelling variants (exact normalized phrase -> canonical normalized phrase)
_SPELLING_VARIANTS: dict[str, str] = {
    "channa dal": "chana dal",
    "chana daal": "chana dal",
    "corriander": "coriander",
    "corriander powder": "coriander powder",
    "corriander seeds": "coriander seeds",
    "moong dhyli": "moong dhuli",
    "tumeric": "turmeric",
    "tumeric powder": "turmeric powder",
    "tomatoe": "tomato",
    "potatoe": "potato",
    "walnut": "walnuts",
    "almond": "almonds",
    "cashew": "cashews",
    "peanut": "peanuts",
    "flax seed": "flax seeds",
    "flaxseed": "flax seeds",
    "flaxseeds": "flax seeds",
    "apples": "apple",
    "bananas": "banana",
    "lemons": "lemon",
    "mangoes": "mango",
    "yoghurt": "yogurt",
    "mung dal": "moong dal",
    "sona masuri rice": "sona masoori rice",
}


def normalize_ingredient_name_key(value: str) -> str:
    """Deterministic text normalization key for ingredient names.

    Applies Unicode NFKC, strips invisible control/format chars, case-folds,
    converts separator hyphens/underscores to spaces, removes non-semantic
    punctuation, and collapses whitespace.
    """
    nfkc = unicodedata.normalize("NFKC", value)
    # Remove invisible format/control characters (Cf, Cc)
    visible = "".join(ch for ch in nfkc if unicodedata.category(ch) not in {"Cf", "Cc"})
    folded = visible.casefold()
    # Replace separator hyphens and underscores with spaces
    sep_norm = re.sub(r"[-_\u2013\u2014]+", " ", folded)
    # Remove non-semantic punctuation
    no_punct = re.sub(r"[^\w\s]", " ", sep_norm, flags=re.UNICODE)
    return re.sub(r"\s+", " ", no_punct).strip()


def _generate_token_ocr_variants(token: str) -> list[str]:
    """Generate context-aware OCR substitutions ONLY for alphabetic/mixed tokens (never pure
    numbers).
    """
    if not any(ch.isalpha() for ch in token) or not any(ch in _OCR_DIGIT_SUBS for ch in token):
        return [token]
    variants = [""]
    for ch in token:
        choices = _OCR_DIGIT_SUBS.get(ch, (ch,))
        variants = [prefix + c for prefix in variants for c in choices]
        if len(variants) > 16:
            variants = variants[:16]
    return variants


def _singularize_token(tok: str) -> str:
    if len(tok) > 4 and tok.endswith("oes"):
        return tok[:-2]
    if len(tok) > 4 and tok.endswith("ies"):
        return tok[:-3] + "y"
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        return tok[:-1]
    return tok


def normalize_ingredient_input(raw_text: str) -> NormalizedIngredientInfo:
    """Extract core ingredient, synonyms, transliterations, attributes, form, and pack info."""
    pack_match = _PACK_RE.search(raw_text)
    packaging_qty = pack_match.group(0).strip() if pack_match else None

    cleaned = _PACK_RE.sub(" ", raw_text.strip().lower())
    # Strip parenthetical synonyms cleanly while preserving them
    paren_syns = re.findall(r"\(([^)]+)\)", cleaned)
    cleaned_no_paren = re.sub(r"\([^)]*\)", " ", cleaned)
    noise_tokens = re.findall(r"[^a-z0-9\s]+", cleaned_no_paren)
    words = re.sub(r"[^a-z0-9\s]+", " ", cleaned_no_paren).split()

    attributes: list[str] = []
    core_words: list[str] = []
    physical_form: str | None = None

    for w in words:
        if w in _ATTRIBUTE_TOKENS:
            attributes.append(w)
            continue
        if w in _FORM_TOKENS:
            physical_form = w
        core_words.append(w)

    if not core_words:
        core_words = list(words)

    phrase = " ".join(core_words)
    spelling_phrase = _SPELLING_VARIANTS.get(phrase, phrase)
    singular_phrase = " ".join(_singularize_token(w) for w in spelling_phrase.split())

    dict_hit = (
        CANONICAL_NAMING_DICTIONARY.get(spelling_phrase)
        or CANONICAL_NAMING_DICTIONARY.get(phrase)
        or CANONICAL_NAMING_DICTIONARY.get(singular_phrase)
    )
    if dict_hit is not None:
        core_norm, pref_name, lang = dict_hit
    else:
        norm_words = spelling_phrase.split()
        core_norm = "_".join(norm_words)
        pref_name = " ".join(w.capitalize() for w in norm_words)
        lang = "en"

    synonyms = [s.strip() for p in paren_syns for s in p.split("/") if s.strip()]
    if phrase != pref_name.lower() and phrase not in synonyms:
        synonyms.append(phrase)
    transliterations = [phrase] if lang != "en" else []

    # Context-aware OCR candidate phrases (only when mixed alphanumeric OCR digits exist)
    ocr_candidates: list[str] = []
    normalization_ops: list[str] = []
    if any(
        any(c.isalpha() for c in w) and any(c in _OCR_DIGIT_SUBS for c in w) for w in core_words
    ):
        per_word_opts = [_generate_token_ocr_variants(w) for w in core_words]
        combos = [""]
        for opts in per_word_opts:
            combos = [f"{p} {o}".strip() for p in combos for o in opts]
            if len(combos) > 24:
                combos = combos[:24]
        for cand_phrase in combos:
            if cand_phrase != phrase and cand_phrase not in ocr_candidates:
                ocr_candidates.append(cand_phrase)
        if ocr_candidates:
            normalization_ops.append(f"ocr_candidates_generated:{phrase}->{len(ocr_candidates)}")

    return NormalizedIngredientInfo(
        raw_input=raw_text,
        core_normalized_name=core_norm,
        preferred_name=pref_name,
        synonyms=synonyms,
        language=lang,
        transliterations=transliterations,
        attributes=attributes,
        physical_form=physical_form,
        packaging_qty=packaging_qty,
        ocr_noise_tokens=noise_tokens,
        ocr_candidates=ocr_candidates,
        normalization_ops=normalization_ops,
    )


def are_ingredients_incompatible(norm_a: str, norm_b: str) -> bool:
    if frozenset({norm_a, norm_b}) in _INCOMPATIBLE_PAIRS:
        return True
    # Processed form vs raw whole produce guard
    if (
        norm_a.endswith("_puree") or norm_a.endswith("_paste") or norm_a.endswith("_flour")
    ) and not (norm_b.endswith("_puree") or norm_b.endswith("_paste") or norm_b.endswith("_flour")):
        return True
    return bool(
        (norm_b.endswith("_puree") or norm_b.endswith("_paste") or norm_b.endswith("_flour"))
        and not (
            norm_a.endswith("_puree") or norm_a.endswith("_paste") or norm_a.endswith("_flour")
        )
    )


async def resolve_ingredient_identity(
    db: AsyncSession,
    *,
    raw_name: str,
    household_id: UUID | None = None,
) -> IngredientResolutionResult:
    """Resolve an ingredient string against DB aliases and active CanonicalIngredients."""
    norm = normalize_ingredient_input(raw_name)
    ingredients = list(
        (
            await db.execute(
                select(CanonicalIngredient).where(CanonicalIngredient.is_active.is_(True))
            )
        )
        .scalars()
        .all()
    )
    by_id = {ing.id: ing for ing in ingredients}

    # 1. Database-backed approved IngredientAlias lookup (household-first, then global)
    lookup_phrases = list(
        dict.fromkeys(
            [
                norm.core_normalized_name.replace("_", " "),
                raw_name.strip().lower(),
                *norm.synonyms,
            ]
        )
    )
    if household_id is not None:
        alias_stmt = (
            select(IngredientAlias)
            .where(
                IngredientAlias.is_approved.is_(True),
                IngredientAlias.normalized_alias.in_(lookup_phrases),
                (IngredientAlias.household_id == household_id)
                | (IngredientAlias.household_id.is_(None)),
            )
            .order_by(IngredientAlias.household_id.is_(None))
        )
    else:
        alias_stmt = select(IngredientAlias).where(
            IngredientAlias.is_approved.is_(True),
            IngredientAlias.household_id.is_(None),
            IngredientAlias.normalized_alias.in_(lookup_phrases),
        )
    alias_hit = (await db.execute(alias_stmt)).scalars().first()
    if alias_hit is not None and alias_hit.canonical_ingredient_id in by_id:
        ing = by_id[alias_hit.canonical_ingredient_id]
        return IngredientResolutionResult(
            normalized=norm,
            matched_ingredient_id=ing.id,
            matched_ingredient_name=ing.name,
            tier="high",
            confidence=Decimal("0.980"),
            method="db_alias",
        )

    # 2. Exact normalized_name or canonical dictionary match
    for ing in ingredients:
        if ing.normalized_name == norm.core_normalized_name:
            method = (
                "transliteration_dictionary"
                if norm.language != "en"
                else ("attribute_normalized" if norm.attributes else "exact_normalized")
            )
            conf = Decimal("0.980") if method == "exact_normalized" else Decimal("0.950")
            return IngredientResolutionResult(
                normalized=norm,
                matched_ingredient_id=ing.id,
                matched_ingredient_name=ing.name,
                tier="high",
                confidence=conf,
                method=method,
            )

    # 3. Context-aware OCR candidate matching (never exceeds MEDIUM without prior approval)
    if norm.ocr_candidates:
        matched_ocr_ings: list[CanonicalIngredient] = []
        seen_ids: set[UUID] = set()
        for cand_phrase in norm.ocr_candidates:
            cand_norm_info = normalize_ingredient_input(cand_phrase)
            for ing in ingredients:
                if (
                    ing.normalized_name == cand_norm_info.core_normalized_name
                    and ing.id not in seen_ids
                ):
                    matched_ocr_ings.append(ing)
                    seen_ids.add(ing.id)
        if len(matched_ocr_ings) == 1:
            ing = matched_ocr_ings[0]
            return IngredientResolutionResult(
                normalized=norm,
                matched_ingredient_id=ing.id,
                matched_ingredient_name=ing.name,
                tier="medium",
                confidence=Decimal("0.840"),
                method="ocr_candidate_review",
            )
        if len(matched_ocr_ings) > 1:
            return IngredientResolutionResult(
                normalized=norm,
                matched_ingredient_id=matched_ocr_ings[0].id,
                matched_ingredient_name=matched_ocr_ings[0].name,
                tier="medium",
                confidence=Decimal("0.700"),
                method="ocr_ambiguous_review",
            )

    # 4. Fuzzy/token similarity (NEVER exceeds MEDIUM)
    best_score = 0.0
    best_ing: CanonicalIngredient | None = None
    incomp: list[str] = []
    for ing in ingredients:
        if are_ingredients_incompatible(norm.core_normalized_name, ing.normalized_name):
            incomp.append(ing.normalized_name)
            continue
        ratio = SequenceMatcher(
            None, norm.core_normalized_name.replace("_", " "), ing.normalized_name.replace("_", " ")
        ).ratio()
        if ratio > best_score:
            best_score = ratio
            best_ing = ing

    if best_ing is not None and best_score >= 0.72:
        return IngredientResolutionResult(
            normalized=norm,
            matched_ingredient_id=best_ing.id,
            matched_ingredient_name=best_ing.name,
            tier="medium",
            confidence=Decimal(f"{min(0.85, best_score):.3f}"),
            method="token_similarity_review",
            incompatible_with=incomp,
        )

    return IngredientResolutionResult(
        normalized=norm,
        matched_ingredient_id=None,
        matched_ingredient_name=None,
        tier="low",
        confidence=Decimal(f"{best_score:.3f}"),
        method="unmatched",
        incompatible_with=incomp,
    )


async def detect_ingredient_duplicate_candidates(
    db: AsyncSession,
) -> list[IngredientDuplicateCandidate]:
    """Read-only discovery of candidate duplicate CanonicalIngredients."""
    ings = list(
        (
            await db.execute(
                select(CanonicalIngredient)
                .where(CanonicalIngredient.is_active.is_(True))
                .order_by(CanonicalIngredient.category, CanonicalIngredient.name)
            )
        )
        .scalars()
        .all()
    )

    map_rows = (
        await db.execute(
            select(
                IngredientMapping.canonical_ingredient_id,
                func.count(IngredientMapping.id),
                func.count(IngredientMapping.household_id),
            ).group_by(IngredientMapping.canonical_ingredient_id)
        )
    ).all()
    map_counts = {r[0]: (int(r[1]), int(r[2])) for r in map_rows}

    groups: dict[str, list[CanonicalIngredient]] = {}
    for ing in ings:
        norm_info = normalize_ingredient_input(ing.name)
        groups.setdefault(norm_info.core_normalized_name, []).append(ing)

    candidates: list[IngredientDuplicateCandidate] = []
    for key, grp in groups.items():
        if len(grp) < 2:
            continue
        hh_cnt = sum(map_counts.get(g.id, (0, 0))[1] for g in grp)
        candidates.append(
            IngredientDuplicateCandidate(
                cluster_key=key,
                proposed_canonical_name=normalize_ingredient_input(grp[0].name).preferred_name,
                category=grp[0].category,
                tier="high",
                confidence=Decimal("0.950"),
                reason="identical_core_normalized_ingredient",
                potential_incompatibilities=[],
                ingredient_ids=[g.id for g in grp],
                original_names=[g.name for g in grp],
                mapped_product_counts={str(g.id): map_counts.get(g.id, (0, 0))[0] for g in grp},
                household_mapping_count=hh_cnt,
            )
        )
    return candidates


async def register_ingredient_alias(
    db: AsyncSession,
    *,
    household_id: UUID | None,
    canonical_ingredient_id: UUID,
    alias: str,
    language: str = "en",
    alias_type: str = "synonym",
    source: str = "user_confirmed",
) -> IngredientAlias:
    """Persist a global or household-scoped IngredientAlias with conflict protection."""
    ing = await db.get(CanonicalIngredient, canonical_ingredient_id)
    if ing is None or not ing.is_active:
        raise AppError(status_code=404, code="NOT_FOUND", message="CanonicalIngredient not found.")

    norm_alias = re.sub(r"\s+", " ", alias.strip().lower())
    if not norm_alias:
        raise AppError(status_code=422, code="VALIDATION_ERROR", message="Alias cannot be empty.")

    if household_id is None:
        stmt = select(IngredientAlias).where(
            IngredientAlias.household_id.is_(None),
            IngredientAlias.normalized_alias == norm_alias,
        )
    else:
        stmt = select(IngredientAlias).where(
            IngredientAlias.household_id == household_id,
            IngredientAlias.normalized_alias == norm_alias,
        )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        if existing.canonical_ingredient_id == canonical_ingredient_id:
            return existing
        raise AppError(
            status_code=409,
            code="ALIAS_CONFLICT",
            message=f"Alias '{norm_alias}' is already assigned to another ingredient.",
        )

    obj = IngredientAlias(
        household_id=household_id,
        canonical_ingredient_id=canonical_ingredient_id,
        alias=alias.strip(),
        normalized_alias=norm_alias,
        language=language,
        alias_type=alias_type,
        source=source,
        confidence=Decimal("1.000"),
        is_approved=True,
    )
    db.add(obj)
    await db.flush()
    return obj


@dataclass(frozen=True)
class IngredientCreationOutcome:
    ingredient: CanonicalIngredient | None
    action: Literal["reused_existing", "created_new", "review_required"]
    resolution: IngredientResolutionResult


async def get_or_create_canonical_ingredient(
    db: AsyncSession,
    *,
    name: str,
    category: str,
    subcategory: str | None = None,
    standard_unit: str = "g",
    cooking_variants: list[str] | None = None,
    supported_units: list[str] | None = None,
    household_id: UUID | None = None,
    allow_create_on_medium: bool = False,
) -> IngredientCreationOutcome:
    """Unified duplicate-prevention guard for CanonicalIngredient creation."""
    res = await resolve_ingredient_identity(db, raw_name=name, household_id=household_id)
    if res.tier == "high" and res.matched_ingredient_id is not None:
        existing = await db.get(CanonicalIngredient, res.matched_ingredient_id)
        if existing is not None:
            return IngredientCreationOutcome(
                ingredient=existing, action="reused_existing", resolution=res
            )

    if res.tier == "medium" and not allow_create_on_medium:
        return IngredientCreationOutcome(ingredient=None, action="review_required", resolution=res)

    key_norm = normalize_ingredient_name_key(name).replace(" ", "_")
    norm_name = res.normalized.core_normalized_name or key_norm
    pref_name = res.normalized.preferred_name or name.strip()

    try:
        async with db.begin_nested():
            ing = CanonicalIngredient(
                name=pref_name,
                normalized_name=norm_name,
                category=category,
                subcategory=subcategory,
                standard_unit=standard_unit,
                cooking_variants=list(cooking_variants or []),
                supported_units=list(supported_units or [standard_unit]),
            )
            db.add(ing)
            await db.flush()
        return IngredientCreationOutcome(ingredient=ing, action="created_new", resolution=res)
    except IntegrityError:
        existing = (
            await db.execute(
                select(CanonicalIngredient).where(CanonicalIngredient.normalized_name == norm_name)
            )
        ).scalar_one_or_none()
        if existing is None:
            raise
        return IngredientCreationOutcome(
            ingredient=existing, action="reused_existing", resolution=res
        )


@dataclass
class DuplicateAuditClusterV2:
    audit_category: Literal[
        "FORMATTING_DUPLICATE",
        "SPELLING_DUPLICATE",
        "ALIAS_DUPLICATE",
        "POTENTIAL_SEMANTIC_DUPLICATE",
        "SIMILAR_BUT_DISTINCT",
    ]
    cluster_key: str
    ingredient_ids: list[UUID]
    original_names: list[str]
    normalized_keys: list[str]
    categories: list[str]
    standard_units: list[str]
    alias_counts: dict[str, int]
    mapping_counts: dict[str, int]
    compatible: bool
    confidence_tier: ConfidenceTier
    suggested_survivor_id: UUID | None
    recommended_action: str


async def audit_ingredient_duplicates_v2(db: AsyncSession) -> list[DuplicateAuditClusterV2]:
    """Read-only categorized audit across all active CanonicalIngredients."""
    ings = list(
        (
            await db.execute(
                select(CanonicalIngredient)
                .where(CanonicalIngredient.is_active.is_(True))
                .order_by(CanonicalIngredient.category, CanonicalIngredient.name)
            )
        )
        .scalars()
        .all()
    )
    map_rows = (
        await db.execute(
            select(
                IngredientMapping.canonical_ingredient_id, func.count(IngredientMapping.id)
            ).group_by(IngredientMapping.canonical_ingredient_id)
        )
    ).all()
    map_cnts = {r[0]: int(r[1]) for r in map_rows}

    al_rows = (
        await db.execute(
            select(
                IngredientAlias.canonical_ingredient_id, func.count(IngredientAlias.id)
            ).group_by(IngredientAlias.canonical_ingredient_id)
        )
    ).all()
    al_cnts = {r[0]: int(r[1]) for r in al_rows}

    clusters: list[DuplicateAuditClusterV2] = []

    # A. Formatting duplicates (same normalize_ingredient_name_key)
    by_fmt: dict[str, list[CanonicalIngredient]] = {}
    for ing in ings:
        by_fmt.setdefault(normalize_ingredient_name_key(ing.name), []).append(ing)
    for k, grp in by_fmt.items():
        if len(grp) >= 2:
            clusters.append(
                DuplicateAuditClusterV2(
                    audit_category="FORMATTING_DUPLICATE",
                    cluster_key=k,
                    ingredient_ids=[g.id for g in grp],
                    original_names=[g.name for g in grp],
                    normalized_keys=[normalize_ingredient_name_key(g.name) for g in grp],
                    categories=[g.category for g in grp],
                    standard_units=[g.standard_unit for g in grp],
                    alias_counts={str(g.id): al_cnts.get(g.id, 0) for g in grp},
                    mapping_counts={str(g.id): map_cnts.get(g.id, 0) for g in grp},
                    compatible=True,
                    confidence_tier="high",
                    suggested_survivor_id=grp[0].id,
                    recommended_action="SAFE_MERGE_CANDIDATE",
                )
            )

    # B. Spelling / core duplicates (same core_normalized_name but different formatting key)
    by_core: dict[str, list[CanonicalIngredient]] = {}
    for ing in ings:
        by_core.setdefault(normalize_ingredient_input(ing.name).core_normalized_name, []).append(
            ing
        )
    for k, grp in by_core.items():
        if len(grp) >= 2 and len({normalize_ingredient_name_key(g.name) for g in grp}) > 1:
            clusters.append(
                DuplicateAuditClusterV2(
                    audit_category="SPELLING_DUPLICATE",
                    cluster_key=k,
                    ingredient_ids=[g.id for g in grp],
                    original_names=[g.name for g in grp],
                    normalized_keys=[normalize_ingredient_name_key(g.name) for g in grp],
                    categories=[g.category for g in grp],
                    standard_units=[g.standard_unit for g in grp],
                    alias_counts={str(g.id): al_cnts.get(g.id, 0) for g in grp},
                    mapping_counts={str(g.id): map_cnts.get(g.id, 0) for g in grp},
                    compatible=True,
                    confidence_tier="high",
                    suggested_survivor_id=grp[0].id,
                    recommended_action="REVIEW_AND_MERGE",
                )
            )

    # E. Similar but distinct (incompatible pairs present in DB)
    by_norm = {ing.normalized_name: ing for ing in ings}
    for pair in sorted(sorted(p) for p in _INCOMPATIBLE_PAIRS):
        a_norm, b_norm = pair[0], pair[1]
        if a_norm in by_norm and b_norm in by_norm:
            ga, gb = by_norm[a_norm], by_norm[b_norm]
            clusters.append(
                DuplicateAuditClusterV2(
                    audit_category="SIMILAR_BUT_DISTINCT",
                    cluster_key=f"{a_norm}__vs__{b_norm}",
                    ingredient_ids=[ga.id, gb.id],
                    original_names=[ga.name, gb.name],
                    normalized_keys=[
                        normalize_ingredient_name_key(ga.name),
                        normalize_ingredient_name_key(gb.name),
                    ],
                    categories=[ga.category, gb.category],
                    standard_units=[ga.standard_unit, gb.standard_unit],
                    alias_counts={
                        str(ga.id): al_cnts.get(ga.id, 0),
                        str(gb.id): al_cnts.get(gb.id, 0),
                    },
                    mapping_counts={
                        str(ga.id): map_cnts.get(ga.id, 0),
                        str(gb.id): map_cnts.get(gb.id, 0),
                    },
                    compatible=False,
                    confidence_tier="high",
                    suggested_survivor_id=None,
                    recommended_action="KEEP_DISTINCT_DO_NOT_MERGE",
                )
            )

    return clusters
