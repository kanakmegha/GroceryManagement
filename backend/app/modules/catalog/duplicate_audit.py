"""Strictly read-only dry-run duplicate audit for canonical_items."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.canonical_resolver import clean_canonical_candidate_name
from app.modules.catalog.classifier import FoodClassificationService, FoodNameNormalizer
from app.modules.cooking.models import CookingPlan
from app.modules.inventory.models import CanonicalItem, InventoryLot, StockLedger
from app.modules.purchases.models import PurchaseItem

# Conservative OCR-homoglyph / spacing map for dry-run duplicate clustering.
_OCR_CANONICAL_EQUIVALENTS: dict[str, str] = {
    "chana bal": "chana dal",
    "chaner dal": "chana dal",
    "chana al": "chana dal",
    "chana đal": "chana dal",
    "akrot": "akhrot",
    "kallmalka": "kali malka",
    "kali malka": "kali malka",
    "lalmalka": "lal malka",
    "lal malka": "lal malka",
    "moong dhyli": "moong dhuli",
    "moong dhuli": "moong dhuli",
    "khulaatta": "khula atta",
    "khula atta": "khula atta",
    "chinldf": "chini df",
    "chinidf": "chini df",
    "chini df": "chini df",
    "40 chaial": "40 chawal",
    "40 chawal": "40 chawal",
    "matchhox": "matchbox",
}


@dataclass
class AffectedRecordCounts:
    inventory_lots: int = 0
    remaining_qty: float = 0.0
    purchase_items: int = 0
    stock_ledger: int = 0
    cooking_plans: int = 0


@dataclass
class DuplicateMergeProposal:
    duplicate_canonical_id: str
    duplicate_name: str
    survivor_canonical_id: str
    survivor_name: str
    suggested_survivor_rename: str | None
    evidence: str
    duplicate_kind: str
    confidence: float
    affected_records: AffectedRecordCounts


@dataclass
class ManualReviewCandidate:
    canonical_id: str
    name: str
    reason: str
    affected_records: AffectedRecordCounts


@dataclass
class DuplicateAuditReport:
    household_id: str
    total_active_canonical_items: int
    duplicate_clusters_count: int
    proposals: list[DuplicateMergeProposal] = field(default_factory=list)
    manual_review_items: list[ManualReviewCandidate] = field(default_factory=list)


def _cluster_key_for_item(name: str) -> tuple[str, str]:
    """Return (cluster_key, normalized_display_suggestion) for an active CanonicalItem name."""
    cleaned = clean_canonical_candidate_name(name)
    norm = FoodNameNormalizer.normalize(cleaned).replace("đ", "d")
    norm = re.sub(r"\s+", " ", norm).strip()

    # Check exact OCR equivalents first (preserving e.g. '40 chawal' vs 'lal chawal')
    if norm in _OCR_CANONICAL_EQUIVALENTS:
        canon = _OCR_CANONICAL_EQUIVALENTS[norm]
        return canon, canon.title()

    # Preserve distinct rice grades & legume variants explicitly
    if norm in {"40 chawal", "lal chawal", "chana kala", "chana dal", "chole"}:
        return norm, norm.title()

    cls = FoodClassificationService.classify_item(cleaned)
    if (
        cls.matched_canonical_name
        and cls.confidence >= 0.85
        and cls.matching_method in {"exact_canonical", "exact_alias", "normalized_token"}
    ):
        matched_norm = FoodNameNormalizer.normalize(cls.matched_canonical_name)
        if matched_norm:
            return matched_norm, cls.matched_canonical_name

    return norm or name.strip().lower(), cleaned or name.strip()


def _cleanliness_score(name: str, counts: AffectedRecordCounts) -> tuple[int, int, int]:
    """Higher tuple = better survivor candidate."""
    stripped = name.strip()
    has_noise_prefix = bool(
        re.match(r"^[\"*•°\-–\d]", stripped)
    ) and not stripped.lower().startswith("40 chawal")
    has_rate_suffix = bool(re.search(r"\b\d{3,}\b", stripped))
    clean_bonus = (
        2 if (not has_noise_prefix and not has_rate_suffix) else (1 if not has_noise_prefix else 0)
    )
    refs = counts.purchase_items + counts.inventory_lots + counts.stock_ledger
    return (clean_bonus, refs, -len(stripped))


def _classify_duplicate_kind(dup_name: str, survivor_name: str) -> tuple[str, str, float]:
    if re.search(r"\b\d{3,}\b", dup_name) or re.search(r"\b(?:kg|g)\s+\d+\b", dup_name, re.I):
        return (
            "rate_contamination",
            f"Rate/price suffix in '{dup_name}' matches '{survivor_name}'",
            0.96,
        )
    if re.search(r"(?:=====|qty\s*:)", dup_name, re.I) or re.match(r"^[\"*•°\-–\d]", dup_name):
        return (
            "serial_or_bleed",
            f"Serial/bullet or section/summary bleed in '{dup_name}' matches '{survivor_name}'",
            0.96,
        )
    return (
        "ocr_noise",
        f"OCR spelling/spacing variation '{dup_name}' matches '{survivor_name}'",
        0.92,
    )


async def run_dry_run_duplicate_audit(
    db: AsyncSession,
    *,
    household_id: UUID,
) -> DuplicateAuditReport:
    """Compute a strictly read-only proposed merge map for active CanonicalItems."""
    items = (
        (
            await db.execute(
                select(CanonicalItem).where(
                    CanonicalItem.household_id == household_id,
                    CanonicalItem.is_active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )

    lot_rows = (
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
    lot_map = {row[0]: (int(row[1]), float(row[2] or 0)) for row in lot_rows}

    pi_rows = (
        await db.execute(
            select(PurchaseItem.canonical_item_id, func.count(PurchaseItem.id))
            .where(PurchaseItem.canonical_item_id.is_not(None))
            .group_by(PurchaseItem.canonical_item_id)
        )
    ).all()
    pi_map = {row[0]: int(row[1]) for row in pi_rows}

    sl_rows = (
        await db.execute(
            select(StockLedger.canonical_item_id, func.count(StockLedger.id))
            .where(StockLedger.household_id == household_id)
            .group_by(StockLedger.canonical_item_id)
        )
    ).all()
    sl_map = {row[0]: int(row[1]) for row in sl_rows}

    cooking_plans = (
        (
            await db.execute(
                select(CookingPlan.ingredients).where(CookingPlan.household_id == household_id)
            )
        )
        .scalars()
        .all()
    )
    cp_counts: dict[str, int] = defaultdict(int)
    for ing_list in cooking_plans:
        if isinstance(ing_list, list):
            for ing in ing_list:
                if isinstance(ing, dict) and ing.get("canonical_item_id"):
                    cp_counts[str(ing["canonical_item_id"])] += 1

    clusters: dict[str, list[tuple[CanonicalItem, str, AffectedRecordCounts]]] = defaultdict(list)
    manual_review: list[ManualReviewCandidate] = []

    for item in items:
        lots, rem_qty = lot_map.get(item.id, (0, 0.0))
        counts = AffectedRecordCounts(
            inventory_lots=lots,
            remaining_qty=rem_qty,
            purchase_items=pi_map.get(item.id, 0),
            stock_ledger=sl_map.get(item.id, 0),
            cooking_plans=cp_counts.get(str(item.id), 0),
        )
        if len(item.name.strip()) > 65:
            manual_review.append(
                ManualReviewCandidate(
                    canonical_id=str(item.id),
                    name=item.name,
                    reason="Long/corrupted multi-row OCR description; requires manual review.",
                    affected_records=counts,
                )
            )
            continue
        key, suggested_clean = _cluster_key_for_item(item.name)
        clusters[key].append((item, suggested_clean, counts))

    proposals: list[DuplicateMergeProposal] = []
    dup_cluster_count = 0

    for _key, members in sorted(clusters.items(), key=lambda kv: kv[0]):
        if len(members) < 2:
            continue
        dup_cluster_count += 1
        members_sorted = sorted(
            members,
            key=lambda m: _cleanliness_score(m[0].name, m[2]),
            reverse=True,
        )
        survivor_item, survivor_suggested, _ = members_sorted[0]
        rename_needed = (
            survivor_suggested
            if survivor_item.name.strip() != survivor_suggested
            and re.match(r"^[\"*•°\-–\d]", survivor_item.name.strip())
            else None
        )
        for dup_item, _dup_sug, dup_counts in members_sorted[1:]:
            kind, evidence, conf = _classify_duplicate_kind(dup_item.name, survivor_item.name)
            proposals.append(
                DuplicateMergeProposal(
                    duplicate_canonical_id=str(dup_item.id),
                    duplicate_name=dup_item.name,
                    survivor_canonical_id=str(survivor_item.id),
                    survivor_name=survivor_item.name,
                    suggested_survivor_rename=rename_needed,
                    evidence=evidence,
                    duplicate_kind=kind,
                    confidence=conf,
                    affected_records=dup_counts,
                )
            )

    return DuplicateAuditReport(
        household_id=str(household_id),
        total_active_canonical_items=len(items),
        duplicate_clusters_count=dup_cluster_count,
        proposals=proposals,
        manual_review_items=manual_review,
    )
