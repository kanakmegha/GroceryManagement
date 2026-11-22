"""Purchased-product identity resolution service (Phase 3A).

Resolves receipt OCR descriptions against existing CanonicalItem records
without conflating purchased products with generic CanonicalIngredient.
Confidence scores are heuristic ranking scores in [0.000, 1.000], NOT
calibrated empirical probabilities.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Literal
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.ingredient_mapping_service import detect_cooking_variant
from app.modules.inventory.models import CanonicalItem, InventoryLot, ItemAlias

ResolutionOutcome = Literal["match_existing", "review_required", "create_new"]
ConfidenceTier = Literal["high", "medium", "low"]

HIGH_RESOLUTION_THRESHOLD = Decimal("0.900")
MEDIUM_RESOLUTION_THRESHOLD = Decimal("0.650")

_KNOWN_BRAND_TOKENS = {
    "amul",
    "aavin",
    "nandini",
    "mother",
    "heritage",
    "milky",
    "mist",
    "india",
    "gate",
    "daawat",
    "fortune",
    "saffola",
    "dhara",
    "tata",
    "aashirvaad",
    "everest",
    "mdh",
    "catch",
    "mtr",
    "eastern",
    "bb",
    "popular",
    "fresho",
    "dabur",
    "haldiram",
    "haldirams",
    "prabhat",
}

_ABBREVIATIONS: dict[str, str] = {
    "pdr": "powder",
    "pwd": "powder",
    "pkt": "packet",
    "gm": "g",
    "gms": "g",
    "ltr": "l",
    "ltrs": "l",
    "pcs": "pcs",
}

# Known OCR character substitutions on uppercase/receipt tokens
_OCR_TOKEN_CORRECTIONS: dict[str, str] = {
    "chinldf": "chini df",
    "chinidf": "chini df",
    "chiandf": "chini df",
    "chinide": "chini df",
    "chinidf.": "chini df",
    "chinldf.": "chini df",
    "chinide.": "chini df",
    "akrot": "akhrot",
    "dhyli": "dhuli",
    "chaner": "chana",
    "channa": "chana",
    "kallmalka": "kali malka",
    "lalmalka": "lal malka",
    "khulaatta": "khula atta",
    "chaial": "chawal",
    "matchhox": "matchbox",
    "tumeric": "turmeric",
    "corriander": "coriander",
}

_DISTINCT_FORM_TOKENS = {
    "flour",
    "atta",
    "puree",
    "paste",
    "oil",
    "chocolate",
    "kali",
    "lal",
}

_PACK_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:kg|g|gm|gms|l|ltr|ltrs|ml|pcs|pc|pack|pkt)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProductResolutionResult:
    outcome: ResolutionOutcome
    tier: ConfidenceTier
    resolved_canonical_item_id: UUID | None
    resolved_canonical_item_name: str | None
    normalized_description: str
    resolution_method: str
    resolution_score: Decimal
    reason: str


@dataclass
class DuplicateCandidateItem:
    canonical_item_id: UUID
    name: str
    slug: str
    default_base_unit: str
    lot_count: int
    total_remaining_qty: Decimal


@dataclass
class DuplicateCandidateCluster:
    cluster_key: str
    reason: str
    similarity_score: Decimal
    items: list[DuplicateCandidateItem] = field(default_factory=list)


def normalize_product_description(raw_text: str) -> str:
    """Normalize a receipt OCR product description while preserving product identity."""
    nfkc = unicodedata.normalize("NFKC", raw_text or "")
    visible = "".join(ch for ch in nfkc if unicodedata.category(ch) not in {"Cf", "Cc"})
    text = visible.strip().casefold()
    # Strip leading bullets, quotes, and receipt line numbers (e.g., '1 ', '• ', '" ')
    text = re.sub(r'^[\s"\'\•\*\-\.°]+', "", text)
    text = re.sub(r"^\d+\s+", "", text)
    # Strip pack sizes
    text = _PACK_RE.sub(" ", text)
    # Strip trailing standalone price/rate numbers (e.g. '47 94.00', '820', '1120', '100')
    text = re.sub(r"(?:\s+\d+(?:\.\d+)?)+$", "", text)
    # Convert separator hyphens/underscores to spaces and remove punctuation
    text = re.sub(r"[-_\u2013\u2014]+", " ", text)
    text = re.sub(r"[^a-z0-9\s]+", " ", text)

    tokens: list[str] = []
    for tok in text.split():
        if tok in _OCR_TOKEN_CORRECTIONS:
            tokens.extend(_OCR_TOKEN_CORRECTIONS[tok].split())
        elif tok in _ABBREVIATIONS:
            tokens.append(_ABBREVIATIONS[tok])
        else:
            tokens.append(tok)

    # Collapse 'chini df' if split or adjacent
    joined = " ".join(tokens).strip()
    joined = re.sub(r"\bchini\s+df\b", "chini df", joined)
    return joined


def _extract_brands(text: str) -> set[str]:
    toks = set(re.sub(r"[^a-z0-9\s]+", " ", text.lower()).split())
    return toks & _KNOWN_BRAND_TOKENS


def _extract_pack(text: str) -> str | None:
    m = _PACK_RE.search(text.lower())
    return re.sub(r"\s+", "", m.group(0)) if m else None


def _units_compatible(u1: str | None, u2: str | None) -> bool:
    if not u1 or not u2:
        return True
    u1_l, u2_l = u1.lower(), u2.lower()
    if u1_l == u2_l:
        return True
    mass = {"g", "kg"}
    vol = {"ml", "l"}
    return (u1_l in mass and u2_l in mass) or (u1_l in vol and u2_l in vol)


async def resolve_product_identity(
    db: AsyncSession,
    *,
    household_id: UUID,
    raw_description: str,
    brand: str | None = None,
    variant: str | None = None,
    pack_size: str | None = None,
    base_unit: str | None = None,
) -> ProductResolutionResult:
    """Resolve a receipt description against existing CanonicalItems."""
    norm_desc = normalize_product_description(raw_description)
    raw_norm = re.sub(r"[^a-z0-9\s]+", " ", raw_description.strip().lower()).strip()

    # 1. Approved ItemAlias lookup (household-first, then global; follow soft-merged survivors)
    alias_stmt = (
        select(ItemAlias, CanonicalItem)
        .join(CanonicalItem, CanonicalItem.id == ItemAlias.canonical_item_id)
        .where(
            ItemAlias.normalized_alias.in_([norm_desc, raw_norm]),
            (ItemAlias.household_id == household_id) | (ItemAlias.household_id.is_(None)),
        )
        .order_by(ItemAlias.household_id.is_(None))
    )
    alias_row = (await db.execute(alias_stmt)).first()
    if alias_row is not None:
        alias_obj, item_obj = alias_row
        seen_ids: set[UUID] = set()
        while (
            not item_obj.is_active or item_obj.merged_into_id is not None
        ) and item_obj.merged_into_id is not None:
            if item_obj.id in seen_ids:
                break
            seen_ids.add(item_obj.id)
            survivor = await db.get(CanonicalItem, item_obj.merged_into_id)
            if survivor is None:
                break
            item_obj = survivor
        if item_obj.is_active and _units_compatible(base_unit, item_obj.default_base_unit):
            return ProductResolutionResult(
                outcome="match_existing",
                tier="high",
                resolved_canonical_item_id=item_obj.id,
                resolved_canonical_item_name=item_obj.name,
                normalized_description=norm_desc,
                resolution_method="approved_alias",
                resolution_score=Decimal("0.980"),
                reason=f"matched_alias:{alias_obj.normalized_alias}",
            )

    # 2. Candidate CanonicalItems in household or global scope
    items = list(
        (
            await db.execute(
                select(CanonicalItem).where(
                    CanonicalItem.is_active.is_(True),
                    (CanonicalItem.household_id == household_id)
                    | (CanonicalItem.household_id.is_(None)),
                )
            )
        )
        .scalars()
        .all()
    )
    if not items or not norm_desc:
        return ProductResolutionResult(
            outcome="create_new",
            tier="low",
            resolved_canonical_item_id=None,
            resolved_canonical_item_name=None,
            normalized_description=norm_desc,
            resolution_method="new_product",
            resolution_score=Decimal("0.000"),
            reason="no_existing_candidates",
        )

    in_brands = _extract_brands(f"{raw_description} {brand or ''}")
    in_variant = variant.lower() if variant else detect_cooking_variant(raw_description)
    in_pack = pack_size or _extract_pack(raw_description)
    in_forms = set(norm_desc.split()) & _DISTINCT_FORM_TOKENS

    best_score = Decimal("0.000")
    best_item: CanonicalItem | None = None
    best_method = "new_product"
    best_reason = "below_threshold"

    for item in items:
        if not _units_compatible(base_unit, item.default_base_unit):
            continue

        item_norm = normalize_product_description(item.name)
        item_brands = _extract_brands(item.name)
        item_variant = detect_cooking_variant(item.name)
        item_pack = _extract_pack(item.name)
        item_forms = set(item_norm.split()) & _DISTINCT_FORM_TOKENS

        # Different brands, cooking variants, or product forms must NEVER auto-merge
        if in_brands and item_brands and in_brands != item_brands:
            continue
        if in_variant and item_variant and in_variant != item_variant:
            continue
        if in_forms != item_forms:
            continue

        if norm_desc == item_norm:
            # Same normalized product; check pack size
            if in_pack and item_pack and in_pack != item_pack:
                score = Decimal("0.780")
                method = "pack_size_differs"
                reason = f"same_product_diff_pack:{in_pack}_vs_{item_pack}"
            else:
                score = Decimal("0.930")
                method = "normalized_match"
                reason = f"exact_normalized_match:{item_norm}"
        else:
            # Fuzzy / token similarity alone NEVER exceeds MEDIUM (< 0.900)
            seq_ratio = SequenceMatcher(None, norm_desc, item_norm).ratio()
            t1, t2 = set(norm_desc.split()), set(item_norm.split())
            jaccard = len(t1 & t2) / len(t1 | t2) if (t1 | t2) else 0.0
            combined = 0.6 * seq_ratio + 0.4 * jaccard
            if combined < 0.65:
                continue
            score = Decimal(f"{min(0.86, combined):.3f}")
            method = "fuzzy_review"
            reason = f"fuzzy_similarity:{combined:.3f}"

        if score > best_score:
            best_score = score
            best_item = item
            best_method = method
            best_reason = reason

    if best_item is not None and best_score >= HIGH_RESOLUTION_THRESHOLD:
        return ProductResolutionResult(
            outcome="match_existing",
            tier="high",
            resolved_canonical_item_id=best_item.id,
            resolved_canonical_item_name=best_item.name,
            normalized_description=norm_desc,
            resolution_method=best_method,
            resolution_score=best_score,
            reason=best_reason,
        )

    if best_item is not None and best_score >= MEDIUM_RESOLUTION_THRESHOLD:
        return ProductResolutionResult(
            outcome="review_required",
            tier="medium",
            resolved_canonical_item_id=best_item.id,
            resolved_canonical_item_name=best_item.name,
            normalized_description=norm_desc,
            resolution_method=best_method,
            resolution_score=best_score,
            reason=best_reason,
        )

    return ProductResolutionResult(
        outcome="create_new",
        tier="low",
        resolved_canonical_item_id=None,
        resolved_canonical_item_name=None,
        normalized_description=norm_desc,
        resolution_method="new_product",
        resolution_score=best_score,
        reason=best_reason,
    )


async def learn_confirmed_product_alias(
    db: AsyncSession,
    *,
    household_id: UUID,
    canonical_item_id: UUID,
    raw_description: str,
) -> ItemAlias | None:
    """Persist an approved household ItemAlias ONLY after user confirmation, with conflict
    checks.
    """
    norm_alias = normalize_product_description(raw_description)
    if not norm_alias:
        norm_alias = re.sub(r"[^a-z0-9\s]+", " ", raw_description.strip().lower()).strip()
    if not norm_alias:
        return None

    target_item = await db.get(CanonicalItem, canonical_item_id)
    if target_item is None or not target_item.is_active:
        return None

    existing = (
        await db.execute(
            select(ItemAlias).where(
                ItemAlias.household_id == household_id,
                ItemAlias.normalized_alias == norm_alias,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        # Conflict check: never silently overwrite an alias pointing to another active CanonicalItem
        return existing

    alias = ItemAlias(
        household_id=household_id,
        canonical_item_id=canonical_item_id,
        normalized_alias=norm_alias,
        source="user_confirmed",
        alias_kind="receipt_ocr",
    )
    db.add(alias)
    await db.flush()
    return alias


async def get_or_create_canonical_item(
    db: AsyncSession,
    *,
    household_id: UUID,
    raw_name: str,
    category: str = "general",
    secondary_category: str | None = None,
    default_base_unit: str = "pcs",
    default_shelf_life_days: int | None = None,
    low_stock_threshold_qty: Decimal = Decimal("0"),
    brand: str | None = None,
    variant: str | None = None,
    pack_size: str | None = None,
    slug_override: str | None = None,
    canonical_name_override: str | None = None,
) -> tuple[CanonicalItem, ProductResolutionResult, bool]:
    """Resolve an existing active CanonicalItem or safely create a new one with concurrency
    protection.

    Returns ``(canonical_item, resolution_result, created_new)``.
    """
    res = await resolve_product_identity(
        db,
        household_id=household_id,
        raw_description=raw_name,
        brand=brand,
        variant=variant,
        pack_size=pack_size,
        base_unit=default_base_unit,
    )
    if res.outcome == "match_existing" and res.resolved_canonical_item_id is not None:
        existing = await db.get(CanonicalItem, res.resolved_canonical_item_id)
        if existing is not None and existing.is_active:
            return existing, res, False

    from app.modules.catalog.product_naming_service import standardize_product_name

    naming = standardize_product_name(canonical_name_override or raw_name)
    clean_name = (naming.canonical_name or canonical_name_override or raw_name).strip()
    slug = slug_override or re.sub(r"[^a-z0-9]+", "-", clean_name.lower()).strip("-") or "item"

    # Exact slug check (follow soft-merged survivor if merged)
    existing_by_slug = (
        (
            await db.execute(
                select(CanonicalItem)
                .where(
                    CanonicalItem.slug == slug,
                    or_(
                        CanonicalItem.household_id == household_id,
                        CanonicalItem.household_id.is_(None),
                    ),
                )
                .order_by(CanonicalItem.household_id.is_(None).asc())
            )
        )
        .scalars()
        .first()
    )
    if existing_by_slug is not None:
        cur = existing_by_slug
        seen: set[UUID] = set()
        while (
            not cur.is_active or cur.merged_into_id is not None
        ) and cur.merged_into_id is not None:
            if cur.id in seen:
                break
            seen.add(cur.id)
            surv = await db.get(CanonicalItem, cur.merged_into_id)
            if surv is None:
                break
            cur = surv
        if cur.is_active:
            return cur, res, False

    try:
        async with db.begin_nested():
            item = CanonicalItem(
                household_id=household_id,
                slug=slug,
                name=clean_name,
                category=category.strip().lower() or "general",
                secondary_category=secondary_category,
                default_base_unit=default_base_unit,
                default_shelf_life_days=default_shelf_life_days,
                low_stock_threshold_qty=low_stock_threshold_qty,
            )
            db.add(item)
            await db.flush()
        return item, res, True
    except IntegrityError:
        winner = (
            await db.execute(
                select(CanonicalItem).where(
                    CanonicalItem.household_id == household_id,
                    CanonicalItem.slug == slug,
                    CanonicalItem.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if winner is None:
            raise
        return winner, res, False


async def detect_duplicate_product_candidates(
    db: AsyncSession,
    *,
    household_id: UUID,
) -> list[DuplicateCandidateCluster]:
    """Read-only duplicate candidate clustering for historical CanonicalItems."""
    items = list(
        (
            await db.execute(
                select(CanonicalItem)
                .where(
                    CanonicalItem.is_active.is_(True),
                    CanonicalItem.household_id == household_id,
                )
                .order_by(CanonicalItem.name)
            )
        )
        .scalars()
        .all()
    )

    lot_stats_rows = (
        await db.execute(
            select(
                InventoryLot.canonical_item_id,
                func.count(InventoryLot.id),
                func.coalesce(func.sum(InventoryLot.remaining_qty), 0),
            )
            .where(InventoryLot.household_id == household_id)
            .group_by(InventoryLot.canonical_item_id)
        )
    ).all()
    lot_stats = {row[0]: (int(row[1]), Decimal(str(row[2]))) for row in lot_stats_rows}

    groups: dict[str, list[CanonicalItem]] = {}
    for item in items:
        key = normalize_product_description(item.name) or item.slug
        groups.setdefault(f"{key}::{item.default_base_unit}", []).append(item)

    clusters: list[DuplicateCandidateCluster] = []
    for full_key, grp in groups.items():
        if len(grp) < 2:
            continue
        key_name = full_key.split("::", 1)[0]
        clusters.append(
            DuplicateCandidateCluster(
                cluster_key=key_name,
                reason="identical_normalized_ocr_description_and_unit",
                similarity_score=Decimal("0.930"),
                items=[
                    DuplicateCandidateItem(
                        canonical_item_id=it.id,
                        name=it.name,
                        slug=it.slug,
                        default_base_unit=it.default_base_unit,
                        lot_count=lot_stats.get(it.id, (0, Decimal("0")))[0],
                        total_remaining_qty=lot_stats.get(it.id, (0, Decimal("0")))[1],
                    )
                    for it in grp
                ],
            )
        )
    return clusters
